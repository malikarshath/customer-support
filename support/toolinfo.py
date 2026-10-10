"""Static facts about each Toolbox tool, read from tools.yaml, for the `tool_call.info` field (SPEC 7.1)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

TOOLS_YAML = Path(__file__).resolve().parent.parent / "mcp_toolbox" / "tools.yaml"

# Parameters the application binds from the session. The model never sees or supplies them (M-5).
BOUND = {"customer_email", "user_email"}


@lru_cache(maxsize=1)
def _tools() -> dict:
    doc = yaml.safe_load(TOOLS_YAML.read_text())
    return {name: spec for name, spec in doc.get("tools", {}).items()}


def tool_info(name: str) -> dict:
    """{kind, access, statement, params, bound} for one tool. `params` is in `$n` order; `bound` marks the session-bound ones."""
    spec = _tools().get(name)
    if spec is None:
        return {"kind": "MCP", "access": "UNKNOWN", "statement": None, "params": [], "bound": []}
    statement = " ".join(str(spec.get("statement", "")).split())
    params = [p["name"] for p in spec.get("parameters", [])]
    access = "READ" if statement.upper().startswith("SELECT") else "WRITE"
    return {
        "kind": "MCP",
        "access": access,
        "statement": statement,
        "params": params,
        "bound": [p for p in params if p in BOUND],
    }
