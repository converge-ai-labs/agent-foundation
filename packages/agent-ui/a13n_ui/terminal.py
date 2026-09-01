"""Terminal frontend for the Agent UI Host."""

from __future__ import annotations

import asyncio
import json
from typing import Literal

from a13n_ui.host import AgentUiHost

OutputFormat = Literal["text", "json"]


async def run(host: AgentUiHost) -> None:
    """Run the interactive terminal frontend against one started Host."""

    print("Agent UI CLI. Commands: /runtime, /restart, /exit")
    while True:
        try:
            line = (await asyncio.to_thread(input, "a13n-ui> ")).strip()
        except EOFError:
            print()
            return
        if line in {"/exit", "/quit"}:
            return
        if line == "/runtime":
            status = await host.runtime_status()
            print_runtime_status(status.model_dump(mode="json"), output="text")
        elif line == "/restart":
            result = await host.restart_runtime()
            print(f"active {result.active.generation_id} ({result.active.state.value})")
        elif line:
            print("Session interaction is not available in this runtime-management slice.")


def print_runtime_status(value: dict[str, object], *, output: OutputFormat) -> None:
    """Render one detached runtime status for terminal output."""

    if output == "json":
        print(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
        return
    active = value.get("active_generation_id")
    generations = value.get("generations")
    print(f"active {active or 'unavailable'}")
    if isinstance(generations, list):
        for item in generations:
            if isinstance(item, dict):
                generation_id = item.get("generation_id", "unknown")
                state = item.get("state", "unknown")
                process_id = item.get("process_id", "-")
                print(f"  {generation_id} {state} pid={process_id}")


__all__ = ["OutputFormat", "print_runtime_status", "run"]
