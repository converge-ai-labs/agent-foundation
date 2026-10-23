from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
from a13n_harness import (
    AgentDefinition,
    AgentIdentityRef,
    AgentInstanceContext,
    HarnessBuilder,
    HarnessEvent,
    HarnessExtensionEvent,
    RunBindings,
    SubagentDefinition,
)
from a13n_harness.capabilities import (
    CodeActCapability,
    CodeActConfig,
    SubagentCapability,
)
from a13n_harness.codeact.runtime import CodeActRunState
from a13n_harness.environment import (
    EnvironmentAction,
    EnvironmentMount,
    EnvironmentPermissionSet,
)
from a13n_harness.environment.advanced import (
    EmptyEnvironmentRuntime,
    create_environment_runtime,
)
from a13n_harness.providers.environment.direct_local.configuration import (
    DirectLocalEnvironmentConfiguration,
    DirectLocalRootConfiguration,
)
from a13n_harness.tools import InvocationPolicyCapability, InvocationPolicyDecision
from a13n_harness.toolsets import (
    CodeActPolicyToolset,
    CodeActToolPolicy,
)
from pydantic_ai import Tool
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.capabilities import AbstractCapability, Capability, HandleDeferredToolCalls, ToolSearch
from pydantic_ai.exceptions import CallDeferred
from pydantic_ai.messages import ModelMessage, ModelRequest, RetryPromptPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel
from pydantic_ai.realtime import RealtimeModel
from pydantic_ai.tools import DeferredToolRequests, DeferredToolResults
from pydantic_ai.toolsets import FunctionToolset

from .environment_helpers import DirectLocalSource

pytestmark = pytest.mark.anyio


def _tool_returns(messages: list[ModelMessage], name: str) -> list[ToolReturnPart]:
    return [
        part
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart) and part.tool_name == name
    ]


def _codeact_tools(*tools: Any, allowed: tuple[str, ...]) -> Capability[Any]:
    return Capability(
        toolsets=[
            CodeActPolicyToolset(
                wrapped=FunctionToolset(list(tools), id="test-tools"),
                policy=CodeActToolPolicy(tools={name: True for name in allowed}),
                reject_unknown_tools=True,
            )
        ],
        id="codeact-test-tools",
    )


def test_codeact_policy_detaches_and_freezes_tool_decisions() -> None:
    source = {"allowed": True}
    policy = CodeActToolPolicy(tools=source)

    source["allowed"] = False

    assert policy.allows("allowed") is True
    with pytest.raises(TypeError):
        policy.tools["allowed"] = False  # type: ignore[index]


def _local_environment(root: Path):
    provider = DirectLocalSource(
        DirectLocalEnvironmentConfiguration(
            root=DirectLocalRootConfiguration(path=root),
        ),
        environment_id="codeact-test",
    )
    return create_environment_runtime(
        mounts={
            "local": EnvironmentMount(
                source=provider,
                permission_ceiling=EnvironmentPermissionSet(operations=frozenset(EnvironmentAction)),
                working_directory="/",
            )
        },
        default_mount="local",
    )


async def test_codeact_preserves_run_state_across_native_model_recovery() -> None:
    from a13n_harness import ModelRecoveryPolicy

    calls: list[int] = []
    requests = 0
    final_value = None

    def double(value: int) -> int:
        calls.append(value)
        return value * 2

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        nonlocal requests, final_value
        requests += 1
        assert "run_code" in {tool.name for tool in info.function_tools}
        if requests == 1:
            code = "saved = await double(value=21)\nsaved"
        elif requests == 2:
            yield "partial answer"
            raise ConnectionResetError("stream disconnected")
        elif requests == 3:
            code = "saved + 1"
        else:
            final_value = _tool_returns(messages, "run_code")[-1].content
            yield "recovered"
            return
        yield {0: DeltaToolCall(name="run_code", json_args=json.dumps({"code": code}), tool_call_id=f"code-{requests}")}

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(CodeActCapability(), _codeact_tools(double, allowed=("double",))),
        model_recovery=ModelRecoveryPolicy(
            enabled=True, max_attempts=2, backoff_initial_seconds=0, backoff_max_seconds=0
        ),
    )
    result = await executable.run("start", bindings=RunBindings.embedded())
    assert result.output_or_raise() == "recovered"
    assert requests == 4
    assert calls == [21]
    assert final_value == 43


