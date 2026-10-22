from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest
from a13n_harness import AgentSpec, HarnessBuilder, RunBindings
from a13n_harness.tools.exposure import CONTROL_NAMES, ToolExposure, ToolsetExposure
from pydantic_ai import RunContext
from pydantic_ai.capabilities import Capability
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel
from pydantic_ai.toolsets import FunctionToolset

pytestmark = pytest.mark.anyio


async def run_calls(
    toolsets: list,
    calls: list[tuple[str, dict[str, Any]]],
    *,
    policy: ToolExposure,
    inspect: Callable[[int, AgentInfo], None] | None = None,
    capabilities: tuple = (),
    bindings: RunBindings | None = None,
    profile=None,
):
    requests = 0
    returns = []

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        nonlocal requests, returns
        if inspect:
            inspect(requests, info)
        returns = [p for m in messages if isinstance(m, ModelRequest) for p in m.parts if isinstance(p, ToolReturnPart)]
        index = requests
        requests += 1
        if index >= len(calls):
            yield "done"
            return
        name, args = calls[index]
        yield {0: DeltaToolCall(name=name, json_args=json.dumps(args), tool_call_id=f"call-{index}")}

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model, profile=profile),
        capabilities=(Capability(toolsets=toolsets), *capabilities),
        tool_exposure=policy,
    )
    result = await executable.run("test", bindings=bindings or RunBindings.embedded())
    return result, returns


async def test_mixed_exposure_browses_summaries_describes_and_executes_original_names() -> None:
    effects = []

    def double(value: int) -> int:
        """Double a customer value."""
        effects.append(value)
        return value * 2

    def ping() -> str:
        return "pong"

    def inspect(step: int, info: AgentInfo) -> None:
        assert {t.name for t in info.function_tools} == {*CONTROL_NAMES, "ping"}
        assert "Use exact customer IDs" not in (info.instructions or "")
        assert all("enum" not in json.dumps(t.parameters_json_schema) for t in info.function_tools)

    result, returns = await run_calls(
        [
            FunctionToolset([double], id="crm", instructions="Use exact customer IDs."),
            FunctionToolset([ping], id="health"),
        ],
        [
            ("list_toolsets", {}),
            ("list_tools", {"toolset": "crm"}),
            ("search_tools", {"query": "double"}),
            ("describe_tools", {"names": ["double"]}),
            ("execute_tool", {"name": "double", "arguments": {"value": "21"}}),
            ("execute_tool", {"name": "ping", "arguments": {}}),
        ],
        policy=ToolExposure(
            toolsets={"crm": ToolsetExposure(exposure="progressive", description="Customer operations")}
        ),
        inspect=inspect,
    )
    assert result.output_or_raise() == "done"
    assert effects == [21]
    assert returns[0].content["toolsets"] == [{"toolset": "crm", "description": "Customer operations", "count": 1}]
    for index in (1, 2):
        assert returns[index].content["tools"][0]["name"] == "double"
        assert "parameters_json_schema" not in returns[index].content["tools"][0]
    definition = returns[3].content["tools"][0]
    assert definition["parameters_json_schema"]["properties"]["value"]["type"] == "integer"
    assert definition["instructions"] == ["Use exact customer IDs."]
    assert returns[4].content == 42
    assert returns[5].content == "pong"


