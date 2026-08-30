from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import a13n_harness.tools.invocation as tool_invocation_module
import a13n_harness.tools.surface as tool_surface_module
import pytest
from a13n_harness import (
    AgentIdentityRef,
    AgentInstanceContext,
    DeferredToolResume,
    DefinitionError,
    HarnessBuilder,
    RunBindings,
    RunError,
    RuntimeContextCapability,
)
from a13n_harness.environment.advanced import EmptyEnvironmentRuntime
from a13n_harness.tools import (
    ClientToolDefinition,
    ClientToolsCapability,
    ClientToolsetDefinition,
    ClientToolsRunCapability,
    ClientToolsSpec,
    HarnessTool,
    HarnessToolMetadata,
    InvocationPolicyCapability,
    InvocationPolicyDecision,
    ToolOutputPolicy,
)
from pydantic import ValidationError
from pydantic_ai import DeferredToolResults, RunContext, Tool, ToolReturn
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.capabilities import AbstractCapability, Capability
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel
from pydantic_ai.toolsets import AbstractToolset

pytestmark = pytest.mark.anyio


def _tool(name: str, *, instruction: str | None = None) -> ClientToolDefinition:
    return ClientToolDefinition(
        name=name,
        description=f"Execute {name} in the external client.",
        parameters_json_schema={
            "type": "object",
            "properties": {"value": {"type": "integer"}},
            "required": ["value"],
        },
        instruction=instruction,
        metadata={"surface": "test"},
    )


def _toolset(name: str, *, toolset_id: str = "client") -> ClientToolsetDefinition:
    return ClientToolsetDefinition(toolset_id=toolset_id, tools=(_tool(name),))


def _model(tool_name: str) -> FunctionModel:
    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        returns = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        if not returns:
            yield {
                0: DeltaToolCall(
                    name=tool_name,
                    json_args=json.dumps({"value": 7}),
                    tool_call_id="external-1",
                )
            }
        else:
            yield f"client-result:{returns[-1].content}"

    return FunctionModel(stream_function=stream)


def _build(spec: ClientToolsSpec, *, tool_name: str, extra_capabilities=()):
    return HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=_model(tool_name),
        capabilities=(ClientToolsCapability(spec=spec), *extra_capabilities),
    )


def _child_bindings() -> RunBindings:
    return RunBindings(
        instance=AgentInstanceContext(
            identity=AgentIdentityRef(issuer="test", subject="child"),
            agent_instance_id="child-1",
            parent_agent_instance_id="parent-1",
            delegation_id="delegation-1",
        ),
        environment=EmptyEnvironmentRuntime(),
    )


async def test_default_client_tool_suspends_without_executing_in_process() -> None:
    spec = ClientToolsSpec(default_toolsets=(_toolset("client_action"),))
    executable = _build(spec, tool_name="client_action")

    result = await executable.run("go", bindings=RunBindings.embedded())

    assert result.status == "suspended"
    assert result.suspend_reason == "deferred"
    assert result.deferred is not None
    assert [call.tool_name for call in result.deferred.calls] == ["client_action"]
    assert result.deferred.approvals == []


async def test_child_surface_removes_external_and_unapproved_tools_but_keeps_functions() -> None:
    observed_tools: list[dict[str, str]] = []
    observed_instructions: list[str] = []

    def local_action(value: int) -> int:
        return value

    def approved_action(value: int) -> int:
        return value

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages
        observed_tools.append({tool.name: tool.kind for tool in info.function_tools})
        observed_instructions.append(info.instructions or "")
        yield "child-done"

    spec = ClientToolsSpec(
        default_toolsets=(
            ClientToolsetDefinition(
                toolset_id="client",
                tools=(_tool("client_action", instruction="Ask the user before sending."),),
            ),
        )
    )
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(
            ClientToolsCapability(spec=spec),
            Capability(
                tools=[local_action, Tool(approved_action, requires_approval=True)],
                id="test-functions",
            ),
        ),
    )

    result = await executable.run("go", bindings=_child_bindings())

    assert result.output_or_raise() == "child-done"
    assert observed_tools == [{"local_action": "function"}]
    assert "client_action" not in observed_instructions[0]
    assert "Ask the user before sending." not in observed_instructions[0]


