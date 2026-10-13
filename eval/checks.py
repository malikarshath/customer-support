"""Pure arithmetic and event checks for the eval runner. No I/O, no model: every gate is a function of data (law 2).

`python -m eval.checks` runs the self-test, which feeds known-good and known-bad streams to every checker so the
checkers themselves are shown to be able to fail.
"""
from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any

STAGE_ORDER = ["sanitize", "judge", "guardrail", "recall", "agent", "mask", "save"]
REQUIRED_SPANS = [
    "agent.turn", "security.sanitize", "security.a2a_judge", "guardrail.check", "memory.recall",
    "invoke_agent", "security.a2a_mask", "memory.save",
]
BLOCKERS = ("sanitize", "judge", "guardrail")


def norm(s: str) -> str:
    """EVALS 3: case-insensitive, with `$` and `,` removed, so `2,200.00` matches `2200`."""
    return str(s).lower().replace("$", "").replace(",", "")


def contains(text: str, needle: str) -> bool:
    return norm(needle) in norm(text)


def percentile(values: Iterable[float], p: float) -> float | None:
    """Nearest-rank percentile (the smallest value with at least p% of the data at or below it)."""
    xs = sorted(values)
    if not xs:
        return None
    k = max(1, math.ceil(p / 100 * len(xs)))
    return xs[k - 1]


def check_events(events: list[dict[str, Any]]) -> list[str]:
    """SPEC 7.3 rules 1-6 over one turn's event stream. Returns violations; empty means the stream obeys every rule."""
    v: list[str] = []
    if not events:
        return ["no events"]
    if events[0].get("type") != "trace":
        v.append("rule 1: the first event is not `trace`")

    terminals = [i for i, e in enumerate(events) if e.get("type") in ("final", "error")]
    if len(terminals) != 1:
        v.append(f"rule 5: {len(terminals)} terminal events (need exactly one)")
    elif terminals[0] != len(events) - 1:
        v.append("rule 5: the terminal event is not the last event")
    last = events[-1]

    open_stage: str | None = None
    last_idx = -1
    seen_steps: list[str] = []
    for e in events:
        t = e.get("type")
        if t == "stage":
            k = e.get("key")
            if open_stage is not None:
                v.append(f"rule 2: stage `{k}` began before `{open_stage}` produced its step")
            if k not in STAGE_ORDER:
                v.append(f"rule 2: unknown stage `{k}`")
            else:
                idx = STAGE_ORDER.index(k)
                if idx <= last_idx:
                    v.append(f"rule 2: stage `{k}` is out of P-2 order")
                last_idx = idx
            open_stage = k
        elif t == "step":
            if e.get("key") != open_stage:
                v.append(f"rule 2: step `{e.get('key')}` does not follow its own stage (open stage: {open_stage})")
            seen_steps.append(str(e.get("key")))
            open_stage = None
        elif t in ("llm", "tool_call", "tool_result") and open_stage != "agent":
            v.append(f"rule 2: `{t}` event outside the agent stage (open stage: {open_stage})")
    if open_stage is not None and last.get("type") != "error":
        v.append(f"rule 2: stage `{open_stage}` never produced a step")

    # rule 3
    calls = [e for e in events if e.get("type") == "tool_call"]
    results = [e for e in events if e.get("type") == "tool_result"]
    for c in calls:
        n = sum(1 for r in results if r.get("id") == c.get("id"))
        if n != 1:
            v.append(f"rule 3: tool_call {c.get('id')} has {n} results (need exactly one)")
    for r in results:
        if not any(c.get("id") == r.get("id") for c in calls):
            v.append(f"rule 3: tool_result {r.get('id')} has no tool_call")

    # rule 4
    for i, e in enumerate(events):
        if e.get("type") == "step" and e.get("status") == "blocked":
            nxt = events[i + 1] if i + 1 < len(events) else {}
            if not (nxt.get("type") == "final" and nxt.get("blocked") is True and nxt.get("blocked_at") == e.get("key")):
                v.append(f"rule 4: blocked step `{e.get('key')}` is not followed immediately by a matching blocked `final`")
            after = events[i + 1:]
            if any(a.get("type") in ("llm", "tool_call", "tool_result", "stage") for a in after):
                v.append("rule 4: events other than `final` follow a blocked step")
    if last.get("type") == "final" and last.get("blocked") and last.get("blocked_at") not in BLOCKERS:
        v.append(f"rule 4: blocked final names blocked_at={last.get('blocked_at')!r}")

    # rule 6
    if last.get("type") == "error" or (last.get("type") == "final" and last.get("blocked")):
        if "save" in seen_steps or any(e.get("type") == "stage" and e.get("key") == "save" for e in events):
            v.append("rule 6: `save` appears on a blocked or error turn")

    # a turn that answered must have walked every stage (P-2). A `cap` turn (P-6) stops inside the agent stage and sends
    # a fixed reply, so it is neither answered nor blocked: it has no mask or save to walk (SPEC 7.3 only orders stages).
    if last.get("type") == "final" and not last.get("blocked") and last.get("terminated", "done") == "done" \
            and seen_steps != STAGE_ORDER:
        v.append(f"rule 2: answered turn did not walk every stage: {seen_steps}")
    return v