async def test_run_code_dispatches_eligible_tools_and_owns_inline_state() -> None:
    calls: list[int] = []
    descriptions: list[str] = []
    observed_returns: list[Any] = []

    def double(value: int) -> int:
        """Double one integer."""
        calls.append(value)
        return value * 2

    def hidden() -> str:
        raise AssertionError("denied tool must not be callable from CodeAct")

    programs = (
        {"code": "saved = await double(value=4)\nsaved"},
        {"code": "saved + 1"},
        {"code": "saved = 3\nsaved", "restart": True},
        {"code": "saved + 1"},
    )

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        runner = next(tool for tool in info.function_tools if tool.name == "run_code")
        descriptions.append(runner.description or "")
        returns = _tool_returns(messages, "run_code")
        if returns:
            observed_returns[:] = [part.content for part in returns]
        if len(returns) < len(programs):
            yield {
                0: DeltaToolCall(
                    name="run_code",
                    json_args=json.dumps(programs[len(returns)]),
                    tool_call_id=f"code-{len(returns) + 1}",
                )
            }
        else:
            yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(
            _codeact_tools(double, hidden, allowed=("double",)),
            CodeActCapability(),
        ),
    )

    events: list[HarnessEvent] = []
    async with executable.stream("run", bindings=RunBindings.embedded()) as stream:
        async for item in stream:
            if isinstance(item, HarnessEvent):
                events.append(item)

    assert stream.result is not None
    assert stream.result.output_or_raise() == "done"
    assert calls == [4]
    assert observed_returns == [8, 9, 3, 4]
    assert all("double" in description and "hidden" not in description for description in descriptions)
    payload_types = [
        item.event.payload["type"]
        for item in events
        if isinstance(item.event, HarnessExtensionEvent) and item.event.kind == "diagnostic"
    ]
    assert payload_types.count("codeact_execution_started") == 4
    assert payload_types.count("codeact_execution_completed") == 4
    assert payload_types.count("codeact_tool_call_started") == 1
    assert payload_types.count("codeact_tool_call_completed") == 1


@pytest.mark.parametrize("gate", ["search", "capability"])
@pytest.mark.parametrize("runner", ["run_code", "run_program"])
async def test_codeact_deferred_tools_follow_discovery_and_loading(tmp_path: Path, gate: str, runner: str) -> None:
    effects: list[int] = []
    validated: list[int] = []
    requests = 0

    def double(value: int) -> int:
        effects.append(value)
        return value * 2

    def validate(ctx, value: int) -> None:
        assert ctx.tool_name == "double"
        validated.append(value)

    class ObserveDirectory(AbstractCapability):
        async def before_model_request(self, ctx, request_context):
            # Exercise the realtime instruction projection with the same prepared
            # manager and discovery state as the ordinary request.
            codeact = next(cap for cap in ctx.capabilities.values() if isinstance(cap, CodeActCapability))
            assert ctx.tools["double"].defer_loading  # Discovery never clears the authored flag.
            live = await codeact._live_instructions(replace(ctx, model=Mock(spec=RealtimeModel)))
            assert ("async def double" in live) is (requests >= 2)
            assert "async def load_capability" not in live
            return request_context

    source = Capability(
        id="arithmetic",
        description="Double an integer",
        defer_loading=gate == "capability",
        toolsets=[
            CodeActPolicyToolset(
                FunctionToolset([Tool(double, defer_loading=gate == "search", args_validator=validate)]),
                CodeActToolPolicy(default=True),
            )
        ],
    )
    program = tmp_path / "double.codeact.py"
    program.write_text("async def main(inputs):\n    return await double(value=inputs['value'])\n")

    def invocation(value: str) -> dict[str, Any]:
        if runner == "run_code":
            return {"code": f"await double(value={value!r})"}
        return {"path": "/workspace/double.codeact.py", "inputs": {"value": value}}

    discover = (
        ("search_tools", {"queries": ["double"]}) if gate == "search" else ("load_capability", {"id": "arithmetic"})
    )
    calls = [(runner, invocation("21")), discover, (runner, invocation("invalid")), (runner, invocation("21"))]

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        nonlocal requests
        definitions = {tool.name: tool for tool in info.function_tools}
        for name in ("run_code", "run_program"):
            assert ("async def double" in (definitions[name].description or "")) is (requests >= 2)
        index = requests
        requests += 1
        if index == len(calls):
            yield "done"
            return
        name, args = calls[index]
        yield {0: DeltaToolCall(name=name, json_args=json.dumps(args), tool_call_id=f"step-{index}")}

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(source, CodeActCapability(), ObserveDirectory(), ToolSearch(strategy="keywords")),
    )
    result = await executable.run("test", bindings=RunBindings.embedded(environment=_local_environment(tmp_path)))
    assert result.output_or_raise() == "done"
    returns = _tool_returns(result.all_messages(), runner)
    assert [part.outcome for part in returns] == ["failed", "failed", "success"]
    assert returns[-1].content == 42
    assert effects == [21]
    assert validated == [21]  # Invalid arguments never reach the custom validator or body.
    assert result.usage.tool_calls == 3  # Discovery, the successful nested tool, and its runner.


