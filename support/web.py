"""The web app (SPEC 5.10, 7.2): login, chat (NDJSON, identical to `--events`), logout, /health and the UI at `/`."""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from support import health
from support.guards.sanitizer import MAX_CHARS
from support.pipeline import Pipeline

UI = Path(__file__).resolve().parent / "ui" / "index.html"


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pipe = await Pipeline().start()
    try:
        yield
    finally:
        await app.state.pipe.close()


app = FastAPI(title="Customer Support", lifespan=lifespan)


async def _body(request: Request) -> dict:
    try:
        b = await request.json()
    except Exception:
        return {}
    return b if isinstance(b, dict) else {}


@app.get("/health")
async def get_health():
    """200 with every dependency "ok"; 503 with the same shape and the failing dependency's value set to its error string."""
    h = await health.check_all()
    return JSONResponse(h, status_code=200 if h["status"] == "ok" else 503)


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(UI.read_text(encoding="utf-8"))


@app.post("/api/login")
async def login(request: Request):
    b = await _body(request)
    email, password = b.get("email"), b.get("password")
    user = None
    if isinstance(email, str) and isinstance(password, str) and email.strip():
        user = await request.app.state.pipe.login(email.strip(), password)
    if not user:
        return JSONResponse({"error": "Invalid email or password."}, status_code=401)
    return JSONResponse(user)


@app.post("/api/logout")
async def logout(request: Request):
    b = await _body(request)
    uid = b.get("user_id")
    if isinstance(uid, str):
        request.app.state.pipe.logout(uid)
    return JSONResponse({"ok": True})


@app.post("/api/chat")
async def chat(request: Request):
    pipe: Pipeline = request.app.state.pipe
    b = await _body(request)
    uid, message = b.get("user_id"), b.get("message")
    if not isinstance(message, str) or not message.strip():
        return JSONResponse({"error": "message is empty"}, status_code=400)
    if not isinstance(uid, str) or not pipe.is_logged_in(uid):
        return JSONResponse({"error": "Not logged in."}, status_code=401)
    if len(message) > MAX_CHARS:
        return JSONResponse({"error": "message too long"}, status_code=413)

    async def stream():
        async for ev in pipe.run_turn(uid, message):
            yield json.dumps(ev) + "\n"

    return StreamingResponse(stream(), media_type="application/x-ndjson")
