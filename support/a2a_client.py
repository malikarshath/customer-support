"""A minimal A2A client for the Judge and the Masker (`tasks/send`, JSON-RPC 2.0 over HTTP).

Every failure mode raises GuardError (unreachable, timeout, HTTP error, JSON-RPC error, missing or malformed
result). The pipeline turns a GuardError into an `error` event. There is no code path here that returns a
default verdict (P-4, R-4).
"""
from __future__ import annotations

import uuid

import httpx
from opentelemetry import propagate


class GuardError(Exception):
    """A guard could not give a trustworthy answer. The turn must fail loudly."""

    def __init__(self, step: str, message: str):
        super().__init__(message)
        self.step = step
        self.message = message


async def tasks_send(step: str, label: str, base_url: str, text: str, timeout: float = 20.0) -> dict:
    """Send one text message with `tasks/send`; return the first `data` part of the completed task."""
    headers: dict[str, str] = {}
    propagate.inject(headers)  # W3C traceparent, so a traced service can join the turn's trace
    payload = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "tasks/send",
        "params": {"id": str(uuid.uuid4()), "message": {"role": "user", "parts": [{"type": "text", "text": text}]}},
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.post(base_url, json=payload, headers=headers)
    except httpx.ConnectError as e:
        raise GuardError(step, f"{label} unreachable: connection refused ({e})") from e
    except httpx.TimeoutException as e:
        raise GuardError(step, f"{label} timed out after {timeout:.0f}s") from e
    except httpx.HTTPError as e:
        raise GuardError(step, f"{label} request failed: {type(e).__name__}: {e}") from e

    try:
        body = r.json()
    except ValueError as e:
        raise GuardError(step, f"{label} returned non-JSON (HTTP {r.status_code})") from e
    if "error" in body:
        raise GuardError(step, f"{label} error: {body['error'].get('message', body['error'])}")
    try:
        task = body["result"]
        if task["status"]["state"] != "completed":
            raise GuardError(step, f"{label} task not completed: {task['status']['state']}")
        for part in task["status"]["message"]["parts"]:
            if part.get("type") == "data":
                return part["data"]
    except GuardError:
        raise
    except (KeyError, TypeError) as e:
        raise GuardError(step, f"{label} returned a malformed task: missing {e}") from e
    raise GuardError(step, f"{label} returned no data part")