async def test_codeact_nested_deferral_uses_child_denial_and_root_handler() -> None:
    handler_calls: list[DeferredToolRequests] = []

    def deferred_action(value: int) -> int:
        raise CallDeferred({"value": value})

    async def handle_deferred(ctx: Any, requests: DeferredToolRequests) -> DeferredToolResults:
        del ctx
        handler_calls.append(requests)
        return DeferredToolResults(
            calls={request.tool_call_id: "handled by root" for request in requests.calls},
        )

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        responses = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, RetryPromptPart | ToolReturnPart) and part.tool_name == "run_code"
        ]
        if not responses:
            yield {
                0: DeltaToolCall(
                    name="run_code",
                    json_args=json.dumps({"code": "await deferred_action(value=7)"}),
                    tool_call_id="code-deferred",
                )
            }
        elif isinstance(responses[-1], RetryPromptPart) or responses[-1].outcome == "failed":
            yield "child recovered"
        else:
            yield str(responses[-1].content)

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(
            _codeact_tools(deferred_action, allowed=("deferred_action",)),
            CodeActCapability(),
            HandleDeferredToolCalls(handler=handle_deferred, id="root-deferred-handler"),
        ),
    )
    child_bindings = RunBindings(
        instance=AgentInstanceContext(
            identity=AgentIdentityRef(issuer="test", subject="child"),
            agent_instance_id="child-1",
            parent_agent_instance_id="parent-1",
            delegation_id="delegation-1",
        ),
        deferred_tools_supported=False,
        environment=EmptyEnvironmentRuntime(),
    )

    child_events: list[HarnessEvent] = []
    async with executable.stream("run", bindings=child_bindings) as child_stream:
        async for item in child_stream:
            if isinstance(item, HarnessEvent):
                child_events.append(item)

    assert child_stream.result is not None
    assert child_stream.result.output_or_raise() == "child recovered"
    assert handler_calls == []
    nested_results = [
        item.event.payload
        for item in child_events
        if isinstance(item.event, HarnessExtensionEvent)
        and item.event.kind == "diagnostic"
        and item.event.payload["type"] == "codeact_tool_call_completed"
    ]
    assert len(nested_results) == 1
    assert nested_results[0]["outcome"] == "deferred"
    assert nested_results[0]["error_type"] == "ToolDenied"

    root_result = await executable.run("run", bindings=RunBindings.embedded())

    assert len(handler_calls) == 1
    assert "handled by root" in root_result.output_or_raise()


async def test_failed_inline_result_validation_discards_session_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_count = 0
    original_reset = CodeActRunState.reset_inline

    async def observe_reset(state: CodeActRunState) -> None:
        nonlocal reset_count
        reset_count += 1
        await original_reset(state)

    monkeypatch.setattr(CodeActRunState, "reset_inline", observe_reset)

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        returns = _tool_returns(messages, "run_code")
        if not returns:
            yield {
                0: DeltaToolCall(
                    name="run_code",
                    json_args=json.dumps({"code": "saved = 3\n'123456789'"}),
                    tool_call_id="code-overflow",
                )
            }
        else:
            assert returns[-1].outcome == "failed"
            yield "reset" if reset_count > 0 else "retained"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(CodeActCapability(CodeActConfig(max_output_bytes=8)),),
    )
    result = await executable.run("test failed feed", bindings=RunBindings.embedded())

    assert result.output_or_raise() == "reset"
    assert reset_count >= 1


async def test_run_program_reads_direct_local_source_and_dispatches_current_tools(tmp_path: Path) -> None:
    (tmp_path / "job.codeact.py").write_text(
        "async def main(inputs):\n    return await double(value=inputs['value'])\n",
        encoding="utf-8",
    )
    calls: list[int] = []

    def double(value: int) -> int:
        calls.append(value)
        return value * 2

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        if not _tool_returns(messages, "run_program"):
            yield {
                0: DeltaToolCall(
                    name="run_program",
                    json_args=json.dumps({"path": "/workspace/job.codeact.py", "inputs": {"value": 5}}),
                    tool_call_id="program-1",
                )
            }
        else:
            yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(_codeact_tools(double, allowed=("double",)), CodeActCapability()),
    )
    result = await executable.run(
        "run program",
        bindings=RunBindings.embedded(environment=_local_environment(tmp_path)),
    )

    assert result.output_or_raise() == "done"
    assert calls == [5]
    returns = [part for part in _tool_returns(result.all_messages(), "run_program")]
    assert [part.content for part in returns] == [10]