def trace_one(spans: list[dict[str, Any]]) -> bool:
    """T-TRACE-ONE: exactly one root span, and it is `agent.turn`; no other span of that name."""
    roots = [s for s in spans if not s.get("parent_id")]
    turns = [s for s in spans if s.get("name") == "agent.turn"]
    return len(roots) == 1 and roots[0].get("name") == "agent.turn" and len(turns) == 1


def trace_shape_missing(spans: list[dict[str, Any]]) -> list[str]:
    """T-TRACE-SHAPE: required SPEC 10 spans missing from a passing turn's trace (`invoke_agent` matches by prefix)."""
    names = [s.get("name", "") for s in spans]
    missing = []
    for r in REQUIRED_SPANS:
        if r == "invoke_agent":
            ok = any(n.startswith("invoke_agent") and not n.endswith("guardrail_agent") for n in names)
        else:
            ok = r in names
        if not ok:
            missing.append(r)
    return missing


def trace_blocked_overrun(spans: list[dict[str, Any]], blocked_at: str) -> list[str]:
    """A blocked turn's spans stop at the blocking guard: nothing after it, and no support-agent span."""
    names = {s.get("name", "") for s in spans}
    later = {
        "sanitize": ["security.a2a_judge", "guardrail.check", "memory.recall", "security.a2a_mask", "memory.save"],
        "judge": ["guardrail.check", "memory.recall", "security.a2a_mask", "memory.save"],
        "guardrail": ["memory.recall", "security.a2a_mask", "memory.save"],
    }.get(blocked_at, [])
    bad = [n for n in later if n in names]
    bad += [n for n in names if n.startswith("invoke_agent") and not n.endswith("guardrail_agent")]
    return bad


# ---------------------------------------------------------------- self-test


def _good_done() -> list[dict]:
    ev: list[dict] = [{"type": "trace", "turn_id": "t", "trace_id": "x", "url": None}]
    for k in STAGE_ORDER:
        ev.append({"type": "stage", "key": k, "label": k})
        if k == "agent":
            ev += [{"type": "llm"}, {"type": "tool_call", "id": 1}, {"type": "tool_result", "id": 1}, {"type": "llm"}]
        ev.append({"type": "step", "key": k, "status": "passed"})
    ev.append({"type": "final", "blocked": False, "blocked_at": None, "response": "ok", "terminated": "done"})
    return ev


def selftest() -> int:
    good = _good_done()
    blocked = [good[0], {"type": "stage", "key": "sanitize"}, {"type": "step", "key": "sanitize", "status": "blocked"},
               {"type": "final", "blocked": True, "blocked_at": "sanitize", "terminated": "blocked"}]
    err = [good[0], {"type": "stage", "key": "judge"}, {"type": "error", "step": "judge", "terminated": "error"}]
    cases: list[tuple[str, list[dict], bool]] = [
        ("good answered turn", good, True),
        ("good blocked turn", blocked, True),
        ("good error turn", err, True),
        ("trace not first", good[1:], False),
        ("two terminals", good + [{"type": "error", "step": "x"}], False),
        ("stages out of order", [good[0], {"type": "stage", "key": "judge"}, {"type": "step", "key": "judge", "status": "passed"},
                                 {"type": "stage", "key": "sanitize"}, {"type": "step", "key": "sanitize", "status": "passed"},
                                 good[-1]], False),
        ("tool_call without result", [e for e in good if e.get("type") != "tool_result"], False),
        ("llm after a block", blocked[:3] + [{"type": "llm"}] + blocked[3:], False),
        ("blocked final names a different guard", blocked[:3] + [{"type": "final", "blocked": True, "blocked_at": "judge"}], False),
        ("save on a blocked turn", blocked[:3] + [{"type": "stage", "key": "save"}] + blocked[3:], False),
        ("answered without walking every stage", good[:3] + [good[-1]], False),
    ]
    bad = 0
    for name, ev, want_ok in cases:
        got = check_events(ev)
        ok = (not got) == want_ok
        bad += 0 if ok else 1
        print(f"{'ok  ' if ok else 'FAIL'} {name}: {'no violations' if not got else '; '.join(got)[:110]}")

    assert percentile([5, 1, 3, 2, 4], 50) == 3 and percentile([1, 2, 3, 4], 95) == 4 and percentile([], 95) is None
    assert contains("It cost $2,200.00 total", "2200") and contains("SHIPPED", "shipped") and not contains("a", "b")
    spans = [{"name": n, "parent_id": None if n == "agent.turn" else "p"} for n in
             ["agent.turn", "security.sanitize", "security.a2a_judge", "guardrail.check", "invoke_agent guardrail_agent",
              "memory.recall", "invoke_agent support_agent", "security.a2a_mask", "memory.save"]]
    assert trace_one(spans) and not trace_shape_missing(spans)
    assert not trace_one(spans + [{"name": "invoke_agent support_agent", "parent_id": None}])
    assert trace_shape_missing([s for s in spans if s["name"] != "memory.save"]) == ["memory.save"]
    assert trace_shape_missing([s for s in spans if s["name"] != "invoke_agent support_agent"]) == ["invoke_agent"]
    assert trace_blocked_overrun(spans[:2], "sanitize") == [] and trace_blocked_overrun(spans, "judge") != []
    print("ok   helpers: percentile, contains, trace_one, trace_shape_missing, trace_blocked_overrun")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(selftest())
