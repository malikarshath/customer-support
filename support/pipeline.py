"""The one pipeline (SPEC section 6). The CLI and the web UI both consume `Pipeline.run_turn`; neither holds logic.

`run_turn` is an async generator of the SPEC 7.1 events. Ordering rules (7.3) are enforced here:
trace first; exactly one terminal event (final or error) last; every tool_call gets one tool_result;
the run log (EVALS 4.1) is written before the terminal event is emitted (P-5).

Steps are added to this module story by story (sanitize, judge, guardrail, recall, mask, save); the
fixed order is P-2: sanitize -> judge -> guardrail -> recall -> agent -> mask -> save.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from toolbox_core import ToolboxClient

from support import config, tracing
from support.guards import sanitizer
from support.agent import APP_NAME, MODEL, TOOLBOX_URL, build_agent, check_login, load_agent_tools
from support.runlog import write_run_log
from support.toolinfo import tool_info

CAP_REPLY = (
    "I couldn't finish that within my limits for a single request ({why}). "
    "Please try a simpler question or break it into smaller steps."
)


BLOCK_REPLY = "Sorry, I can't help with that request. I can help with your orders, deliveries, returns and account."


def new_turn_id() -> str:
    return "turn_" + uuid.uuid4().hex[:20]


def _ms(t0: float) -> int:
    return int((time.perf_counter() - t0) * 1000)


def _decode_arg(v: Any) -> Any:
    """Args that arrive as JSON strings (action-log's `parameters`) are decoded before emitting (7.1)."""
    if isinstance(v, str) and v.strip()[:1] in "{[":
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def _unwrap_result(resp: Any) -> Any:
    """The Toolbox wraps output as {"result": "<JSON string>"}; unwrap and decode it (7.1)."""
    if isinstance(resp, dict) and set(resp) == {"result"}:
        resp = resp["result"]
    if isinstance(resp, str):
        try:
            return json.loads(resp)
        except ValueError:
            return resp
    return resp


@dataclass
class UserSession:
    email: str
    full_name: str
    is_premium: bool
    runner: Runner
    session_id: str


@dataclass
class TurnState:
    """Everything the run log needs, filled in as the turn goes."""
    turn_id: str
    user: str
    message: str
    t0: float = field(default_factory=time.perf_counter)
    trace_id: str = ""
    steps: list[dict] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)
    llm_calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    terminated: str = "error"
    blocked_at: str | None = None

    def log(self) -> dict:
        return {
            "turn_id": self.turn_id,
            "trace_id": self.trace_id,
            "user": self.user,
            "message": self.message,
            "terminated": self.terminated,
            "blocked_at": self.blocked_at,
            "wall_clock_ms": _ms(self.t0),
            "tokens": {"in": self.tokens_in, "out": self.tokens_out},
            "steps": self.steps,
            "tool_calls": self.tool_calls,
            "llm_calls": self.llm_calls,
        }


