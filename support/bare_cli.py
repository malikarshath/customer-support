"""Throwaway stage 3 CLI: log in, ask questions, print the final text. Replaced by support/cli.py at stage 4.

Run: .venv/bin/python -m support.bare_cli
"""
from __future__ import annotations

import asyncio
import getpass
import sys

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from toolbox_core import ToolboxClient

from support.agent import TOOLBOX_URL, build_agent, check_login, load_agent_tools

APP = "support"


async def main(email: str | None = None) -> None:
    async with ToolboxClient(TOOLBOX_URL) as client:
        email = email or input("Email: ").strip()
        password = getpass.getpass("Password: ")
        user = await check_login(client, email, password)
        if not user:
            print("Invalid email or password.")
            return
        print(f"Logged in as {user['full_name']}.")

        tools = await load_agent_tools(client, user["email"])
        agent = build_agent(tools)
        sessions = InMemorySessionService()
        runner = Runner(app_name=APP, agent=agent, session_service=sessions)
        session = await sessions.create_session(app_name=APP, user_id=user["email"])

        while True:
            try:
                text = input("You: ").strip()
            except EOFError:
                break
            if text.lower() in {"quit", "exit", "q"}:
                break
            if not text:
                continue
            msg = types.Content(role="user", parts=[types.Part(text=text)])
            final = ""
            async for event in runner.run_async(user_id=user["email"], session_id=session.id, new_message=msg):
                if event.is_final_response() and event.content and event.content.parts:
                    final = "".join(p.text or "" for p in event.content.parts)
            print(f"Agent: {final}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else None))