@pytest.mark.parametrize("kind", ["approval", "validator", "external", "function"])
async def test_invocation_deferral_projects_validated_target_and_resumes_once(kind: str) -> None:
    from a13n_harness import DeferredToolResume, HarnessState
    from a13n_harness.tools.exposure import project_tool_call
    from pydantic_ai import ApprovalRequired, CallDeferred, Tool, ToolDefinition
    from pydantic_ai.tools import DeferredToolResults
    from pydantic_ai.toolsets import ExternalToolset

    effects = []
    metadata = []

    def action(ctx: RunContext, value: int, label: str = "default") -> int:
        if kind == "function":
            raise CallDeferred(metadata={"reason": "client work"})
        effects.append((value, label))
        metadata.append(ctx.tool_call_metadata)
        return value * 2

    def validate(ctx: RunContext, **args):
        if kind == "validator" and not ctx.tool_call_approved:
            raise ApprovalRequired(metadata={"reason": "review"})

    tool = Tool(action, requires_approval=kind == "approval", args_validator=validate)
    source = FunctionToolset([tool], id="actions")
    if kind == "external":
        source = ExternalToolset(
            [ToolDefinition(name="action", parameters_json_schema=tool.function_schema.json_schema)], id="actions"
        )

    async def model(messages, info):
        if any(isinstance(m, ModelRequest) and any(isinstance(p, ToolReturnPart) for p in m.parts) for m in messages):
            yield "done"
        else:
            yield {
                0: DeltaToolCall(
                    name="execute_tool", json_args='{"name":"action","arguments":{"value":"21"}}', tool_call_id="outer"
                )
            }

    agent = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(Capability(toolsets=[source]),),
        tool_exposure=ToolExposure(
            toolsets={"actions": ToolsetExposure(exposure="progressive", description="Actions")}
        ),
    )
    first = await agent.run("go")
    assert first.status == "suspended", first
    assert first.deferred is not None and first.state is not None
    assert effects == []
    requests = first.deferred
    call = (requests.approvals or requests.calls)[0]
    assert call.tool_name == "execute_tool" and call.tool_call_id == "outer"
    projected = project_tool_call(call, requests.metadata["outer"])
    assert projected.tool_name == "action" and projected.tool_call_id == "outer"
    assert projected.args_as_dict() == ({"value": "21"} if kind == "external" else {"value": 21, "label": "default"})
    supplied = DeferredToolResults(
        approvals={"outer": True} if requests.approvals else {},
        calls={"outer": 42} if requests.calls else {},
        metadata={"outer": {"reviewer": "test"}},
    )
    resumed = await agent.run(
        previous_state=HarnessState.model_validate_json(first.state.model_dump_json()),
        deferred_resume=DeferredToolResume(requests, supplied),
    )
    assert resumed.output_or_raise() == "done"
    assert effects == ([(21, "default")] if requests.approvals else [])
    if requests.approvals:
        assert metadata == [{"reviewer": "test"}]
    returns = [
        p
        for m in resumed.all_messages()
        if isinstance(m, ModelRequest)
        for p in m.parts
        if isinstance(p, ToolReturnPart)
    ]
    assert returns[-1].content == 42
    assert returns[-1].tool_name == "execute_tool" and returns[-1].tool_call_id == "outer"


@pytest.mark.parametrize("eligible", [False, True])
async def test_codeact_uses_target_policy_without_expanding_directory(eligible: bool) -> None:
    from a13n_harness.capabilities import CodeActCapability
    from a13n_harness.toolsets import CodeActPolicyToolset, CodeActToolPolicy

    effects = []

    def double(value: int) -> int:
        effects.append(value)
        return value * 2

    def inspect(step, info):
        runner = next(t for t in info.function_tools if t.name == "run_code")
        assert "execute_tool" in runner.description, runner.description
        assert "async def double" not in runner.description

    result, returns = await run_calls(
        [CodeActPolicyToolset(FunctionToolset([double], id="crm"), CodeActToolPolicy(default=eligible))],
        [("run_code", {"code": "await execute_tool(name='double', arguments={'value': 21})"})],
        policy=ToolExposure(toolsets={"crm": ToolsetExposure(exposure="progressive", description="Customer tools")}),
        capabilities=(CodeActCapability(),),
        inspect=inspect,
    )
    assert result.output_or_raise() == "done"
    assert effects == ([21] if eligible else [])
    if eligible:
        assert returns[-1].content == 42
    else:
        assert returns[-1].outcome == "failed"
        assert json.loads(returns[-1].content)["codeact"]["tool_call_count"] == 0