class Pipeline:
    def __init__(self) -> None:
        self._client: ToolboxClient | None = None
        self._sessions = InMemorySessionService()
        self._users: dict[str, UserSession] = {}

    async def start(self) -> "Pipeline":
        tracing.setup()
        self._client = ToolboxClient(TOOLBOX_URL)
        await self._client.__aenter__()
        return self

    async def close(self) -> None:
        if self._client:
            await self._client.__aexit__(None, None, None)

    async def login(self, email: str, password: str) -> dict | None:
        user = await check_login(self._client, email, password)
        if not user:
            return None
        tools = await load_agent_tools(self._client, user["email"])  # email bound here (M-5)
        agent = build_agent(tools)
        runner = Runner(app_name=APP_NAME, agent=agent, session_service=self._sessions)
        session = await self._sessions.create_session(app_name=APP_NAME, user_id=user["email"])
        self._users[user["email"]] = UserSession(
            user["email"], user["full_name"], bool(user["is_premium_customer"]), runner, session.id
        )
        return {"user_id": user["email"], "full_name": user["full_name"], "is_premium": bool(user["is_premium_customer"])}

    def logout(self, email: str) -> None:
        self._users.pop(email, None)

    def is_logged_in(self, email: str) -> bool:
        return email in self._users

    # ------------------------------------------------------------------ the turn

    async def run_turn(self, email: str, message: str) -> AsyncIterator[dict]:
        us = self._users[email]
        st = TurnState(turn_id=new_turn_id(), user=email, message=message)
        span, ctx = tracing.start_turn_span(email, message)  # one root span per turn (O-2)
        st.trace_id = tracing.trace_id_hex(span)
        span.set_attribute("turn.id", st.turn_id)
        ended = False

        def end_span(output: str, err: str | None = None) -> None:
            nonlocal ended
            if ended:
                return
            ended = True
            span.set_attribute("output.value", output)
            span.set_attribute("turn.terminated", st.terminated)
            if err:
                tracing.fail(span, err)
            span.end()

        try:
            yield {"type": "trace", "turn_id": st.turn_id, "trace_id": st.trace_id, "url": tracing.trace_url(st.trace_id)}

            # ---- sanitize (S-1/S-2): cheap, in-process, no model call
            yield {"type": "stage", "key": "sanitize", "label": "Input Sanitizer"}
            t_s = time.perf_counter()
            with tracing.step_span(ctx, "security.sanitize", "GUARDRAIL") as sp:
                verdict = sanitizer.check(message)
                sp.set_attribute("guard.verdict", "allow" if verdict.allow else "block")
                sp.set_attribute("guard.reason", verdict.reason)
            ms = _ms(t_s)
            st.steps.append({"key": "sanitize", "status": "passed" if verdict.allow else "blocked", "ms": ms})
            yield {"type": "step", "key": "sanitize", "status": "passed" if verdict.allow else "blocked",
                   "detail": verdict.reason, "ms": ms, "span": "security.sanitize", "kind": "Python fn"}
            if not verdict.allow:
                yield self._blocked_final(st, "sanitize", end_span)
                return

            final_text, cap_reason, error = "", None, None
            async for ev in self._run_agent(us, st, ctx):
                if ev["type"] == "_result":
                    final_text, cap_reason, error = ev["text"], ev["cap"], ev["error"]
                else:
                    yield ev

            if error:
                st.terminated = "error"
                write_run_log(st.log())
                end_span(error, error)
                yield {"type": "error", "step": "agent", "status": 502, "error": error, "terminated": "error"}
                return

            if cap_reason:
                st.terminated = "cap"
                final_text = CAP_REPLY.format(why=cap_reason)
            else:
                st.terminated = "done"
            write_run_log(st.log())  # P-5: before the terminal event
            end_span(final_text)
            yield {
                "type": "final", "blocked": False, "blocked_at": None, "response": final_text,
                "terminated": st.terminated, "wall_clock_ms": _ms(st.t0),
                "tokens": {"in": st.tokens_in, "out": st.tokens_out},
            }
        finally:
            end_span("", "turn abandoned before a terminal event")  # no-op if already ended

    def _blocked_final(self, st: TurnState, key: str, end_span) -> dict:
        """P-3: a blocking guard ends the turn at once. No later step runs, including save. Run log first (P-5)."""
        st.terminated, st.blocked_at = "blocked", key
        write_run_log(st.log())
        end_span(BLOCK_REPLY)
        return {"type": "final", "blocked": True, "blocked_at": key, "response": BLOCK_REPLY,
                "terminated": "blocked", "wall_clock_ms": _ms(st.t0), "tokens": {"in": st.tokens_in, "out": st.tokens_out}}

    async def _run_agent(self, us: UserSession, st: TurnState, ctx) -> AsyncIterator[dict]:
        """Run the ADK agent, translating its events into llm / tool_call / tool_result events, under the P-6 budgets."""
        yield {"type": "stage", "key": "agent", "label": "Support agent"}
        t_agent = time.perf_counter()
        msg = types.Content(role="user", parts=[types.Part(text=st.message)])
        gen = us.runner.run_async(user_id=us.email, session_id=us.session_id, new_message=msg)

        text, cap, error = "", None, None
        pending: dict[str, tuple[int, str, float]] = {}  # adk call id -> (our id, name, t_start)
        t_prev = time.perf_counter()
        deadline = st.t0 + config.T_BUD_WALL_S
        try:
            while True:
                remaining = deadline - time.perf_counter()
                if remaining <= 0:
                    cap = f"over the {config.T_BUD_WALL_S}s time limit"
                    break
                try:
                    # Same task throughout (no wait_for): ADK's own spans open and close around these steps.
                    async with asyncio.timeout(remaining):
                        ev = await tracing.in_context(ctx, gen.__anext__())
                except StopAsyncIteration:
                    break
                except TimeoutError:
                    cap = f"over the {config.T_BUD_WALL_S}s time limit"
                    break

                now = time.perf_counter()
                calls = ev.get_function_calls() or []
                responses = ev.get_function_responses() or []

                if ev.author == us_agent_name(us) and (ev.usage_metadata or calls or ev.is_final_response()) and not responses:
                    u = ev.usage_metadata
                    tin = int(getattr(u, "prompt_token_count", 0) or 0)
                    tout = int(getattr(u, "candidates_token_count", 0) or 0) + int(getattr(u, "thoughts_token_count", 0) or 0)
                    st.tokens_in += tin
                    st.tokens_out += tout
                    st.llm_calls += 1
                    decision = f"call {calls[0].name}" if calls else "final answer"
                    yield {"type": "llm", "model": MODEL, "decision": decision, "tokens_in": tin,
                           "tokens_out": tout, "ms": int((now - t_prev) * 1000), "span": "call_llm"}

                for c in calls:
                    if len(st.tool_calls) + len(pending) >= config.T_BUD_TOOLS:
                        cap = f"over the {config.T_BUD_TOOLS}-tool-call limit"
                        break
                    n = len(st.tool_calls) + len(pending) + 1
                    pending[c.id] = (n, c.name, now)
                    yield {"type": "tool_call", "id": n, "name": c.name,
                           "args": {k: _decode_arg(v) for k, v in (c.args or {}).items()},
                           "info": tool_info(c.name), "span": f"execute_tool {c.name}"}
                if cap:
                    break

                for r in responses:
                    n, name, t_start = pending.pop(r.id, (len(st.tool_calls) + 1, r.name, now))
                    result = _unwrap_result(r.response)
                    err = r.response.get("error") if isinstance(r.response, dict) else None
                    ok = not err
                    entry = {"name": name, "ok": ok, "ms": int((now - t_start) * 1000)}
                    if not ok:
                        entry["error"] = str(err)
                    st.tool_calls.append(entry)
                    out = {"type": "tool_result", "id": n, "name": name, "ok": ok, "ms": entry["ms"], "result": result}
                    if not ok:
                        out["error"] = entry["error"]
                    yield out

                if ev.is_final_response() and ev.content and ev.content.parts:
                    text = "".join(p.text or "" for p in ev.content.parts if not getattr(p, "thought", False))

                if st.tokens_in + st.tokens_out > config.T_BUD_TOKENS:
                    cap = f"over the {config.T_BUD_TOKENS}-token limit"
                    break
                t_prev = time.perf_counter()
        except Exception as e:  # fail loud (P-4 spirit): an agent failure is an error turn, never an empty reply
            error = f"Support agent failed: {type(e).__name__}: {e}"
        finally:
            await gen.aclose()

        if not error and not cap and not text.strip():
            error = "Support agent returned no final answer"
        if not error:  # rule 2 (7.3): every stage is followed by its own step
            yield {"type": "step", "key": "agent", "status": "passed", "kind": "in-process",
                   "detail": f"{st.llm_calls} model call(s), {len(st.tool_calls)} tool call(s)" + (f"; stopped: {cap}" if cap else ""),
                   "ms": _ms(t_agent), "span": f"invoke_agent {us.runner.agent.name}"}
        yield {"type": "_result", "text": text, "cap": cap, "error": error}


def us_agent_name(us: UserSession) -> str:
    return us.runner.agent.name
