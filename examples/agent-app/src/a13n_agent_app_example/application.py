"""Shared Agent Harness build path and the basic application layer."""

from __future__ import annotations

from collections.abc import Sequence

from a13n_harness import AgentContext, ExecutableAgent, HarnessBuilder, HarnessRunResult, RunBindings
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.models import Model


def build_agent(
    *,
    model: Model | str,
    model_id: str = "logical:agent-app",
    instructions: str = "Respond clearly and directly to the user's request.",
    capabilities: Sequence[AbstractCapability[AgentContext]] = (),
) -> ExecutableAgent[str]:
    """Build one reusable Agent with an explicit, deterministic composition."""

    return HarnessBuilder(configured_plugins_enabled=False).build_code(
        AgentSpec(model=model_id, instructions=instructions),
        output_type=str,
        model=model,
        capabilities=capabilities,
    )


async def run_basic_agent(
    prompt: str,
    *,
    model: Model | str,
) -> HarnessRunResult[str]:
    """Own the executable lifecycle and run one prompt with fresh local bindings."""

    executable = build_agent(model=model)
    async with executable:
        return await executable.run(
            prompt,
            bindings=RunBindings.local(),
        )
