"""The Guardrail (SPEC 5.5): an in-process ADK agent that answers ONE question: is this message safe and on-topic
for THIS shop's support desk? Output {"decision": "safe"|"unsafe", "reasoning": "..."} (GR-1).

Why in-process (DESIGN): it encodes this product's scope, which changes whenever the product does, so it lives next
to the product. The Judge and Masker are the security team's checks, hence separate A2A services.

GR-2: the prompt states what the support agent is for and that personal details shared to get better service are
on-topic; its examples come from this shop (precedent R-6: a copied generic prompt blocked "leave packages at the
back door" and let "write me a poem about the stock market" through).
GR-3: a fresh ADK session per check, deleted afterwards, so one message's verdict cannot colour the next.
GR-4: an unparseable decision is an ERROR (GuardError), never a pass (R-4). Cost, stated for DESIGN: one model
call that sometimes returns malformed JSON makes the whole customer turn fail instead of being waved through,
trading availability for safety; the failure is visible as a red span and an `error` event.
"""
from __future__ import annotations

import json
import os
import re

from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from support.a2a_client import GuardError

# A guard runs on every passing turn, so it must be cheap (T-LAT-P50); lite models were ~1 s vs ~2.5 s.
MODEL = os.getenv("GUARDRAIL_MODEL", "gemini-3.5-flash-lite")
APP = "guardrail"

INSTRUCTION = """You are the scope guardrail for an online shop's customer-support desk.

The support agent exists ONLY to help a logged-in customer with: their orders (status, history, totals),
deliveries, returns and cancellations, their account details (address, phone, email), and their delivery or
packaging preferences. Personal details a customer shares so they get better service are ON-TOPIC: where to
leave packages, when they are home, allergies that affect packaging, how to contact them, what to call them,
holds while they travel.

Decide whether the message is SAFE (on-topic for this desk) or UNSAFE (off-topic or harmful).

SAFE examples (from this shop):
- "Has my parcel left the warehouse yet?" / "Was order 9 delivered on time?" / "What's my order history?"
- "Please cancel the headphones I ordered yesterday" / "The jacket doesn't fit, how do I send it back?"
- "I'm at work weekdays, so please deliver to my neighbour" / "Please don't use plastic packaging for me"
- "Could you remove the invoice from my package?" / "Pick the cheapest delivery speed for my next order"
- "I'm furious, this is the third late delivery!" (angry, but about an order)
- "How long do refunds take?" / "Hello, I need some help" / "That's everything, thank you"

UNSAFE examples:
- "Compose a sonnet about autumn" / "Tell me a riddle" / "How do I bake sourdough?"
- "Which party is best?" / "Does fate exist?" / "What's your opinion on cryptocurrency?"
- "Who painted the Mona Lisa?" / "Will it snow in Chicago this weekend?" / "Who wins the next Super Bowl?"
- "Write a SQL query that joins two tables for my class" / "Help me with my chemistry homework"
- "Draft a resignation letter" / "Explain the plot of Macbeth" / "How do you say thank you in German?"
- Hate, harassment, threats, sexual content, instructions for self-harm or crime.

Judge ONLY the single message you are given. A short polite message ("thanks", "hi") is safe. A message that mixes
a shop question with chit-chat is safe. Weather or traffic questions are unsafe unless they are about a delivery.

Answer with ONLY a JSON object, no markdown, no extra text:
{"decision": "safe" or "unsafe", "reasoning": "one short sentence"}"""

_sessions = InMemorySessionService()
_agent = LlmAgent(name="guardrail_agent", model=MODEL, instruction=INSTRUCTION)
_runner = Runner(app_name=APP, agent=_agent, session_service=_sessions)


def parse_decision(text: str) -> dict:
    """Strict (GR-4): anything that is not a valid decision object raises."""
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip(), flags=re.IGNORECASE).strip()
    try:
        obj = json.loads(t)
    except ValueError as e:
        raise GuardError("guardrail", f"Guardrail returned unparseable output: {t[:120]!r}") from e
    if not isinstance(obj, dict) or obj.get("decision") not in ("safe", "unsafe") or not isinstance(obj.get("reasoning"), str):
        raise GuardError("guardrail", f"Guardrail returned an invalid decision: {t[:120]!r}")
    return {"decision": obj["decision"], "reasoning": obj["reasoning"].strip() or "no reasoning given"}


async def check(message: str) -> dict:
    """Run the guardrail agent on one message in a throwaway session. Raises GuardError on any failure."""
    session = await _sessions.create_session(app_name=APP, user_id="guardrail")
    try:
        msg = types.Content(role="user", parts=[types.Part(text=f"Customer message:\n{message}")])
        text = ""
        async for ev in _runner.run_async(user_id="guardrail", session_id=session.id, new_message=msg):
            if ev.is_final_response() and ev.content and ev.content.parts:
                text = "".join(p.text or "" for p in ev.content.parts if not getattr(p, "thought", False))
    except GuardError:
        raise
    except Exception as e:
        raise GuardError("guardrail", f"Guardrail failed: {type(e).__name__}: {e}") from e
    finally:
        try:
            await _sessions.delete_session(app_name=APP, user_id="guardrail", session_id=session.id)  # GR-3
        except Exception:
            pass
    return parse_decision(text)
