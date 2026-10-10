"""The web app (SPEC 5.10). FDE-14 provides /health; FDE-15 adds login, chat (NDJSON) and the UI."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from support import health

app = FastAPI(title="Customer Support")


@app.get("/health")
async def get_health():
    """200 with every dependency "ok"; 503 with the same shape and the failing dependency's value set to its error string."""
    h = await health.check_all()
    return JSONResponse(h, status_code=200 if h["status"] == "ok" else 503)
