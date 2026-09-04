"""Interactive terminal adapter for the process-local Agent UI App."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from a13n_ui.app import AgentUiApp
from a13n_ui.surfaces import NewThreadDefaults


@dataclass(frozen=True, slots=True)
class TuiLaunchOptions:
    """Terminal-local initial selection that never mutates file defaults."""

    thread_id: str | None = None
    defaults: NewThreadDefaults = field(default_factory=NewThreadDefaults)


async def run(app: AgentUiApp, *, launch: TuiLaunchOptions | None = None) -> None:
    """Run the interactive terminal frontend against one started App."""

    launch = launch or TuiLaunchOptions()
    print("Agent UI TUI. Commands: /status, /exit")
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
            selection = launch.defaults
            print(
                f"{status.state.value} objects={status.object_count} "
                f"thread={launch.thread_id or '-'} project={selection.project_id or '-'} "
                f"agent={selection.agent_id or '-'} "
                f"environment={selection.environment_profile_id or '-'}"
            )
        elif line:
            print("Use the headless run command for Session execution.")


__all__ = ["TuiLaunchOptions", "run"]