@pytest.mark.parametrize("supported", [False, True])
async def test_native_exposure_uses_provider_search_or_fails(supported: bool) -> None:
    from pydantic_ai.profiles import ModelProfile
    from pydantic_ai.profiles.anthropic import anthropic_model_profile

    def lookup() -> str:
        return "found"

    seen = []

    def inspect(step, info):
        seen.append(info)
        parameters = info.model_request_parameters
        assert {t.name for t in parameters.function_tools} == {"lookup"}
        assert parameters.function_tools[0].defer_loading
        assert parameters.function_tools[0].with_native == "tool_search"
        assert any(t.kind == "tool_search" for t in parameters.native_tools)

    from contextlib import nullcontext

    from a13n_harness.errors import DefinitionError

    expectation = nullcontext() if supported else pytest.raises(DefinitionError, match="native tool search")
    with expectation:
        result, _ = await run_calls(
            [FunctionToolset([lookup], id="crm")],
            [],
            policy=ToolExposure(
                progressive_search="native",
                toolsets={"crm": ToolsetExposure(exposure="progressive", description="Customer tools")},
            ),
            profile=anthropic_model_profile("claude-sonnet-4-6")
            if supported
            else ModelProfile(supported_native_tools=frozenset()),
            inspect=inspect,
        )
    if supported:
        assert result.output_or_raise() == "done"
        assert seen
    else:
        assert not seen


async def test_exact_overrides_and_bounded_browse() -> None:
    from pydantic_ai import Tool

    def lookup(value: int = 1) -> int:
        return value

    source = FunctionToolset([Tool(lookup, name=f"lookup_{i:02}") for i in range(45)], id="crm")

    def inspect(step, info):
        assert {t.name for t in info.function_tools} == {*CONTROL_NAMES, "lookup_00"}

    result, returns = await run_calls(
        [source],
        [
            ("list_tools", {"toolset": "crm"}),
            ("list_tools", {"toolset": "crm", "offset": 20}),
            ("list_tools", {"toolset": "crm", "offset": 40}),
        ],
        policy=ToolExposure(
            toolsets={
                "crm": ToolsetExposure(
                    exposure="progressive", tool_exposure={"lookup_00": "direct"}, description="Customer tools"
                )
            }
        ),
        inspect=inspect,
    )
    assert result.output_or_raise() == "done"
    assert [len(r.content["tools"]) for r in returns] == [20, 20, 4]
    assert [r.content["next_offset"] for r in returns] == [20, 40, None]
    assert all(r.content["total"] == 44 for r in returns)


@pytest.mark.parametrize("strategy", [None, "bm25", "regex"])
async def test_native_anthropic_wire_uses_original_names_and_deferred_definitions(strategy) -> None:
    from dataclasses import replace

    from pydantic_ai.models.anthropic import AnthropicModel
    from pydantic_ai.providers.anthropic import AnthropicProvider

    adapter = AnthropicModel("claude-sonnet-4-6", provider=AnthropicProvider(api_key="test"))

    def lookup(value: int) -> int:
        return value

    def inspect(step, info):
        _, prepared = adapter.prepare_request(None, replace(info.model_request_parameters, tool_visibility=None))
        assert prepared.visibility_of("lookup") == "deferred"
        wire, _ = adapter._prepare_tools_and_tool_choice({}, prepared)
        wire, _, _ = adapter._add_native_tools(wire, prepared, {})
        target = next(t for t in wire if t.get("name") == "lookup")
        assert target["defer_loading"] is True
        assert target["input_schema"]["properties"]["value"]["type"] == "integer"
        search = next(t for t in wire if str(t.get("type", "")).startswith("tool_search_tool_"))
        assert (strategy or "bm25") in search["type"]
        assert not set(CONTROL_NAMES).intersection(t.get("name") for t in wire)

    result, _ = await run_calls(
        [FunctionToolset([lookup], id="crm")],
        [],
        inspect=inspect,
        profile=adapter.profile,
        policy=ToolExposure(
            progressive_search="native",
            strategy=strategy,
            toolsets={"crm": ToolsetExposure(exposure="progressive", description="Customer tools")},
        ),
    )
    assert result.output_or_raise() == "done"


