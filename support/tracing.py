"""Phoenix tracing (SPEC 5.8, section 10).

Phoenix runs as its own long-lived process with persistent storage (O-1; started by run.sh). This module only
points the OpenTelemetry exporter at it, instruments Gemini calls (O-3), and gives the pipeline a helper for the
named step spans. ADK emits invoke_agent / call_llm / execute_tool spans on its own, as children of whatever
span is current when its runner is iterated, so the pipeline attaches the agent.turn context around each step
of the runner.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

import httpx
from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

PROJECT = "customer-support"
PHOENIX_PORT = os.getenv("PHOENIX_PORT", "6006")
PHOENIX_URL = f"http://localhost:{PHOENIX_PORT}"
_INIT = False


def setup() -> None:
    """Register the tracer provider and instrument Gemini. Idempotent."""
    global _INIT
    if _INIT:
        return
    from phoenix.otel import register
    from openinference.instrumentation.google_genai import GoogleGenAIInstrumentor

    provider = register(
        project_name=PROJECT,
        endpoint=f"http://127.0.0.1:{PHOENIX_PORT}/v1/traces",
        batch=False,  # export each span as it ends: a short-lived CLI must not lose its tail
        auto_instrument=False,
        set_global_tracer_provider=True,
        verbose=False,
    )
    GoogleGenAIInstrumentor().instrument(tracer_provider=provider)
    _INIT = True


def tracer() -> trace.Tracer:
    return trace.get_tracer("support")


_PID: str | None = None


def project_id() -> str | None:
    """Phoenix's id for our project, needed to build trace URLs. Cached only on success (the project
    does not exist until the first span is exported)."""
    global _PID
    if _PID:
        return _PID
    try:
        r = httpx.get(f"http://127.0.0.1:{PHOENIX_PORT}/v1/projects/{PROJECT}", timeout=3)
        if r.status_code == 200:
            _PID = r.json()["data"]["id"]
    except Exception:
        pass
    return _PID


def trace_url(trace_id: str) -> str | None:
    pid = project_id()
    return f"{PHOENIX_URL}/projects/{pid}/traces/{trace_id}" if pid else None


def start_turn_span(user: str, message: str):
    """Open the root agent.turn span (CHAIN). Returns (span, context)."""
    span = tracer().start_span(
        "agent.turn",
        attributes={"openinference.span.kind": "CHAIN", "input.value": message, "user.id": user},
    )
    return span, trace.set_span_in_context(span)


def trace_id_hex(span) -> str:
    return format(span.get_span_context().trace_id, "032x")


@contextmanager
def step_span(ctx, name: str, kind: str = "GUARDRAIL", **attrs) -> Iterator[trace.Span]:
    """A named child span of agent.turn (names and kinds are exact, SPEC section 10).

    Never `yield` an event from inside this `with` block: do the work, leave the block, then yield.
    On an exception the span is marked ERROR with the exception recorded, and the exception propagates.
    """
    with tracer().start_as_current_span(
        name, context=ctx, attributes={"openinference.span.kind": kind, **attrs}, record_exception=True, set_status_on_exception=True
    ) as sp:
        yield sp


def fail(span, exc: BaseException | str) -> None:
    span.set_status(Status(StatusCode.ERROR, str(exc)))
    if isinstance(exc, BaseException):
        span.record_exception(exc)


async def in_context(ctx, awaitable):
    """Await `awaitable` with `ctx` current, so spans ADK opens inside it parent under agent.turn."""
    token = otel_context.attach(ctx)
    try:
        return await awaitable
    finally:
        otel_context.detach(token)