async def test_child_deferred_terminal_guard_fails_instead_of_suspending(monkeypatch: pytest.MonkeyPatch) -> None:
    original_resolver = tool_surface_module.resolve_tool_surface

    def bypass_child_filter(candidates, *, allow_deferred=True):
        del allow_deferred
        return original_resolver(candidates, allow_deferred=True)

    monkeypatch.setattr(tool_surface_module, "resolve_tool_surface", bypass_child_filter)

    async def bypass_child_runtime_denial(self, ctx, *, requests):
        del self, ctx, requests
        return None

    monkeypatch.setattr(
        tool_invocation_module.ToolExecutionBoundaryCapability,
        "handle_deferred_tool_calls",
        bypass_child_runtime_denial,
    )
    spec = ClientToolsSpec(default_toolsets=(_toolset("client_action"),))
    executable = _build(spec, tool_name="client_action")

    result = await executable.run("go", bindings=_child_bindings())

    assert result.status == "failed"
    assert result.failure is not None
    assert result.failure.code == "subagent_deferred_unsupported"
    assert result.state is not None
    assert result.deferred is None
    assert result.suspend_reason is None
    assert result.all_messages()
    assert result.usage.requests == 1


async def test_client_tool_instructions_are_deterministic_and_run_frozen() -> None:
    seen: list[str | None] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        del messages
        seen.append(info.instructions)
        yield {
            0: DeltaToolCall(
                name="client_action",
                json_args=json.dumps({"value": 1}),
                tool_call_id="external-1",
            )
        }

    spec = ClientToolsSpec(
        default_toolsets=(
            ClientToolsetDefinition(
                toolset_id="client",
                tools=(_tool("client_action", instruction="Ask the user before sending."),),
            ),
        )
    )
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(ClientToolsCapability(spec=spec),),
    )
    result = await executable.run("go", bindings=RunBindings.embedded())

    assert result.status == "suspended"
    assert seen == ["Client tool `client_action`: Ask the user before sending."]


async def test_run_override_replaces_defaults_and_empty_override_clears_them() -> None:
    seen_names: list[set[str]] = []

    @dataclass
    class CaptureTools(AbstractCapability[Any]):
        async def before_model_request(self, ctx: RunContext[Any], request_context):
            seen_names.append(ctx.available_tool_names)
            return request_context

    spec = ClientToolsSpec(
        default_toolsets=(_toolset("default_action"),),
        allow_run_override=True,
    )
    replacement = _build(spec, tool_name="replacement_action", extra_capabilities=(CaptureTools(),))
    replaced = await replacement.run(
        "go",
        bindings=RunBindings.embedded(
            capabilities=(ClientToolsRunCapability(toolsets=(_toolset("replacement_action"),)),)
        ),
    )
    assert replaced.status == "suspended"
    assert replaced.deferred is not None
    assert [call.tool_name for call in replaced.deferred.calls] == ["replacement_action"]
    assert "default_action" not in seen_names[0]

    async def text_stream(messages, info):
        del messages, info
        yield "done"

    cleared = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=text_stream),
        capabilities=(ClientToolsCapability(spec=spec), CaptureTools()),
    )
    clear_result = await cleared.run(
        "go",
        bindings=RunBindings.embedded(capabilities=(ClientToolsRunCapability(toolsets=()),)),
    )
    assert clear_result.status == "completed"
    assert "default_action" not in seen_names[-1]


