"""Interactive terminal adapter for the process-local Agent UI App."""

from __future__ import annotations

import asyncio

from a13n_ui.app import AgentUiApp


async def run(app: AgentUiApp) -> None:
    """Run the interactive terminal frontend against one started App."""

    print("Agent UI CLI. Commands: /status, /exit")
    while True:
        try:
            line = (await asyncio.to_thread(input, "a13n-ui> ")).strip()
        except EOFError:
            print()
            return
        if line in {"/exit", "/quit"}:
            return
        if line == "/status":
            status = await app.status()
            print(f"{status.state.value} pid={status.process_id} objects={status.object_count}")
        elif line:
            print("Use the headless run command for Session execution.")


__all__ = ["run"]