@pytest.mark.parametrize("runner", ["run_code", "run_program"])
async def test_codeact_can_read_system_time(tmp_path: Path, runner: str) -> None:
    imports = "from datetime import date, datetime, timezone\nimport time\n"
    expression = (
        "{'time': time.time(), 'datetime': datetime.now(timezone.utc).timestamp(), 'date': date.today().isoformat()}"
    )
    if runner == "run_program":
        (tmp_path / "clock.codeact.py").write_text(
            imports + f"async def main(inputs):\n    return {expression}\n", encoding="utf-8"
        )
        arguments = {"path": "/workspace/clock.codeact.py"}
    else:
        arguments = {"code": imports + expression}

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        if not _tool_returns(messages, runner):
            yield {0: DeltaToolCall(name=runner, json_args=json.dumps(arguments), tool_call_id="clock-1")}
        else:
            yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(CodeActCapability(),),
    )
    before_time = time.time()
    result = await executable.run(
        "read system time", bindings=RunBindings.embedded(environment=_local_environment(tmp_path))
    )
    after_time = time.time()

    assert result.output_or_raise() == "done"
    returned = _tool_returns(result.all_messages(), runner)[0]
    assert returned.outcome == "success"
    assert isinstance(returned.content, dict)
    assert before_time <= returned.content["time"] <= after_time
    assert before_time <= returned.content["datetime"] <= after_time
    assert (
        datetime.fromtimestamp(before_time, UTC).date().isoformat()
        <= returned.content["date"]
        <= datetime.fromtimestamp(after_time, UTC).date().isoformat()
    )


@pytest.mark.parametrize(
    ("source", "config"),
    [
        pytest.param("while True:\n    pass", CodeActConfig(timeout_seconds=1), id="compute-timeout"),
        pytest.param("[0] * 10_000_000", CodeActConfig(max_memory_bytes=1024 * 1024), id="memory-limit"),
    ],
)
async def test_codeact_resource_failure_resets_inline_state(source: str, config: CodeActConfig) -> None:
    requests = 0

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        nonlocal requests
        del info
        requests += 1
        if requests == 1:
            code = "saved = 42\n" + source
        elif requests == 2:
            assert _tool_returns(messages, "run_code")[-1].outcome == "failed"
            code = "retained = False\ntry:\n    saved\n    retained = True\nexcept NameError:\n    pass\nretained"
        else:
            assert _tool_returns(messages, "run_code")[-1].content is False
            yield "reset"
            return
        yield {
            0: DeltaToolCall(name="run_code", json_args=json.dumps({"code": code}), tool_call_id=f"limit-{requests}")
        }

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(CodeActCapability(config),),
    )
    result = await executable.run("test resource limits", bindings=RunBindings.embedded())

    assert result.output_or_raise() == "reset"
    assert requests == 3


async def test_inline_state_survives_many_individually_budgeted_feeds() -> None:
    calls = 0

    def increment(value: int) -> int:
        nonlocal calls
        calls += 1
        return value + 1

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        returns = _tool_returns(messages, "run_code")
        if not returns:
            code = "saved = 42\nsaved"
        elif len(returns) < 7:
            # Each feed stays below max_tool_calls, but six feeds exceed Monty's
            # default 1000 cumulative external-call/future-resolution suspensions.
            code = "for i in range(100):\n    saved = await increment(value=saved)\nsaved"
        else:
            assert [part.content for part in returns] == [42, 142, 242, 342, 442, 542, 642]
            yield "done"
            return
        yield {
            0: DeltaToolCall(name="run_code", json_args=json.dumps({"code": code}), tool_call_id=f"feed-{len(returns)}")
        }

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(CodeActCapability(), _codeact_tools(increment, allowed=("increment",))),
    )
    result = await executable.run("keep inline state across feeds", bindings=RunBindings.embedded())

    assert result.output_or_raise() == "done"
    assert calls == 600


