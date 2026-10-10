"""Memory as a pipeline step (SPEC 5.7), on Mem0.

R-1 recall is a step, not a tool: before the agent runs, search Mem0 with the user's message, filtered to that
user, requesting T-MEM-TOPK. (Precedent R-2: the prompt said "ALWAYS search memory" and the model skipped it.)
R-2 a memory is inserted only if score >= T-MEM-MINSCORE and len <= T-MEM-MAXCHARS; every other candidate is
reported as skipped with the reason (R-5 lists every candidate with score and inserted flag).
R-3 inserted memories go at the top of the agent's input under a fixed header.
R-4 save ONLY the user's message, never the agent's reply (precedent: Bob's memories held "Assistant states it
cannot set a specific delivery location", and the agent then trusted its own old refusal over his preference).
R-6 the agent has no memory tool.

A Mem0 failure is a GuardError naming the step (fail loud): the turn ends in an `error` event, it does not quietly
proceed with no memory.
"""
from __future__ import annotations

import asyncio
import os

from dotenv import load_dotenv

from support import config
from support.a2a_client import GuardError

load_dotenv()

HEADER = "Relevant memories about this customer (from Mem0):"

_client = None


def client():
    global _client
    if _client is None:
        from mem0 import MemoryClient

        key = os.getenv("MEM0_API_KEY")
        if not key:
            raise GuardError("recall", "MEM0_API_KEY is not set")
        _client = MemoryClient(api_key=key)
    return _client


def classify(results: list[dict]) -> list[dict]:
    """Apply R-2 to raw Mem0 results. Pure function (unit-testable): no network."""
    out = []
    for r in results:
        text = r.get("memory") or r.get("text") or ""
        score = float(r.get("score") or 0.0)
        if len(text) > config.T_MEM_MAXCHARS:
            inserted, reason = False, f"too long ({len(text)} > {config.T_MEM_MAXCHARS} chars)"
        elif score < config.T_MEM_MINSCORE:
            inserted, reason = False, f"below cutoff ({score:.2f} < {config.T_MEM_MINSCORE})"
        else:
            inserted, reason = True, None
        # Long memories are truncated in the EVENT only; they are never inserted into a prompt.
        shown = text if len(text) <= 200 else text[:200] + "..."
        out.append({"memory": shown, "score": round(score, 2), "inserted": inserted, "reason": reason, "_full": text})
    return out


def render_input(message: str, candidates: list[dict]) -> str:
    """R-3: the fixed header and inserted memories at the top of the agent's input; otherwise the message untouched."""
    chosen = [c["_full"] for c in candidates if c["inserted"]]
    if not chosen:
        return message
    lines = "\n".join(f"- {m}" for m in chosen)
    return f"{HEADER}\n{lines}\n\nCustomer message: {message}"


async def recall(email: str, message: str) -> list[dict]:
    def _search():
        return client().search(message, filters={"user_id": email}, top_k=config.T_MEM_TOPK)

    try:
        resp = await asyncio.to_thread(_search)
    except GuardError:
        raise
    except Exception as e:
        raise GuardError("recall", f"Memory recall failed: {type(e).__name__}: {e}") from e
    results = resp.get("results", []) if isinstance(resp, dict) else resp
    return classify(results or [])


async def save(email: str, message: str) -> dict:
    """R-4: the user's own words only."""
    def _add():
        return client().add([{"role": "user", "content": message}], user_id=email)

    try:
        resp = await asyncio.to_thread(_add)
    except GuardError:
        raise
    except Exception as e:
        raise GuardError("save", f"Memory save failed: {type(e).__name__}: {e}") from e
    return resp if isinstance(resp, dict) else {"raw": resp}


async def clear(email: str) -> None:
    """Delete every memory for one user (the eval clears a user before each memory pair)."""
    if not email or "*" in email:  # Mem0 treats user_id="*" as "everyone in the project": never allow it
        raise ValueError(f"refusing to clear memories for {email!r}")
    await asyncio.to_thread(lambda: client().delete_all(user_id=email))
