"""Run-bound execution controls, model context, and composed tool surfaces."""

import asyncio
import json

import pytest
from a13n_harness import AgentSpec, CodeActStateOwner, HarnessBuilder, HarnessState, RunBindings
from a13n_harness.capabilities import (
    CodeActCapability,
    ProxyToolSearchConfig,
    ToolSearchPlan,
    ToolSearchSelection,
)
from a13n_harness.capabilities.tool_execution import ToolExecutionCapability
from a13n_harness.tool_execution import ToolExecutionConfig, ToolExecutionRuntime
from pydantic_ai.capabilities import Capability
from pydantic_ai.messages import ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

pytestmark = pytest.mark.anyio

CONTROLS = {f"tool_execution_{action}" for action in ("info", "result", "wait", "cancel")}


@pytest.mark.parametrize(
    "capability,bound,mode",
    [(False, True, "inline"), (True, False, "inline"), (True, True, "inline"), (True, True, "auto")],
)
async def test_execution_surface_and_summary_require_capability_and_runtime(capability, bound, mode):
    runtime = ToolExecutionRuntime()
    state, other = HarnessState.new(), HarnessState.new()

    async def operation():
        return "PRIVATE RESULT BODY"

    records = [
        runtime.admit(
            operation,
            thread_id=owner.thread_id,
            run_id="run-original",
            tool_call_id="call-original",
            tool_name="lookup",
        )
        for owner in (state, other)
    ]
    for owner, record in zip((state, other), records, strict=True):
        await runtime.wait(owner.thread_id, record.execution_id)
    visible = capability and bound
    requests = 0

    async def model(messages, info):
        nonlocal requests
        definitions = {tool.name: tool for tool in info.function_tools}
        assert CONTROLS.intersection(definitions) == (CONTROLS if visible else set())
        prompts = "\n".join(
            str(part.content) for message in messages for part in message.parts if isinstance(part, UserPromptPart)
        )
        assert ("Tool executions (status only" in prompts) is visible
        assert (records[0].execution_id in prompts) is visible
        assert records[1].execution_id not in prompts
        assert "PRIVATE RESULT BODY" not in prompts
        # Controls are ordinary native tools, not implicitly CodeAct-enabled.
        for runner in ("run_code", "run_program"):
            assert all(name not in definitions[runner].description for name in CONTROLS)
        requests += 1
        if requests == 1:
            yield {
                0: DeltaToolCall(
                    name="run_code",
                    json_args=json.dumps({"code": "await store(key='probe', value=42)\nawait load(key='probe')"}),
                    tool_call_id="code",
                )
            }
        elif requests == 2 and visible:
            yield {0: DeltaToolCall(name="tool_execution_info", json_args="{}", tool_call_id="inspect")}
        else:
            returns = [part for message in messages for part in message.parts if isinstance(part, ToolReturnPart)]
            code = next(part for part in returns if part.tool_name == "run_code")
            assert code.content == 42
            if visible:
                inspected = returns[-1].content
                assert inspected[0]["execution_id"] == records[0].execution_id
            yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(
            CodeActCapability(),
            *((ToolExecutionCapability(ToolExecutionConfig(mode=mode)),) if capability else ()),
        ),
    )
    try:
        result = await executable.run(
            "inspect",
            previous_state=state,
            bindings=RunBindings.embedded(tool_execution=runtime if bound else None, codeact_state=CodeActStateOwner()),
        )
        assert result.output_or_raise() == "done"
        assert not (await runtime.info(state.thread_id, records[0].execution_id)).delivery_acknowledged
        assert not (await runtime.info(other.thread_id, records[1].execution_id)).delivery_acknowledged
    finally:
        await runtime.close()


async def test_reused_executable_resolves_binding_independently_in_concurrent_runs():
    runtime = ToolExecutionRuntime()
    entered, release = asyncio.Event(), asyncio.Event()
    observations = []

    async def model(messages, info):
        prompts = [part.content for message in messages for part in message.parts if isinstance(part, UserPromptPart)]
        label = next(
            prompt for prompt in prompts if isinstance(prompt, str) and prompt.startswith(("bound-", "unbound-"))
        )
        visible = CONTROLS.intersection(tool.name for tool in info.function_tools)
        observations.append((label, visible))
        if label == "bound-overlap":
            entered.set()
            await release.wait()
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(ToolExecutionCapability(),),
    )
    try:
        assert (await executable.run("unbound-first")).output_or_raise() == "done"
        async with asyncio.timeout(10), asyncio.TaskGroup() as group:
            bound_run = group.create_task(
                executable.run("bound-overlap", bindings=RunBindings.embedded(tool_execution=runtime))
            )
            await entered.wait()
            assert (await executable.run("unbound-overlap")).output_or_raise() == "done"
            release.set()
        assert bound_run.result().output_or_raise() == "done"
        assert (
            await executable.run("bound-last", bindings=RunBindings.embedded(tool_execution=runtime))
        ).output_or_raise() == "done"
        assert (await executable.run("unbound-last")).output_or_raise() == "done"
        assert observations == [
            (label, CONTROLS if label.startswith("bound") else set())
            for label in ("unbound-first", "bound-overlap", "unbound-overlap", "bound-last", "unbound-last")
        ]
    finally:
        release.set()
        await runtime.close()


@pytest.mark.parametrize("bound", [False, True])
async def test_grouped_tool_search_cannot_discover_unbound_execution_controls(bound):
    runtime = ToolExecutionRuntime()
    capability = ToolExecutionCapability()

    def status() -> str:
        return "ready"

    ordinary = Capability(tools=[status])
    requests = 0

    async def model(messages, info):
        nonlocal requests
        requests += 1
        if requests == 1:
            yield {
                0: DeltaToolCall(
                    name="discover_tools",
                    json_args=json.dumps({"query": "tool_execution_info"}),
                    tool_call_id="search",
                )
            }
        else:
            returns = [part for message in messages for part in message.parts if isinstance(part, ToolReturnPart)]
            assert ("tool_execution_info" in str(returns[-1].content)) is bound
            yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(capability, ordinary),
        tool_search=ToolSearchPlan(
            groups={"execution": ToolSearchSelection("Execution controls", capabilities=(capability, ordinary))},
            config=ProxyToolSearchConfig(),
        ),
    )
    try:
        result = await executable.run(
            "discover", bindings=RunBindings.embedded(tool_execution=runtime if bound else None)
        )
        assert result.output_or_raise() == "done"
    finally:
        await runtime.close()
