"""Minimal application-owned Agent Harness build and run path."""

from __future__ import annotations

from converge_agent_harness import ExecutableAgent, HarnessBuilder, HarnessRunResult, RunBindings
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.models import Model


def build_general_agent(*, model: Model | str) -> ExecutableAgent[str]:
    """Build one reusable Agent without optional Capabilities or Environment tools."""

    return HarnessBuilder(configured_plugins_enabled=False).build_code(
        AgentSpec(
            model="logical:general-agent",
            instructions="Respond clearly and directly to the user's request.",
        ),
        output_type=str,
        model=model,
    )


async def run_general_agent(
    prompt: str,
    *,
    model: Model | str,
) -> HarnessRunResult[str]:
    """Own the executable lifecycle and run one prompt with fresh local bindings."""

    executable = build_general_agent(model=model)
    async with executable:
        return await executable.run(
            prompt,
            bindings=RunBindings.local(),
        )
