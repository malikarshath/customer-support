"""The Judge's deterministic pattern-matching tool (SPEC J-2).

A superset of the Sanitizer's shapes plus the phrases the Sanitizer deliberately leaves alone because a bare
phrase is only meaningful as an *instruction to the system*: instruction override, role hijack, prompt
extraction, mass data requests, and attempts to name a privileged tool. Pure function, no I/O, no model.

Per DESIGN decision: if this tool matches, the Judge blocks at once without spending a model call (cheap
rejection, T-LAT-BLOCK-P95). If it does not match, the model reads the message (the subtler cases).
"""
from __future__ import annotations

import re

from support.guards.sanitizer import PATTERNS as SANITIZER_PATTERNS


def _p(pattern: str) -> re.Pattern:
    return re.compile(pattern, re.IGNORECASE | re.DOTALL)


JUDGE_ONLY_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("instruction override", _p(r"\b(ignore|disregard|forget|override)\b[^.\n]{0,25}\b(all |any |your |the )?(previous|prior|above|earlier|preceding)\b")),
    ("instruction override", _p(r"\b(forget|ignore|disregard)\s+(all\s+)?(your|the|these)\s+(rules|instructions|guidelines|restrictions)\b")),
    ("instruction override", _p(r"(^|\n)\s*#{1,6}\s*new\s+instructions\b|\bnew\s+instructions\s*:")),
    ("role hijack", _p(r"\byou\s+are\s+now\b|\bfrom\s+now\s+on\b[^.\n]{0,30}\banswer\s+as\b|\bpretend\s+(you\s+are|to\s+be|that|the)\b|\bact\s+as\s+(an?\s+)?(admin|administrator|root|dba|database)")),
    ("role hijack", _p(r"\bDAN\b|\bwithout\s+rules\b|\bjailbreak\b|\bdeveloper\s+mode\b")),
    ("forged authority", _p(r"(^|\n)\s*(system|assistant|developer)\s*:|\bthe\s+user\s+is\s+an?\s+admin(istrator)?\b|\bsecurity\s+judge\s+approved\b")),
    ("prompt extraction", _p(r"\b(system|initial|hidden)\s+prompt\b|\byour\s+(instructions|rules|configuration)\b|\brepeat\s+(everything|all|the\s+text)\s+(above|before)\b|\bverbatim\b|\bbetween\s+triple\s+backticks\b|\bwhat\s+were\s+you\s+told\b")),
    ("privileged tool named", _p(r"\bupdate-order-status\b|\bdelete-order\b|\bset-order\b")),
    # "all my orders" / "every order" are the customer's own business; mass requests name other people or the system.
    ("mass data request", _p(r"\b(all|every)\s+(the\s+)?(customers?|users?|accounts?|emails?|passwords?)\b|\b(all|every)\s+(the\s+)?orders?\s+(in|from)\s+the\s+(database|system|table)\b|\bfull\s+users?\s+table\b|\bdump\b[^.\n]{0,25}\b(table|orders|users|database|customer)")),
    ("credential request", _p(r"\bpasswords?\s+of\b|\blist\s+(the\s+)?passwords?\b|\bexport\b[^.\n]{0,30}\b(emails?|addresses|customers?|users?)\b")),
    ("privilege or price tampering", _p(r"\b(set|make|upgrade)\b[^.\n]{0,20}\b(my\s+)?account\b[^.\n]{0,15}\bpremium\b|\b100\s*%\s*discount\b|\bfree\s+of\s+charge\b[^.\n]{0,20}\border")),
]

ALL_PATTERNS = SANITIZER_PATTERNS + JUDGE_ONLY_PATTERNS


def scan_patterns(message: str) -> dict:
    """Scan a customer message for known attack signatures.

    Args:
        message: the raw customer message.

    Returns:
        {"matched": bool, "matches": [names of the patterns that fired]}
    """
    names = []
    for name, pat in ALL_PATTERNS:
        if pat.search(message) and name not in names:
            names.append(name)
    return {"matched": bool(names), "matches": names}
