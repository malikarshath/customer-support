"""The Data Masker: a separate A2A service (SPEC 5.6, K-1..K-3).

Run:  python -m support.services.masker          (port MASKER_PORT, default 10003)

Same wire as the Judge: JSON-RPC `tasks/send`, agent card at /.well-known/agent.json. The request carries the
agent's final text (text part) and who the reply is for (data part {"own_email": ...}).

DESIGN decision: mask only OTHER people's data. The customer's own email stays visible, so "Your order ships to
123 Market St" is not destroyed. Per K-2 it changes only PII: emails other than the user's own, phone numbers, and
card-like numbers. Everything else (case, whitespace, order numbers, prices, dates) is byte-for-byte untouched
(precedent R-8: a Masker that lowercased every reply).

This is a pure function, no model: masking must be exact and cheap, and a model rewriting text is exactly how
R-8 happened. K-3: it reports what it masked, and "nothing to mask" only after having looked.
"""
from __future__ import annotations

import os
import re
import time
import uuid

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

load_dotenv()

PORT = int(os.getenv("MASKER_PORT", "10003"))

EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")
# 13-19 digits, optionally grouped by single spaces or hyphens: a payment card number shape.
CARD = re.compile(r"(?<![\d\-])(?:\d[ \-]?){12,18}\d(?![\d\-])")
# 10-digit NANP style (with optional +1 / parentheses), or the 7-digit local form 555-0100.
PHONE = re.compile(
    r"(?<![\w\-])(?:\+\d{1,3}[ .\-]?)?(?:\(\d{3}\)\s?|\d{3}[ .\-])\d{3}[ .\-]\d{4}(?![\w\-])"
    r"|(?<![\w\-.])\d{3}[ .\-]\d{4}(?![\w\-])"
)


def mask(text: str, own_email: str | None) -> dict:
    own = (own_email or "").strip().lower()
    counts = {"email": 0, "phone": 0, "card": 0}

    def sub_email(m: re.Match) -> str:
        if m.group(0).lower() == own:
            return m.group(0)
        counts["email"] += 1
        return "[REDACTED_EMAIL]"

    def sub_card(m: re.Match) -> str:
        digits = re.sub(r"\D", "", m.group(0))
        if not 13 <= len(digits) <= 19:
            return m.group(0)
        counts["card"] += 1
        return "[REDACTED_CARD]"

    def sub_phone(m: re.Match) -> str:
        counts["phone"] += 1
        return "[REDACTED_PHONE]"

    # Order matters: emails first (their digits must not read as phones), then cards, then phones.
    out = EMAIL.sub(sub_email, text)
    out = CARD.sub(sub_card, out)
    out = PHONE.sub(sub_phone, out)

    parts = []
    for key, noun in (("email", "email address"), ("phone", "phone number"), ("card", "card number")):
        n = counts[key]
        if n:
            parts.append(f"{n} {noun}" + ("es" if noun.endswith("ss") and n > 1 else "s" if n > 1 else ""))
    detail = ("masked " + ", ".join(parts)) if parts else "nothing to mask"
    return {"masked_text": out, "count": sum(counts.values()), "by_type": counts, "detail": detail}


CARD_JSON = {
    "name": "Data Masker",
    "description": "Masks other people's PII (emails, phone numbers, card numbers) in an agent reply; changes nothing else.",
    "url": f"http://127.0.0.1:{PORT}/",
    "version": "1.0.0",
    "capabilities": {"streaming": False, "pushNotifications": False},
    "defaultInputModes": ["text", "data"],
    "defaultOutputModes": ["data"],
    "skills": [{
        "id": "mask_reply", "name": "Mask PII in a reply",
        "description": "Returns {masked_text, count, by_type, detail}. The customer's own email is left visible.",
        "tags": ["security", "privacy"], "examples": ["Call Bob on 555-123-4567"],
    }],
    "methods": ["tasks/send"],
}

app = FastAPI(title="Data Masker (A2A)")


def _err(rid, code, message, status=200):
    return JSONResponse({"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}, status_code=status)


@app.get("/.well-known/agent.json")
async def card():
    return CARD_JSON


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/")
async def rpc(request: Request):
    try:
        body = await request.json()
    except ValueError:
        return _err(None, -32700, "Parse error", 400)
    rid = body.get("id") if isinstance(body, dict) else None
    if not isinstance(body, dict) or body.get("jsonrpc") != "2.0" or "method" not in body:
        return _err(rid, -32600, "Invalid Request", 400)
    if body["method"] != "tasks/send":
        return _err(rid, -32601, f"Method not found: {body['method']}")
    params = body.get("params") or {}
    try:
        parts = params["message"]["parts"]
        text = "".join(p.get("text", "") for p in parts if p.get("type", "text") == "text")
        own = next((p["data"].get("own_email") for p in parts if p.get("type") == "data" and isinstance(p.get("data"), dict)), None)
    except (KeyError, TypeError, AttributeError):
        return _err(rid, -32602, "Invalid params: expected params.message.parts[]")
    t0 = time.perf_counter()
    try:
        result = mask(text, own)
    except Exception as e:
        return _err(rid, -32000, f"Masker failed: {type(e).__name__}: {e}")
    result["ms"] = int((time.perf_counter() - t0) * 1000)
    part = {"type": "data", "data": result}
    return {"jsonrpc": "2.0", "id": rid, "result": {
        "id": params.get("id") or str(uuid.uuid4()), "sessionId": params.get("sessionId"),
        "status": {"state": "completed", "message": {"role": "agent", "parts": [part]}},
        "artifacts": [{"name": "masked", "parts": [part]}],
    }}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