async def test_inline_delegation_gives_root_and_child_independent_codeact_runtimes() -> None:
    calls: list[tuple[str, int]] = []

    def parent_double(value: int) -> int:
        calls.append(("parent", value))
        return value * 2

    def child_double(value: int) -> int:
        calls.append(("child", value))
        return value * 2

    async def child_model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        if not _tool_returns(messages, "run_code"):
            yield {
                0: DeltaToolCall(
                    name="run_code",
                    json_args='{"code":"child_value = await child_double(value=3)\\nchild_value"}',
                    tool_call_id="child-code-1",
                )
            }
        else:
            yield "child-done"

    child = AgentDefinition(
        agent=AgentSpec(),
        output_type=str,
        definition_id="codeact-child-v1",
        model=FunctionModel(stream_function=child_model),
        capabilities=(_codeact_tools(child_double, allowed=("child_double",)), CodeActCapability()),
    )

    async def parent_model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        code_returns = _tool_returns(messages, "run_code")
        delegate_returns = _tool_returns(messages, "delegate")
        if not code_returns:
            yield {
                0: DeltaToolCall(
                    name="run_code",
                    json_args='{"code":"root_value = await parent_double(value=2)\\nroot_value"}',
                    tool_call_id="parent-code-1",
                )
            }
        elif not delegate_returns:
            yield {
                0: DeltaToolCall(
                    name="delegate",
                    json_args='{"subagent":"reviewer","prompt":"run child code"}',
                    tool_call_id="delegate-1",
                )
            }
        else:
            yield "parent-done"

    async def allow(*args: Any, **kwargs: Any) -> InvocationPolicyDecision:
        del args, kwargs
        return InvocationPolicyDecision.allow()

    parent = AgentDefinition(
        agent=AgentSpec(),
        output_type=str,
        definition_id="codeact-parent-v1",
        model=FunctionModel(stream_function=parent_model),
        capabilities=(
            _codeact_tools(parent_double, allowed=("parent_double",)),
            CodeActCapability(),
            SubagentCapability(),
        ),
        subagents=(
            SubagentDefinition(
                name="reviewer",
                description="Run one bounded child task.",
                agent=child,
            ),
        ),
    )

    bindings = RunBindings(
        instance=AgentInstanceContext(
            identity=AgentIdentityRef(issuer="test", subject="parent"),
            agent_instance_id="parent-1",
        ),
        environment=EmptyEnvironmentRuntime(),
        capabilities=(InvocationPolicyCapability(evaluator=allow),),
    )
    result = await HarnessBuilder().build(parent).run("start", bindings=bindings)

    assert result.output_or_raise() == "parent-done"
    assert calls == [("parent", 2), ("child", 3)]


@pytest.mark.parametrize("local_mount", [False, True])
async def test_run_program_unreadable_source_returns_tool_failure_and_continues(
    tmp_path: Path, local_mount: bool
) -> None:
    requests = 0

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        nonlocal requests
        requests += 1
        if requests == 1:
            yield {
                0: DeltaToolCall(
                    name="run_program", json_args='{"path":"/missing.codeact.py"}', tool_call_id="program-1"
                )
            }
        else:
            returned = _tool_returns(messages, "run_program")[-1]
            assert returned.outcome == "failed"
            assert "CodeAct program could not be read" in str(returned.content)
            assert "Environment mount" in str(returned.content)
            yield "Recovered from missing program."

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(CodeActCapability(),),
    )
    environment = _local_environment(tmp_path) if local_mount else EmptyEnvironmentRuntime()
    result = await executable.run("run a program", bindings=RunBindings.embedded(environment=environment))
    assert result.status == "completed"
    assert requests == 2


@pytest.mark.parametrize("runner", ["run_code", "run_program"])
@pytest.mark.parametrize(
    "source",
    [
        pytest.param("if True print('invalid')", id="syntax"),
        pytest.param("1 / 0", id="runtime"),
        pytest.param(
            "import asyncio\nawait asyncio.gather(*[view(file_path=p) for p in ['AGENTS.md']])",
            id="unavailable-tool",
        ),
        pytest.param("await double(value='invalid')", id="nested-validation"),
    ],
)
async def test_codeact_repeated_source_failures_do_not_exhaust_model_retries(
    tmp_path: Path, runner: str, source: str
) -> None:
    requests = 0
    calls: list[int] = []

    def double(value: int) -> int:
        calls.append(value)
        return value * 2

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        nonlocal requests
        del info
        requests += 1
        if requests <= 3:
            code = source
        elif requests == 4:
            code = "await double(value=21)"
        else:
            returns = _tool_returns(messages, runner)
            assert [part.outcome for part in returns] == ["failed", "failed", "failed", "success"]
            assert returns[-1].content == 42
            assert not any(
                isinstance(part, RetryPromptPart)
                for message in messages
                if isinstance(message, ModelRequest)
                for part in message.parts
            )
            yield "recovered"
            return
        if runner == "run_code":
            arguments = {"code": code}
        else:
            lines = code.splitlines()
            lines[-1] = "return " + lines[-1]
            (tmp_path / "job.codeact.py").write_text(
                "async def main(inputs):\n" + "\n".join("    " + line for line in lines) + "\n",
                encoding="utf-8",
            )
            arguments = {"path": "/workspace/job.codeact.py"}
        yield {0: DeltaToolCall(name=runner, json_args=json.dumps(arguments), tool_call_id=f"attempt-{requests}")}

    executable = HarnessBuilder().build(
        AgentSpec(retries={"tools": 0}),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(CodeActCapability(), _codeact_tools(double, allowed=("double",))),
    )
    result = await executable.run(
        "recover from source errors", bindings=RunBindings.embedded(environment=_local_environment(tmp_path))
    )

    assert result.output_or_raise() == "recovered"
    assert requests == 5
    assert calls == [21]