async def test_override_policy_and_owner_are_fail_closed() -> None:
    spec = ClientToolsSpec(default_toolsets=(_toolset("default_action"),), allow_run_override=False)
    executable = _build(spec, tool_name="replacement_action")
    with pytest.raises(DefinitionError) as forbidden:
        await executable.run(
            "go",
            bindings=RunBindings.embedded(
                capabilities=(ClientToolsRunCapability(toolsets=(_toolset("replacement_action"),)),)
            ),
        )
    assert forbidden.value.code == "client_tools_override_forbidden"

    without_owner = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=_model("replacement_action"),
    )
    with pytest.raises(DefinitionError) as missing:
        await without_owner.run(
            "go",
            bindings=RunBindings.embedded(
                capabilities=(ClientToolsRunCapability(toolsets=(_toolset("replacement_action"),)),)
            ),
        )
    assert missing.value.code == "client_tools_owner_missing"


async def test_external_suspend_detach_rebuild_and_resume() -> None:
    spec = ClientToolsSpec(default_toolsets=(_toolset("client_action"),))
    first_executable = _build(spec, tool_name="client_action")
    first = await first_executable.run("go", bindings=RunBindings.embedded())
    assert first.state is not None and first.deferred is not None

    requests = first.deferred
    rebuilt = _build(spec, tool_name="client_action")
    second = await rebuilt.run(
        bindings=RunBindings.embedded(),
        previous_state=first.state,
        deferred_resume=DeferredToolResume(
            requests,
            requests.build_results(calls={requests.calls[0].tool_call_id: {"ok": True}}),
        ),
    )

    assert second.status == "completed"
    assert "ok" in second.output_or_raise()


async def test_deferred_result_boundary_preserves_prior_wire_history_until_provider_advances() -> None:
    spec = ClientToolsSpec(default_toolsets=(_toolset("client_action"),))
    first = await _build(
        spec,
        tool_name="client_action",
        extra_capabilities=(RuntimeContextCapability(),),
    ).run("go", bindings=RunBindings.embedded())
    assert first.state is not None and first.deferred is not None
    original = list(first.state.message_history)
    seen: list[list[ModelMessage]] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del info
        seen.append(messages)
        yield "done"

    rebuilt = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(
            ClientToolsCapability(spec=spec),
            RuntimeContextCapability(),
        ),
    )
    requests = first.deferred
    result = await rebuilt.run(
        bindings=RunBindings.embedded(),
        previous_state=first.state,
        deferred_resume=DeferredToolResume(
            requests,
            requests.build_results(calls={requests.calls[0].tool_call_id: {"ok": True}}),
        ),
    )

    assert result.output_or_raise() == "done"
    assert seen[0][: len(original)] == original
    assert isinstance(seen[0][-1], ModelRequest)
    assert any(isinstance(part, ToolReturnPart) for part in seen[0][-1].parts)


async def test_deferred_external_results_reject_wrapped_non_finite_values_and_cycles() -> None:
    first = await _build(
        ClientToolsSpec(default_toolsets=(_toolset("client_action"),)),
        tool_name="client_action",
    ).run("go", bindings=RunBindings.embedded())
    assert first.state is not None and first.deferred is not None
    requests = first.deferred
    call_id = requests.calls[0].tool_call_id

    with pytest.raises(RunError) as wrapped_nan:
        _build(
            ClientToolsSpec(default_toolsets=(_toolset("client_action"),)),
            tool_name="client_action",
        ).stream(
            bindings=RunBindings.embedded(),
            previous_state=first.state,
            deferred_resume=DeferredToolResume(
                requests,
                DeferredToolResults(calls={call_id: ToolReturn({"value": float("nan")})}),
            ),
        )
    assert wrapped_nan.value.code == "deferred_results_invalid"

    cyclic: dict[str, Any] = {}
    cyclic["self"] = cyclic
    with pytest.raises(RunError) as cycle:
        _build(
            ClientToolsSpec(default_toolsets=(_toolset("client_action"),)),
            tool_name="client_action",
        ).stream(
            bindings=RunBindings.embedded(),
            previous_state=first.state,
            deferred_resume=DeferredToolResume(
                requests,
                DeferredToolResults(calls={call_id: cyclic}),
            ),
        )
    assert cycle.value.code == "deferred_results_invalid"