async def test_invalid_target_arguments_retry_with_full_schema_without_effects() -> None:
    from pydantic_ai.messages import RetryPromptPart

    effects = []

    def double(value: int) -> int:
        effects.append(value)
        return value * 2

    result, _ = await run_calls(
        [FunctionToolset([double], id="crm")],
        [("execute_tool", {"name": "double", "arguments": {"value": "invalid"}})],
        policy=ToolExposure(toolsets={"crm": ToolsetExposure(exposure="progressive", description="Customers")}),
    )
    assert result.output_or_raise() == "done"
    assert effects == []
    retry = next(p for m in result.all_messages() for p in m.parts if isinstance(p, RetryPromptPart))
    assert retry.tool_call_id == "call-0" and retry.tool_name == "execute_tool"
    assert '"parameters_json_schema"' in retry.content
    assert '"type": "integer"' in retry.content


@pytest.mark.parametrize("target", ["execute_tool", "describe_tools", "missing"])
async def test_invocation_rejects_controls_and_unknown_names(target: str) -> None:
    def lookup() -> str:
        raise AssertionError("No target must execute")

    result, _ = await run_calls(
        [FunctionToolset([lookup], id="crm")],
        [("execute_tool", {"name": target, "arguments": {}})],
        policy=ToolExposure(toolsets={"crm": ToolsetExposure(exposure="progressive", description="Customers")}),
    )
    assert result.output_or_raise() == "done"


async def test_directory_delivered_once_and_only_changes_after_state_roundtrip() -> None:
    from a13n_harness import HarnessState
    from pydantic_ai import Tool
    from pydantic_ai.messages import TextContent, UserPromptPart

    def directory_count(messages):
        return sum(
            isinstance(item, TextContent) and "This directory replaces earlier tool directories." in item.content
            for m in messages
            for p in m.parts
            if isinstance(p, UserPromptPart) and isinstance(p.content, list)
            for item in p.content
        )

    def lookup() -> str:
        return "found"

    policy = ToolExposure(toolsets={"crm": ToolsetExposure(exposure="progressive", description="Customers")})
    history_counts = []

    async def model(messages, info):
        history_counts.append(directory_count(messages))
        yield "done"

    def build(count):
        return HarnessBuilder().build(
            AgentSpec(),
            output_type=str,
            model=FunctionModel(stream_function=model),
            capabilities=(
                Capability(
                    toolsets=[FunctionToolset([Tool(lookup, name=f"lookup_{i}") for i in range(count)], id="crm")]
                ),
            ),
            tool_exposure=policy,
        )

    result = await build(6).run("start")
    assert result.output_or_raise() == "done"
    state = HarnessState.model_validate_json(result.state.model_dump_json())
    result = await build(6).run("again", previous_state=state)
    assert result.output_or_raise() == "done"
    result = await build(7).run("changed", previous_state=result.state)
    assert result.output_or_raise() == "done"
    assert history_counts == [1, 1, 2]


@pytest.mark.parametrize("nested", [False, True])
async def test_handoff_restores_directory_and_called_schema_only(nested: bool) -> None:
    from a13n_harness.capabilities import CodeActCapability, HandoffCapability
    from a13n_harness.toolsets import CodeActPolicyToolset, CodeActToolPolicy
    from pydantic_ai.messages import ModelMessagesTypeAdapter

    effects = []

    def double(value: int) -> int:
        effects.append(value)
        return value * 2

    def triple(value: int) -> int:
        return value * 3

    calls = [
        ("describe_tools", {"names": ["triple"]}),
        ("run_code", {"code": "await execute_tool(name='double', arguments={'value': 21})"})
        if nested
        else ("execute_tool", {"name": "double", "arguments": {"value": 21}}),
        ("summarize", {"content": "Continue arithmetic"}),
    ]
    step = 0

    async def model(messages, info):
        nonlocal step
        if step == len(calls):
            text = ModelMessagesTypeAdapter.dump_json(messages).decode()
            assert "retained-tool-discovery" in text
            assert "This directory replaces earlier tool directories." in text
            restored = [
                p
                for m in messages
                for p in m.parts
                if isinstance(p, ToolReturnPart) and p.tool_name == "describe_tools"
            ]
            assert len(restored) == 1
            assert [t["name"] for t in restored[0].content["tools"]] == ["double"]
            assert restored[0].content["tools"][0]["parameters_json_schema"]["properties"]["value"]["type"] == "integer"
            runner = next(t for t in info.function_tools if t.name == "run_code")
            assert "async def double" not in runner.description
            assert "async def execute_tool" in runner.description
            yield "done"
            return
        name, args = calls[step]
        step += 1
        yield {0: DeltaToolCall(name=name, json_args=json.dumps(args), tool_call_id=f"step-{step}")}

    agent = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(
            Capability(
                toolsets=[
                    CodeActPolicyToolset(FunctionToolset([double, triple], id="math"), CodeActToolPolicy(default=True))
                ]
            ),
            CodeActCapability(),
            HandoffCapability(),
        ),
        tool_exposure=ToolExposure(
            toolsets={"math": ToolsetExposure(exposure="progressive", description="Arithmetic")}
        ),
    )
    result = await agent.run("calculate")
    assert result.output_or_raise() == "done"
    assert effects == [21]