@pytest.mark.parametrize(
    ("runner", "arguments", "config"),
    [
        pytest.param("run_code", {"code": "'too long'"}, CodeActConfig(max_source_bytes=4), id="source-size"),
        pytest.param("run_program", {"path": "job.py"}, CodeActConfig(), id="program-suffix"),
        pytest.param("run_program", {"path": "job.codeact.py"}, CodeActConfig(), id="program-entrypoint"),
        pytest.param(
            "run_program",
            {"path": "job.codeact.py", "inputs": {"value": "too long"}},
            CodeActConfig(max_output_bytes=4),
            id="program-input-size",
        ),
    ],
)
async def test_codeact_preflight_failure_returns_to_model_with_no_retries(
    tmp_path: Path, runner: str, arguments: dict[str, Any], config: CodeActConfig
) -> None:
    (tmp_path / "job.codeact.py").write_text("def main(inputs):\n    return inputs\n", encoding="utf-8")
    requests = 0

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        nonlocal requests
        del info
        requests += 1
        if requests == 1:
            yield {0: DeltaToolCall(name=runner, json_args=json.dumps(arguments), tool_call_id="preflight")}
        else:
            assert _tool_returns(messages, runner)[-1].outcome == "failed"
            yield "handled"

    executable = HarnessBuilder().build(
        AgentSpec(retries={"tools": 0}),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(CodeActCapability(config),),
    )
    result = await executable.run(
        "handle invalid input", bindings=RunBindings.embedded(environment=_local_environment(tmp_path))
    )
    assert result.output_or_raise() == "handled"
    assert requests == 2


@pytest.mark.parametrize("start_tool", [False, True])
async def test_codeact_failure_metadata_tracks_started_calls_without_replay(start_tool: bool) -> None:
    calls: list[int] = []
    requests = 0

    def record(value: int) -> None:
        calls.append(value)

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        nonlocal requests
        del info
        requests += 1
        if requests == 1:
            source = "await record(value=7)\n" if start_tool else ""
            source += "raise ValueError('private-value-not-for-diagnostics')"
            yield {0: DeltaToolCall(name="run_code", json_args=json.dumps({"code": source}), tool_call_id="failure")}
        else:
            returned = _tool_returns(messages, "run_code")[-1]
            assert returned.outcome == "failed"
            payload = json.loads(str(returned.content))
            assert payload["codeact"]["status"] == "failed"
            assert payload["codeact"]["tool_call_count"] == int(start_tool)
            assert payload["codeact"]["side_effect_uncertain"] is start_tool
            assert payload["error"]["type"] == "MontyRuntimeError"
            assert "private-value-not-for-diagnostics" not in str(returned.content)
            yield "handled"

    executable = HarnessBuilder().build(
        AgentSpec(retries={"tools": 0}),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(CodeActCapability(), _codeact_tools(record, allowed=("record",))),
    )
    result = await executable.run("handle execution failure", bindings=RunBindings.embedded())
    assert result.output_or_raise() == "handled"
    assert requests == 2
    assert calls == ([7] if start_tool else [])


