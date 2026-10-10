"""The Sanitizer (SPEC 5.4): in-process, no model call, runs before the Judge.

S-1: a length limit, a character allow-list, and the cheapest obvious patterns.
S-2: apostrophes, #, $, :, ;, @ and ordinary punctuation are ALLOWED. The legitimate set contains all of
them ("What's the status...", "order #2", "over $100", "after 5pm:", "alice@example.com", "Dec; hold my...").

So this guard looks for attack SHAPES (a statement, a tag, a template, a traversal), never for single
characters or single words. "drop the gift wrap", "select the fastest option" and "delete my old address" must
pass; the Judge (a model plus a bigger pattern tool) reads everything that is subtler than a shape.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

MAX_CHARS = 1000  # a support message is a sentence or two; this is generous, and bounds what reaches every model

# Unicode categories we refuse: control (except \n \t \r), format (zero-width / bidi tricks), surrogates, private use.
_BAD_CATEGORIES = {"Cc", "Cf", "Cs", "Co"}
_ALLOWED_CONTROL = {"\n", "\t", "\r"}


@dataclass(frozen=True)
class Verdict:
    allow: bool
    reason: str


def _p(pattern: str) -> re.Pattern:
    return re.compile(pattern, re.IGNORECASE | re.DOTALL)


# (name, pattern). Each is a structural shape, anchored on something no support message contains.
PATTERNS: list[tuple[str, re.Pattern]] = [
    # --- SQL injection shapes
    ("stacked SQL statement", _p(r";\s*(drop|truncate|alter)\s+(table|database)\b")),
    ("stacked SQL statement", _p(r";\s*delete\s+from\b")),
    ("stacked SQL statement", _p(r";\s*update\s+\w+\s+set\b")),
    ("stacked SQL statement", _p(r";\s*insert\s+into\b")),
    ("SQL UNION SELECT", _p(r"\bunion\s+(all\s+)?select\b")),
    ("SQL SELECT * FROM", _p(r"\bselect\s+(\*|[\w.,\s]+?)\s+from\s+\w+")),
    ("SQL tautology", _p(r"\b(or|and)\s+\d+\s*=\s*\d+\b")),
    ("SQL quote tautology", _p(r"'\s*(or|and)\s*'[^']*'\s*=\s*'")),
    ("SQL comment after a quote", _p(r"'\s*(--|#|/\*)")),
    # --- markup / script injection
    ("HTML tag injection", _p(r"<\s*/?\s*(script|iframe|img|svg|object|embed|link|style|body|form|meta)\b")),
    ("inline event handler", _p(r"\bon(error|load|click|mouseover|focus)\s*=")),
    ("javascript: URL", _p(r"\bjavascript\s*:")),
    # --- template injection
    ("template expression", _p(r"\{\{.*?\}\}|\{%.*?%\}|\$\{.*?\}")),
    # --- shell and path traversal
    ("shell substitution", _p(r"\$\(|`[^`]*`")),
    ("shell command after separator", _p(r"[;|&]\s*(cat|ls|rm|curl|wget|bash|sh|nc|chmod|sudo)\b\s*\S")),
    ("path traversal", _p(r"\.\.[/\\]")),
    ("sensitive system path", _p(r"/etc/(passwd|shadow|hosts)\b|/proc/self")),
]


def check(message: str) -> Verdict:
    """Pure function of the message. Cheap by construction: no I/O, no model."""
    if len(message) > MAX_CHARS:
        return Verdict(False, f"message too long ({len(message)} > {MAX_CHARS} characters)")

    for ch in message:
        if ch in _ALLOWED_CONTROL:
            continue
        cat = unicodedata.category(ch)
        if cat in _BAD_CATEGORIES:
            return Verdict(False, f"disallowed character U+{ord(ch):04X} ({cat})")

    for name, pat in PATTERNS:
        if pat.search(message):
            return Verdict(False, f"matched attack pattern: {name}")

    return Verdict(True, "allow: within length, characters allowed, no attack shape")