@pytest.mark.parametrize("decision", ["approve", "edit", "deny"])
async def test_approval_preserves_target_presentation_and_argument_edits(decision) -> None:
    from a13n_harness import DeferredToolResume
    from a13n_harness.tools.approval import APPROVAL_PRESENTATION_KEY
    from pydantic_ai import Tool, ToolApproved, ToolDenied
    from pydantic_ai.tools import DeferredToolResults

    effects = []

    def write_file(path: str) -> str:
        effects.append(path)
        return path

    result, _ = await run_calls(
        [FunctionToolset([Tool(write_file, requires_approval=True)], id="files")],
        [("execute_tool", {"name": "write_file", "arguments": {"path": "original.txt"}})],
        policy=ToolExposure(toolsets={"files": ToolsetExposure(exposure="progressive", description="Files")}),
    )
    assert result.status == "suspended"
    presentation = result.deferred.metadata["call-0"][APPROVAL_PRESENTATION_KEY]
    assert presentation["tool_id"] == "tool/files/write_file"
    assert presentation["target"] == "path: original.txt"

    async def model(messages, info):
        yield "done"

    agent = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(Capability(toolsets=[FunctionToolset([Tool(write_file, requires_approval=True)], id="files")]),),
        tool_exposure=ToolExposure(toolsets={"files": ToolsetExposure(exposure="progressive", description="Files")}),
    )
    approval = (
        ToolApproved(override_args={"path": "edited.txt"})
        if decision == "edit"
        else ToolDenied("No")
        if decision == "deny"
        else True
    )
    resume = DeferredToolResume(result.deferred, DeferredToolResults(approvals={"call-0": approval}))
    result = await agent.run(previous_state=result.state, deferred_resume=resume)
    assert result.output_or_raise() == "done"
    assert effects == ([] if decision == "deny" else ["edited.txt" if decision == "edit" else "original.txt"])
    if decision == "edit":
        assert resume.results.approvals["call-0"].override_args == {"path": "edited.txt"}