@pytest.mark.parametrize("runner", ["run_code", "run_program"])
@pytest.mark.parametrize(
    ("failure_phase", "catch_error"),
    [("arguments", False), ("body", False), ("hook", False), ("body", True)],
)
@pytest.mark.parametrize("prior_success", [False, True])
async def test_codeact_validation_failure_settles_started_calls(
    tmp_path: Path, runner: str, failure_phase: str, catch_error: bool, prior_success: bool
) -> None:
    from pydantic import TypeAdapter

    effects: list[str] = []
    requests = 0

    def record() -> None:
        effects.append("record")

    def target(value: int) -> int:
        effects.append("target")
        if failure_phase == "body":
            TypeAdapter(int).validate_python("private-validation-value")
        return value

    class ValidateResult(AbstractCapability):
        async def after_tool_execute(self, ctx, *, call, tool_def, args, result):
            if call.tool_name == "target" and failure_phase == "hook":
                TypeAdapter(int).validate_python("private-validation-value")
            return result

    code = "await record()\n" if prior_success else ""
    code += "await target(value='invalid')" if failure_phase == "arguments" else "await target(value=7)"
    if catch_error:
        code = (
            "try:\n"
            + "\n".join("    " + line for line in code.splitlines())
            + "\nexcept RuntimeError as exc:\n    error = str(exc)\nerror"
        )
    if runner == "run_code":
        arguments = {"code": code}
    else:
        lines = code.splitlines()
        if catch_error:
            lines[-1] = "return " + lines[-1]
        (tmp_path / "job.codeact.py").write_text(
            "async def main(inputs):\n" + "\n".join("    " + line for line in lines) + "\n",
            encoding="utf-8",
        )
        arguments = {"path": "/workspace/job.codeact.py"}
    started_count = int(prior_success) + int(failure_phase != "arguments")

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        nonlocal requests
        del info
        requests += 1
        if requests == 1:
            yield {0: DeltaToolCall(name=runner, json_args=json.dumps(arguments), tool_call_id="validation-failure")}
        else:
            returned = _tool_returns(messages, runner)[-1]
            if catch_error:
                assert returned.outcome == "success"
                assert returned.content == "Nested tool 'target' failed with ValidationError"
            else:
                assert returned.outcome == "failed"
                payload = json.loads(str(returned.content))
                assert payload["codeact"]["tool_call_count"] == started_count
                assert payload["codeact"]["side_effect_uncertain"] is (started_count > 0)
            assert "private-validation-value" not in str(returned.content)
            yield "handled"

    executable = HarnessBuilder().build(
        AgentSpec(retries={"tools": 0}),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(
            CodeActCapability(),
            _codeact_tools(record, target, allowed=("record", "target")),
            ValidateResult(),
        ),
    )
    payloads: list[dict[str, Any]] = []
    async with executable.stream(
        "handle validation failure", bindings=RunBindings.embedded(environment=_local_environment(tmp_path))
    ) as stream:
        async for item in stream:
            if isinstance(item, HarnessEvent) and isinstance(item.event, HarnessExtensionEvent):
                if item.event.kind == "diagnostic":
                    payloads.append(item.event.payload)

    assert stream.result is not None
    assert stream.result.output_or_raise() == "handled"
    assert requests == 2
    assert effects == (["record"] if prior_success else []) + ([] if failure_phase == "arguments" else ["target"])
    started = [payload for payload in payloads if payload["type"] == "codeact_tool_call_started"]
    completed = [payload for payload in payloads if payload["type"] == "codeact_tool_call_completed"]
    assert len(started) == len(completed) == started_count
    assert [payload["nested_tool_call_id"] for payload in completed] == [
        payload["nested_tool_call_id"] for payload in started
    ]
    assert [payload["outcome"] for payload in completed] == (["completed"] if prior_success else []) + (
        [] if failure_phase == "arguments" else ["failed"]
    )
    if failure_phase != "arguments":
        assert completed[-1]["error_type"] == "ValidationError"
        assert completed[-1]["side_effect_uncertain"] is True
        assert completed[-1]["duration_ms"] >= 0
    execution = next(payload for payload in payloads if payload["type"] == "codeact_execution_completed")
    assert execution["status"] == ("completed" if catch_error else "failed")
    assert execution["call_count"] == started_count
    assert execution["side_effect_uncertain"] is (not catch_error and started_count > 0)
    assert "private-validation-value" not in json.dumps(payloads)


async def test_background_runner_keeps_real_nested_values_and_publishes_late_state() -> None:
    import asyncio

    from a13n_harness.capabilities.tool_execution import ToolExecutionCapability
    from a13n_harness.codeact.state import CodeActStateOwner
    from a13n_harness.tool_execution import ToolExecutionConfig, ToolExecutionRuntime

    entered, finish = asyncio.Event(), asyncio.Event()
    calls = 0
    requests = 0
    receipt = None

    async def lookup() -> int:
        nonlocal calls
        calls += 1
        entered.set()
        await finish.wait()
        return 21

    async def model(messages, info):
        nonlocal requests, receipt
        requests += 1
        if requests == 1:
            yield {
                0: DeltaToolCall(
                    name="run_code",
                    tool_call_id="code-background",
                    json_args=json.dumps(
                        {"code": 'value = await lookup()\nawait store(key="late", value=value * 2)\nvalue * 2'}
                    ),
                )
            }
        else:
            if requests == 2:
                receipt = _tool_returns(messages, "run_code")[-1].content
            yield "conversation continues"

    runtime, values = ToolExecutionRuntime(), CodeActStateOwner()
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(
            CodeActCapability(),
            ToolExecutionCapability(ToolExecutionConfig(mode="auto", foreground_wait_seconds=0)),
            _codeact_tools(lookup, allowed=("lookup",)),
        ),
    )
    first = await executable.run("start", bindings=RunBindings.embedded(tool_execution=runtime, codeact_state=values))
    frozen_checkpoint = first.state.model_dump_json()
    await entered.wait()
    assert receipt is not None and receipt["execution_id"].startswith("exec-")
    finish.set()
    outcome = await runtime.wait(first.thread_id, receipt["execution_id"])
    assert outcome.record.status == "succeeded", outcome.record.error
    assert outcome.result.return_value == 42
    assert calls == 1
    assert (await values.snapshot()).values == {"late": 42}
    assert first.state.model_dump_json() == frozen_checkpoint
    second = await executable.run(
        "next",
        previous_state=first.state,
        bindings=RunBindings.embedded(tool_execution=runtime, codeact_state=values),
    )
    assert '"late":42' in second.state.model_dump_json()
    assert first.state.model_dump_json() == frozen_checkpoint
    await runtime.close()


