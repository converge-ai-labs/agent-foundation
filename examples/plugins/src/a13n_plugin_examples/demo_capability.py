"""Run declarative AgentSpec and explicit-code custom Capability composition."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal

from a13n_harness import (
    AgentSpec,
    CapabilityTypeCatalog,
    ExecutableAgent,
    HarnessBuilder,
    RunBindings,
)
from pydantic_ai.agent.spec import CapabilitySpec
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, FunctionModel

from a13n_plugin_examples.capability import (
    CAPABILITY_SERIALIZATION_NAME,
    ExampleInstructionsCapability,
)

type CapabilitySelectionMode = Literal["agent-spec", "code"]


@dataclass(frozen=True, slots=True)
class CapabilityDemoResult:
    selection_mode: CapabilitySelectionMode
    capability_name: str
    instructions: str
    model_instructions: str
    output: str


def _offline_model(observed_instructions: list[str | None]) -> FunctionModel:
    async def stream(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> AsyncIterator[str]:
        del messages
        observed_instructions.append(info.instructions)
        yield "offline capability response"

    return FunctionModel(stream_function=stream)


def _build_agent(
    *,
    selection_mode: CapabilitySelectionMode,
    instructions: str,
    observed_instructions: list[str | None],
) -> ExecutableAgent[str]:
    if selection_mode == "agent-spec":
        agent_spec = AgentSpec(
            capabilities=[
                CapabilitySpec(
                    name=CAPABILITY_SERIALIZATION_NAME,
                    arguments={"instructions": instructions},
                )
            ]
        )
        builder = HarnessBuilder(
            capability_type_catalog=CapabilityTypeCatalog.from_types(
                (ExampleInstructionsCapability,),
            ),
            configured_plugins_enabled=False,
        )
        definition_capabilities = ()
    else:
        agent_spec = AgentSpec()
        builder = HarnessBuilder(configured_plugins_enabled=False)
        definition_capabilities = (ExampleInstructionsCapability(instructions=instructions),)

    return builder.build(
        agent_spec,
        output_type=str,
        model=_offline_model(observed_instructions),
        capabilities=definition_capabilities,
    )


async def run_capability_demo(
    *,
    selection_mode: CapabilitySelectionMode,
) -> CapabilityDemoResult:
    """Select one custom Capability and prove its instruction reaches the Model."""

    instructions = f"Selection mode is {selection_mode}."
    observed_instructions: list[str | None] = []
    executable = _build_agent(
        selection_mode=selection_mode,
        instructions=instructions,
        observed_instructions=observed_instructions,
    )
    async with executable:
        result = await executable.run(
            "Report the selected Capability mode.",
            bindings=RunBindings.embedded(),
        )

    if len(observed_instructions) != 1 or observed_instructions[0] is None:
        raise RuntimeError("The custom Capability instruction did not reach the Model")
    model_instructions = observed_instructions[0]
    if instructions not in model_instructions:
        raise RuntimeError("The Model received unexpected Capability instructions")

    return CapabilityDemoResult(
        selection_mode=selection_mode,
        capability_name=CAPABILITY_SERIALIZATION_NAME,
        instructions=instructions,
        model_instructions=model_instructions,
        output=result.output_or_raise(),
    )


def _run_main(selection_mode: CapabilitySelectionMode) -> None:
    result = asyncio.run(run_capability_demo(selection_mode=selection_mode))
    print(f"selection mode: {result.selection_mode}")
    print(f"selected capability: {result.capability_name}")
    print(f"capability instructions: {result.instructions}")
    print(f"model received instructions: {result.model_instructions}")
    print(f"output: {result.output}")


def main_agent_spec() -> None:
    """Run the Host-authorized AgentSpec selection path."""

    _run_main("agent-spec")


def main_code() -> None:
    """Run the explicit concrete Capability path."""

    _run_main("code")


if __name__ == "__main__":
    main_agent_spec()