async def test_mixed_external_and_approval_batch_resumes_through_native_categories() -> None:
    executed: list[int] = []

    def managed_change(value: int) -> int:
        executed.append(value)
        return value

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        returns = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        if not returns:
            yield {
                0: DeltaToolCall(
                    name="client_action",
                    json_args=json.dumps({"value": 7}),
                    tool_call_id="external-1",
                ),
                1: DeltaToolCall(
                    name="managed_change",
                    json_args=json.dumps({"value": 8}),
                    tool_call_id="approval-1",
                ),
            }
        else:
            yield "done"

    class Allow:
        async def __call__(self, invocation, metadata, *, context):
            del invocation, metadata, context
            return InvocationPolicyDecision.allow()

    spec = ClientToolsSpec(default_toolsets=(_toolset("client_action"),))
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(
            ClientToolsCapability(spec=spec),
            Capability(
                tools=[
                    HarnessTool(
                        managed_change,
                        harness_metadata=HarnessToolMetadata(
                            tool_id="managed.change",
                            effects=frozenset({"write"}),
                            credential_audiences=(),
                            idempotency="provider_key",
                            output_policy=ToolOutputPolicy(max_inline_bytes=1024, max_output_bytes=4096),
                        ),
                        requires_approval=True,
                    )
                ],
                id="test-tools",
            ),
        ),
    )
    first = await executable.run("go", bindings=RunBindings.embedded())
    assert first.state is not None and first.deferred is not None
    assert len(first.deferred.calls) == 1
    assert len(first.deferred.approvals) == 1

    requests = first.deferred
    second = await executable.run(
        bindings=RunBindings.embedded(capabilities=(InvocationPolicyCapability(evaluator=Allow()),)),
        previous_state=first.state,
        deferred_resume=DeferredToolResume(
            requests,
            requests.build_results(
                calls={requests.calls[0].tool_call_id: {"client": "ok"}},
                approve_all=True,
            ),
        ),
    )

    assert second.status == "completed"
    assert executed == [8]


async def test_changed_external_surface_is_rejected_before_new_model_work() -> None:
    first = await _build(
        ClientToolsSpec(default_toolsets=(_toolset("old_action"),)),
        tool_name="old_action",
    ).run("go", bindings=RunBindings.embedded())
    assert first.state is not None and first.deferred is not None
    requests = first.deferred

    changed = _build(
        ClientToolsSpec(default_toolsets=(_toolset("new_action"),)),
        tool_name="new_action",
    )
    with pytest.raises(DefinitionError) as exc_info:
        await changed.run(
            bindings=RunBindings.embedded(),
            previous_state=first.state,
            deferred_resume=DeferredToolResume(
                requests,
                requests.build_results(calls={requests.calls[0].tool_call_id: "done"}),
            ),
        )
    assert exc_info.value.code == "deferred_surface_mismatch"


@dataclass
class _PrefixTools(AbstractCapability[Any]):
    def get_wrapper_toolset(self, toolset: AbstractToolset[Any]) -> AbstractToolset[Any]:
        return toolset.prefixed("changed_")


async def test_client_tool_final_name_cannot_be_changed_by_an_inner_wrapper() -> None:
    executable = _build(
        ClientToolsSpec(default_toolsets=(_toolset("client_action"),)),
        tool_name="changed_client_action",
        extra_capabilities=(_PrefixTools(),),
    )
    with pytest.raises(DefinitionError) as exc_info:
        await executable.run("go", bindings=RunBindings.embedded())
    assert exc_info.value.code == "client_tool_name_changed"


def test_client_declarations_reject_authority_metadata_and_non_object_schema() -> None:
    with pytest.raises(ValidationError):
        ClientToolDefinition(
            name="bad",
            description="bad",
            parameters_json_schema={"type": "string"},
        )
    with pytest.raises(ValidationError):
        ClientToolDefinition(
            name="bad",
            description="bad",
            parameters_json_schema={"type": "object"},
            metadata={"nested": {"credential": "secret"}},
        )
    with pytest.raises(ValidationError):
        ClientToolDefinition(
            name="bad",
            description="bad",
            parameters_json_schema={
                "type": "object",
                "properties": {"value": {"type": "number", "default": float("nan")}},
            },
        )