async def test_owned_codeact_requires_host_held_explicit_state():
    from a13n_harness.capabilities.tool_execution import ToolExecutionCapability
    from a13n_harness.tool_execution import ToolExecutionRuntime

    requests = []

    async def model(messages, info):
        requests.append(messages)
        yield "should not run"

    runtime = ToolExecutionRuntime()
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(CodeActCapability(), ToolExecutionCapability()),
    )
    result = await executable.run("start", bindings=RunBindings.embedded(tool_execution=runtime))
    assert result.status != "completed"
    assert requests == []
    assert await runtime.summarize(result.thread_id) == ()
    await runtime.close()


async def test_background_runner_retains_dynamic_generation_and_task_affine_scope() -> None:
    import asyncio

    from a13n_harness.capabilities.tool_execution import ToolExecutionCapability
    from a13n_harness.tool_execution import ToolExecutionConfig, ToolExecutionRuntime
    from anyio import create_task_group
    from pydantic_ai.toolsets import DynamicToolset

    finish = asyncio.Event()
    generations = []
    calls = []
    receipt = None
    requests = 0

    class ScopedTools(FunctionToolset):
        async def __aenter__(self):
            self.group = create_task_group()
            await self.group.__aenter__()
            self.owner = asyncio.current_task()
            self.open = True
            return self

        async def __aexit__(self, *args):
            assert asyncio.current_task() is self.owner
            self.open = False
            await self.group.__aexit__(*args)

    def factory(ctx):
        generation = len(generations) + 1

        async def lookup() -> int:
            assert tools.open
            calls.append(generation)
            await finish.wait()
            assert tools.open
            return generation

        tools = ScopedTools([lookup], id="generation-tools")
        generations.append(tools)
        return CodeActPolicyToolset(wrapped=tools, policy=CodeActToolPolicy(default=True))

    async def model(messages, info):
        nonlocal requests, receipt
        requests += 1
        if requests == 1:
            yield {
                0: DeltaToolCall(
                    name="run_code",
                    tool_call_id="dynamic-code",
                    json_args=json.dumps({"code": "first = await lookup()\nsecond = await lookup()\n[first, second]"}),
                )
            }
        else:
            receipt = _tool_returns(messages, "run_code")[-1].content
            yield "continue"

    runtime = ToolExecutionRuntime()
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(
            CodeActCapability(),
            ToolExecutionCapability(ToolExecutionConfig(mode="auto", foreground_wait_seconds=0)),
            Capability(toolsets=[DynamicToolset(factory)]),
        ),
    )
    from a13n_harness.codeact.state import CodeActStateOwner

    result = await executable.run(
        "start", bindings=RunBindings.embedded(tool_execution=runtime, codeact_state=CodeActStateOwner())
    )
    assert len(generations) >= 2
    assert generations[0].open
    finish.set()
    outcome = await runtime.wait(result.thread_id, receipt["execution_id"])
    assert outcome.record.status == "succeeded", outcome.record.error
    assert outcome.result.return_value == [1, 1]
    assert calls == [1, 1]
    assert not any(item.open for item in generations)
    await runtime.close()


async def test_inline_session_lease_covers_result_validation_and_busy_restart():
    import asyncio
    from unittest.mock import AsyncMock

    state = CodeActRunState(CodeActConfig())
    validating, finish = asyncio.Event(), asyncio.Event()

    async def result(execution):
        return execution.completed.output

    async def blocked_result(execution):
        validating.set()
        await finish.wait()
        return await result(execution)

    options = dict(
        dispatch=AsyncMock(),
        admit=AsyncMock(),
        release=AsyncMock(),
        valid_names=set(),
        sequential_names=set(),
        global_sequential=False,
        restart=False,
    )
    try:
        active = asyncio.create_task(state.execute_inline("saved = 41\nsaved", finish=blocked_result, **options))
        await validating.wait()
        with pytest.raises(RuntimeError, match="already owns"):
            await state.execute_inline("saved = 0", finish=result, **{**options, "restart": True})
        finish.set()
        assert await active == 41
        assert await state.execute_inline("saved + 1", finish=result, **options) == 42

        async def invalid_result(execution):
            raise ValueError("invalid final output")

        with pytest.raises(ValueError, match="invalid final output"):
            await state.execute_inline("saved", finish=invalid_result, **options)
        from pydantic_monty import MontyRuntimeError

        with pytest.raises(MontyRuntimeError, match="saved"):
            await state.execute_inline("saved", finish=result, **options)
    finally:
        await state.close()
