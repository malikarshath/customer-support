"""The CLI (SPEC 5.9): a thin view over Pipeline.run_turn.

  python -m support.cli            readable, one line per step
  python -m support.cli --events   the raw NDJSON event stream on stdout and nothing else (C-3)

Prompts go to stderr so --events stdout stays pure NDJSON. When stdin is not a terminal (the eval
runner), email, password and messages are read as plain lines.
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import sys

from support.pipeline import Pipeline


def _ask(prompt: str, hidden: bool = False) -> str | None:
    if hidden and sys.stdin.isatty():
        return getpass.getpass(prompt, stream=sys.stderr)
    print(prompt, end="", file=sys.stderr, flush=True)
    line = sys.stdin.readline()
    return line.rstrip("\n") if line else None


def render(ev: dict) -> str | None:
    """One readable line per event (C-2)."""
    t = ev["type"]
    if t == "trace":
        return None
    if t == "stage":
        return None
    if t == "step":
        return f"[{ev['key']}] {ev['status']} · {ev.get('ms', 0):,} ms · {ev.get('span', '')}  {ev.get('detail', '')}".rstrip()
    if t == "llm":
        return f"[llm] {ev['decision']} · {ev['tokens_in']} in / {ev['tokens_out']} out · {ev['ms']:,} ms · {ev['span']}"
    if t == "tool_call":
        return f"[tool] {ev['name']}({json.dumps(ev['args'])}) · {ev['info']['access']} · bound: {', '.join(ev['info']['bound']) or '-'}"
    if t == "tool_result":
        return f"[tool] -> {'ok' if ev['ok'] else 'FAILED'} · {ev['ms']} ms · {json.dumps(ev['result'])[:300]}"
    if t == "final":
        return f"Agent: {ev['response']}\n[{ev['terminated']}] {ev['wall_clock_ms']:,} ms · {ev['tokens']['in']} in / {ev['tokens']['out']} out tokens"
    if t == "error":
        return f"ERROR [{ev['step']}] {ev['error']}"
    return None


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", action="store_true", help="print the raw event stream as NDJSON on stdout")
    ap.add_argument("--failing-demo", action="store_true",
                    help="mark this session's turns as the deliberate failing trajectory (logs go to runs/failing/)")
    args = ap.parse_args()

    pipe = await Pipeline().start()
    try:
        email = _ask("Email: ")
        password = _ask("Password: ", hidden=True)
        if not email or password is None:
            return 1
        user = await pipe.login(email.strip(), password)
        if not user:
            print("Invalid email or password.", file=sys.stderr)
            return 1
        print(f"Logged in as {user['full_name']}.", file=sys.stderr)

        while True:
            text = _ask("You: ")
            if text is None or text.strip().lower() in {"quit", "exit", "q"}:
                break
            if not text.strip():
                continue
            async for ev in pipe.run_turn(user["user_id"], text, failing=args.failing_demo):
                if args.events:
                    print(json.dumps(ev), flush=True)
                else:
                    line = render(ev)
                    if line:
                        print(line, flush=True)
                    if ev["type"] == "trace" and ev.get("url"):
                        print(f"[trace] {ev['url']}", flush=True)
    finally:
        await pipe.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