@pytest.mark.parametrize("immediate", [False, True])
async def test_invocation_background_owns_target_with_outer_history_id(immediate):
    import asyncio

    from a13n_harness._tool_execution import TOOL_EXECUTION_METADATA_KEY
    from a13n_harness.capabilities.tool_execution import ToolExecutionCapability
    from a13n_harness.tool_execution import ToolExecutionConfig, ToolExecutionRuntime
    from pydantic_ai import Tool

    entered, finish = asyncio.Event(), asyncio.Event()
    effects = []
    returns = []

    async def lookup(value: int):
        effects.append(value)
        entered.set()
        await finish.wait()
        return value * 2

    async def model(messages, info):
        nonlocal returns
        returns = [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)]
        if not returns:
            yield {
                0: DeltaToolCall(
                    name="execute_tool", json_args='{"name":"lookup","arguments":{"value":21}}', tool_call_id="outer"
                )
            }
        else:
            yield "done"

    runtime = ToolExecutionRuntime()
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(
            Capability(
                toolsets=[FunctionToolset([Tool(lookup, metadata={TOOL_EXECUTION_METADATA_KEY: "owned"})], id="source")]
            ),
            ToolExecutionCapability(
                config=ToolExecutionConfig(mode="inline" if immediate else "auto", foreground_wait_seconds=0)
            ),
        ),
        tool_exposure=ToolExposure(
            toolsets={"source": ToolsetExposure(exposure="progressive", description="Lookup values")}
        ),
    )
    try:
        async with executable.stream("test", bindings=RunBindings.embedded(tool_execution=runtime)) as stream:

            async def consume():
                return [event async for event in stream]

            consumer = asyncio.create_task(consume())
            await asyncio.wait_for(entered.wait(), 5)
            if immediate:
                await stream.steer("continue", immediate=True)
            await asyncio.wait_for(consumer, 5)
            assert stream.result.status == "completed"
            thread_id = stream.thread_id
        assert effects == [21]
        assert len(returns) == 1
        assert returns[0].tool_name == "execute_tool"
        assert returns[0].tool_call_id == "outer"
        receipt = returns[0].content
        assert receipt["tool_name"] == "lookup"
        record = await runtime.info(thread_id, receipt["execution_id"])
        assert record.tool_call_id == "outer"
        assert record.tool_name == "lookup"
        finish.set()
        outcome = await runtime.wait(thread_id, record.execution_id)
        assert outcome.result == 42
        assert effects == [21]
    finally:
        finish.set()
        await runtime.close()


@pytest.mark.parametrize("replacement", ["removed", "local", "external"])
@pytest.mark.parametrize("successful", [False, True])
async def test_external_invocation_resume_checks_actual_target(replacement, successful):
    from a13n_harness import DeferredToolResume, DefinitionError
    from pydantic_ai import ToolDefinition, ToolFailed
    from pydantic_ai.tools import DeferredToolResults
    from pydantic_ai.toolsets import ExternalToolset

    policy = ToolExposure(toolsets={"client": ToolsetExposure(exposure="progressive", description="Client work")})
    source = ExternalToolset([ToolDefinition(name="action")], id="client")
    first, _ = await run_calls([source], [("execute_tool", {"name": "action", "arguments": {}})], policy=policy)
    assert first.deferred is not None and first.state is not None
    effects = []

    def action():
        effects.append(True)

    sources = (
        []
        if replacement == "removed"
        else [source if replacement == "external" else FunctionToolset([action], id="client")]
    )
    observed = []

    async def stream(messages, info):
        observed.extend(messages)
        yield "continued"

    agent = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(Capability(toolsets=sources),),
        tool_exposure=policy,
    )

    async def resume():
        return await agent.run(
            previous_state=first.state,
            deferred_resume=DeferredToolResume(
                first.deferred,
                DeferredToolResults(calls={"call-0": 42 if successful else ToolFailed("Client disconnected")}),
            ),
        )

    if replacement == "local" or (replacement == "removed" and successful):
        with pytest.raises(DefinitionError, match="current target"):
            await resume()
        assert not observed
    else:
        result = await resume()
        assert result.output_or_raise() == "continued"
        returns = [p for m in observed if isinstance(m, ModelRequest) for p in m.parts if isinstance(p, ToolReturnPart)]
        assert len(returns) == 1
        assert returns[0].tool_name == "execute_tool" and returns[0].tool_call_id == "call-0"
        assert returns[0].content == (42 if successful else "Client disconnected")
    assert effects == []


async def test_execute_tool_accepts_bound_execution_controls():
    from a13n_harness.capabilities.tool_execution import ToolExecutionCapability
    from a13n_harness.tool_execution import ToolExecutionRuntime

    def lookup():
        return 42

    runtime = ToolExecutionRuntime()
    try:
        result, returns = await run_calls(
            [FunctionToolset([lookup], id="source")],
            [("execute_tool", {"name": "tool_execution_info", "arguments": {}})],
            policy=ToolExposure(toolsets={"source": ToolsetExposure(exposure="progressive", description="Lookup")}),
            capabilities=(ToolExecutionCapability(),),
            bindings=RunBindings.embedded(tool_execution=runtime),
        )
        assert result.output_or_raise() == "done"
        assert returns[-1].content == []
    finally:
        await runtime.close()
