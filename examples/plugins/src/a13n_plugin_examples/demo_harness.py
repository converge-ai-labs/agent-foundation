"""Run packaged and explicit Harness plugins against an offline model."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, MutableSequence, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from a13n_harness import (
    AbstractHarnessPlugin,
    ExecutableAgent,
    HarnessBuilder,
    HarnessRunResultEvent,
    RunBindings,
)
from a13n_harness.plugin_configuration import HarnessBuildContext
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, FunctionModel

from a13n_plugin_examples.records import RunObservation

# The Harness-owned configuration document selects this installed metadata key.
PLUGIN_KEY = "example.run-recorder"
type HarnessSelectionMode = Literal["entrypoint", "code"]


@dataclass(frozen=True, slots=True)
class HarnessDemoResult:
    selection_mode: HarnessSelectionMode
    plugin_id: str
    run_id: str
    output: str
    observation: RunObservation


def _offline_model() -> FunctionModel:
    async def stream(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> AsyncIterator[str]:
        del messages, info
        yield "offline model response"

    return FunctionModel(stream_function=stream)


def _build_demo_agent(
    *,
    build_context: HarnessBuildContext,
    plugins: Sequence[AbstractHarnessPlugin] = (),
) -> ExecutableAgent[str]:
    return HarnessBuilder(build_context=build_context).build(
        AgentSpec(),
        output_type=str,
        model=_offline_model(),
        plugins=plugins,
    )


def build_configured_demo_agent() -> ExecutableAgent[str]:
    """Let Harness configuration select and create the installed plugin."""

    context = HarnessBuildContext.from_file(
        Path(__file__).with_name("harness-plugins.yaml"),
        extensions={"example": {"mode": "offline"}},
    )
    return _build_demo_agent(build_context=context)


def build_direct_demo_agent(
    observations: MutableSequence[RunObservation],
) -> ExecutableAgent[str]:
    """Construct a concrete plugin directly with a Python collaborator."""

    from a13n_plugin_examples.harness import RunRecorderPlugin

    return _build_demo_agent(
        build_context=HarnessBuildContext(),
        plugins=(
            RunRecorderPlugin(
                plugin_id="recorder-code",
                observation_sink=observations,
            ),
        ),
    )


async def _run_demo(
    *,
    selection_mode: HarnessSelectionMode,
    plugin_id: str,
    executable: ExecutableAgent[str],
) -> HarnessDemoResult:
    # Demo-only inspection uses the public bound-plugin lookup. A real recorder
    # sends observations to its own bounded sink and does not expose plugin
    # construction or lookup to the Host.
    from a13n_plugin_examples.harness import RunRecorderPlugin

    async with executable.stream(
        "Return the deterministic offline response.",
        bindings=RunBindings.embedded(),
    ) as stream:
        plugin = stream.context.plugins.require(plugin_id, RunRecorderPlugin)
        items = [item async for item in stream]
    terminal = items[-1]
    if not isinstance(terminal, HarnessRunResultEvent):
        raise RuntimeError("The Harness example did not produce a terminal result")
    result = terminal.result
    output = result.output_or_raise()
    observations = plugin.observations
    if len(observations) != 1:
        raise RuntimeError("The example plugin did not produce exactly one run observation")
    return HarnessDemoResult(
        selection_mode=selection_mode,
        plugin_id=plugin_id,
        run_id=result.run_id,
        output=output,
        observation=observations[0],
    )


async def run_harness_entrypoint_demo() -> HarnessDemoResult:
    """Let one explicit Build Context apply the installed package factory."""

    return await _run_demo(
        selection_mode="entrypoint",
        plugin_id="recorder-entrypoint",
        executable=build_configured_demo_agent(),
    )


async def run_harness_code_demo() -> HarnessDemoResult:
    """Compose the concrete plugin directly without scanning package metadata."""

    observations: list[RunObservation] = []
    executable = build_direct_demo_agent(observations)
    return await _run_demo(
        selection_mode="code",
        plugin_id="recorder-code",
        executable=executable,
    )


def _print_result(result: HarnessDemoResult) -> None:
    print(f"selection mode: {result.selection_mode}")
    print(f"plugin id: {result.plugin_id}")
    print(f"run id: {result.run_id}")
    print(f"output: {result.output}")
    print(f"observed status: {result.observation.status}")
    print(f"observed events: {result.observation.event_count}")


def main_entrypoint() -> None:
    """Run the package entry-point path without a model API key."""

    _print_result(asyncio.run(run_harness_entrypoint_demo()))


def main_code() -> None:
    """Run the explicit concrete-object path without a model API key."""

    _print_result(asyncio.run(run_harness_code_demo()))


if __name__ == "__main__":
    main_entrypoint()
