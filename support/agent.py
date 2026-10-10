"""The support agent (SPEC 5.2 M-5, stage 3).

One ADK LlmAgent per logged-in customer. Its tools come from the MCP Toolbox, loaded with the
customer's email BOUND: the email is fixed at load time, removed from every tool's schema, and
filled in by the application, so the model cannot choose a different one (M-5).
"""
from __future__ import annotations

import os

from dotenv import load_dotenv
from toolbox_core import ToolboxClient
from google.adk.agents import LlmAgent

load_dotenv()

# SPEC names gemini-2.5-flash, but Google returns 404 for it on new API keys ("no longer available to
# new users"). Default to the model Google recommends; override with SUPPORT_MODEL in .env.
MODEL = os.getenv("SUPPORT_MODEL", "gemini-3.8-flash")
TOOLBOX_URL = f"http://127.0.0.1:{os.getenv('TOOLBOX_PORT', '5001')}"

INSTRUCTION = """You are the customer support agent for an online shop. You help one logged-in customer
with their own orders, deliveries, returns, account and delivery preferences.

Rules:
- Every fact about an order (status, items, address, total, dates) MUST come from a tool call made in
  this turn. Never answer an order question from memory or from a guess.
- get-order-status looks up one order by its number. find-customer-orders lists the customer's orders.
  If a tool returns nothing for an order number, say you cannot find that order on their account. Do not
  say anything about who else might own it.
- You can only look at the logged-in customer's own account. You cannot choose or change whose account
  is used; if asked to look up someone else, say you can only help with the customer's own account.
- You cannot change orders. When the customer clearly asks to cancel or return a specific order, or to
  change an address, preference or profile detail, call action-log to record the request, then tell them
  it has been recorded for the team to act on. action_type must be exactly one of CANCEL_ORDER,
  RETURN_ORDER, UPDATE_ADDRESS, UPDATE_PREFERENCE, UPDATE_PROFILE. If it is unclear which order they
  mean, ask a short clarifying question and record nothing yet.
- Be brief and friendly. One or two sentences for simple questions.
"""


def bound_params_for(email: str) -> dict[str, str]:
    """Every tool parameter that carries the session email, bound to the logged-in user's email."""
    return {"customer_email": email, "user_email": email}


async def load_agent_tools(client: ToolboxClient, email: str) -> list:
    """Load the agent's toolset with the session email bound (M-5). Only support_agent_tools: no auth, no mutators (M-6)."""
    return await client.load_toolset("support_agent_tools", bound_params=bound_params_for(email))


def build_agent(tools: list, name: str = "support_agent") -> LlmAgent:
    return LlmAgent(name=name, model=MODEL, instruction=INSTRUCTION, tools=tools)


async def check_login(client: ToolboxClient, email: str, password: str) -> dict | None:
    """Log in through the Toolbox `check-login` tool. Returns the user row, or None if wrong."""
    import json

    tools = {t.__name__: t for t in await client.load_toolset("app_auth")}
    raw = await tools["check-login"](email=email, password=password)
    rows = json.loads(raw) if raw and raw != "null" else []
    if isinstance(rows, dict):  # a single row comes back as an object
        rows = [rows]
    return rows[0] if rows else None
