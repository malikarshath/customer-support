"""Dependency health (SPEC 7.2 /health, C-4 CLI preflight, EVALS gate 1).

Every value is "ok" or the dependency's error string (free strings, never an enum). A check that cannot tell
reports an error: there is no "unknown means fine".
"""
from __future__ import annotations

import asyncio
import os
import socket

import httpx
from dotenv import load_dotenv

from support import config
from support.agent import MODEL, TOOLBOX_URL

load_dotenv()

DEPS = ("db", "toolbox", "judge", "masker", "mem0", "phoenix")


async def _http_ok(url: str, label: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=4) as c:
            r = await c.get(url)
        return "ok" if r.status_code == 200 else f"{label} returned HTTP {r.status_code}"
    except Exception as e:
        return f"{label} unreachable: {type(e).__name__}: {e}".rstrip(": ")


async def check_db() -> str:
    """The database is reached through the Toolbox only (M-2). Here: is Postgres accepting connections at all?"""
    host, port = os.getenv("DB_HOST", "127.0.0.1"), int(os.getenv("DB_PORT", "5432"))

    def _connect():
        with socket.create_connection((host, port), timeout=3):
            return "ok"

    try:
        return await asyncio.to_thread(_connect)
    except Exception as e:
        return f"postgres not accepting connections on {host}:{port}: {e}"


async def check_toolbox() -> str:
    """Toolbox up AND able to run a query end to end (check-login with a dummy credential returns no row, which
    still proves toolbox -> postgres works)."""
    base = await _http_ok(TOOLBOX_URL + "/", "toolbox")
    if base != "ok":
        return base
    try:
        from toolbox_core import ToolboxClient

        async with ToolboxClient(TOOLBOX_URL) as c:
            tools = {t.__name__: t for t in await c.load_toolset("app_auth")}
            await tools["check-login"](email="health@check.invalid", password="x")
        return "ok"
    except Exception as e:
        return f"toolbox query failed: {type(e).__name__}: {e}"


async def check_mem0() -> str:
    from support import memory

    try:
        await asyncio.to_thread(lambda: memory.client().search("health", filters={"user_id": "health-check"}, top_k=1))
        return "ok"
    except Exception as e:
        return f"mem0 failed: {type(e).__name__}: {e}"[:200]


async def check_all() -> dict:
    keys = {
        "db": check_db(),
        "toolbox": check_toolbox(),
        "judge": _http_ok(config.JUDGE_URL + ".well-known/agent.json", "judge"),
        "masker": _http_ok(config.MASKER_URL + ".well-known/agent.json", "masker"),
        "mem0": check_mem0(),
        "phoenix": _http_ok(f"http://127.0.0.1:{os.getenv('PHOENIX_PORT', '6006')}/healthz", "phoenix"),
    }
    vals = await asyncio.gather(*keys.values())
    out = dict(zip(keys, vals, strict=True))
    out["status"] = "ok" if all(v == "ok" for v in vals) else "degraded"
    out["model"] = MODEL
    return {"status": out["status"], "model": out["model"], **{k: out[k] for k in DEPS}}


def down(h: dict) -> list[str]:
    return [k for k in DEPS if h.get(k) != "ok"]
