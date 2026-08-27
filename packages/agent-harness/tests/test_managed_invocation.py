from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest
from a13n_harness import AgentContext, HarnessBuilder, HarnessEvent, RunBindings
from a13n_harness.errors import DefinitionError
from a13n_harness.tools import (
    ClientToolsCapability,
    HarnessTool,
    HarnessToolMetadata,
    InvocationPolicyCapability,
    InvocationPolicyDecision,
    ToolOutputPolicy,
)
from a13n_harness.tools.invocation import _apply_result_policy
from a13n_harness.toolsets import (
    FINAL_TOOL_OUTPUT_HARD_CHARS,
    acknowledge_tool_output,
    tool_output_bytes,
    tool_output_text,
)
from pydantic_ai import (
    AudioUrl,
    BinaryContent,
    CachePoint,
    DocumentUrl,
    ImageUrl,
    RunContext,
    TextContent,
    ToolReturn,
    UploadedFile,
    VideoUrl,
)
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.capabilities import AbstractCapability, Capability, CapabilityOrdering, CombinedCapability
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel

pytestmark = pytest.mark.anyio


def _metadata(tool_id: str = "math.add") -> HarnessToolMetadata:
    return HarnessToolMetadata(
        tool_id=tool_id,
        effects=frozenset({"read"}),
        credential_audiences=(),
        idempotency="read_only",
        output_policy=ToolOutputPolicy(max_inline_bytes=1024, max_output_bytes=4096),
    )


def _tool_model(tool_name: str, args: dict[str, Any]) -> FunctionModel:
    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        returned = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        if not returned:
            yield {
                0: DeltaToolCall(
                    name=tool_name,
                    json_args=json.dumps(args),
                    tool_call_id="call-1",
                )
            }
        else:
            yield f"result={returned[-1].content}"

    return FunctionModel(stream_function=stream)


@dataclass
class _Policy:
    decision: InvocationPolicyDecision
    calls: list[tuple[str, dict[str, Any]]]

    async def __call__(self, invocation, metadata, *, context):
        del context
        self.calls.append((metadata.tool_id, dict(invocation.normalized_arguments)))
        return self.decision


@dataclass
class _InvocationPolicySubclass(InvocationPolicyCapability):
    pass


async def test_managed_tool_is_authorized_after_native_argument_validation() -> None:
    executed: list[int] = []

    def add(value: int) -> dict[str, int]:
        executed.append(value)
        return {"value": value + 1}

    policy = _Policy(InvocationPolicyDecision.allow(), [])
    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=_tool_model("add", {"value": "2"}),
        capabilities=(Capability(tools=[HarnessTool(add, harness_metadata=_metadata())], id="test-tools"),),
    )

    async with executable.stream(
        "go",
        bindings=RunBindings.local(capabilities=(InvocationPolicyCapability(evaluator=policy),)),
    ) as stream:
        items = [item async for item in stream]

    assert executed == [2]
    assert policy.calls == [("math.add", {"value": 2})]
    phases = [
        item.event.payload["phase"]
        for item in items
        if isinstance(item, HarnessEvent) and hasattr(item.event, "kind") and item.event.kind == "invocation"
    ]
    assert phases == ["prepared", "authorized", "dispatching", "completed"]
    assert items[-1].result.status == "completed"


async def test_managed_tool_without_fresh_policy_is_denied_without_dispatch() -> None:
    executed = False

    def dangerous() -> str:
        nonlocal executed
        executed = True
        return "should not run"

    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=_tool_model("dangerous", {}),
        capabilities=(
            Capability(
                tools=[HarnessTool(dangerous, harness_metadata=_metadata("dangerous"))],
                id="test-tools",
            ),
        ),
    )
    result = await executable.run("go", bindings=RunBindings.local())

    assert executed is False
    assert result.status == "completed"
    assert "authorization is unavailable" in result.output_or_raise()


async def test_unmanaged_native_tool_keeps_pydantic_semantics() -> None:
    def native(value: int) -> int:
        return value * 2

    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=_tool_model("native", {"value": 3}),
        capabilities=(Capability(tools=[native], id="test-tools"),),
    )
    result = await executable.run("go", bindings=RunBindings.local())
    assert result.status == "completed"
    assert "6" in result.output_or_raise()


async def test_programmatic_tool_manager_dispatch_crosses_the_same_boundary() -> None:
    executed: list[int] = []

    def target(value: int) -> int:
        executed.append(value)
        return value + 1

    async def proxy(ctx: RunContext[AgentContext]) -> Any:
        assert ctx.tool_manager is not None
        return await ctx.tool_manager.handle_call(
            ToolCallPart("target", {"value": 9}, tool_call_id="programmatic-1"),
            wrap_validation_errors=False,
        )

    policy = _Policy(InvocationPolicyDecision.allow(), [])
    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=_tool_model("proxy", {}),
        capabilities=(
            Capability(
                tools=[HarnessTool(target, harness_metadata=_metadata("target")), proxy],
                id="test-tools",
            ),
        ),
    )
    result = await executable.run(
        "go",
        bindings=RunBindings.local(capabilities=(InvocationPolicyCapability(evaluator=policy),)),
    )

    assert result.status == "completed"
    assert executed == [9]
    assert [call[0] for call in policy.calls] == ["target"]


async def test_managed_invocation_rejects_non_finite_programmatic_arguments() -> None:
    executed = False

    def target(value: float) -> float:
        nonlocal executed
        executed = True
        return value

    async def proxy(ctx: RunContext[AgentContext]) -> Any:
        assert ctx.tool_manager is not None
        return await ctx.tool_manager.handle_call(
            ToolCallPart("target", {"value": float("nan")}, tool_call_id="programmatic-1"),
            wrap_validation_errors=False,
        )

    policy = _Policy(InvocationPolicyDecision.allow(), [])
    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=_tool_model("proxy", {}),
        capabilities=(
            Capability(
                tools=[HarnessTool(target, harness_metadata=_metadata("target")), proxy],
                id="test-tools",
            ),
        ),
    )
    result = await executable.run(
        "go",
        bindings=RunBindings.local(capabilities=(InvocationPolicyCapability(evaluator=policy),)),
    )

    assert result.status == "completed"
    assert executed is False
    assert policy.calls == []


async def test_strict_policy_rejects_unmanaged_final_function_surface() -> None:
    def native() -> str:
        return "unsafe"

    policy = _Policy(InvocationPolicyDecision.allow(), [])
    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=_tool_model("native", {}),
        capabilities=(Capability(tools=[native], id="test-tools"),),
    )
    with pytest.raises(DefinitionError) as exc_info:
        await executable.run(
            "go",
            bindings=RunBindings.local(
                capabilities=(InvocationPolicyCapability(evaluator=policy, strict_managed_tools=True),)
            ),
        )
    assert exc_info.value.code == "unmanaged_tool_rejected"


async def test_run_bindings_reject_feature_capabilities_and_reserved_subclasses() -> None:
    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=_tool_model("unused", {}),
    )

    with pytest.raises(DefinitionError) as feature_error:
        executable.stream(
            "go",
            bindings=RunBindings.local(capabilities=(Capability(tools=[lambda: "injected"], id="injected"),)),
        )
    assert feature_error.value.code == "capability_scope_invalid"

    subclass = _InvocationPolicySubclass(
        evaluator=_Policy(InvocationPolicyDecision.allow(), []),
    )
    with pytest.raises(DefinitionError) as subclass_error:
        executable.stream(
            "go",
            bindings=RunBindings.local(capabilities=(subclass,)),
        )
    assert subclass_error.value.code == "capability_scope_invalid"

    with pytest.raises(DefinitionError) as definition_subclass_error:
        HarnessBuilder().build_code(
            AgentSpec(model="logical:test"),
            output_type=str,
            model=_tool_model("unused", {}),
            capabilities=(subclass,),
        )
    assert definition_subclass_error.value.code == "capability_scope_invalid"


async def test_run_policy_cannot_be_installed_as_stale_definition_authority() -> None:
    with pytest.raises(DefinitionError) as exc_info:
        HarnessBuilder().build_code(
            AgentSpec(model="logical:test"),
            output_type=str,
            model=_tool_model("unused", {}),
            capabilities=(InvocationPolicyCapability(evaluator=_Policy(InvocationPolicyDecision.allow(), [])),),
        )
    assert exc_info.value.code == "capability_scope_invalid"


async def test_reserved_capabilities_are_rejected_inside_nested_combined_sources() -> None:
    with pytest.raises(DefinitionError) as definition_error:
        HarnessBuilder().build_code(
            AgentSpec(model="logical:test"),
            output_type=str,
            model=_tool_model("unused", {}),
            capabilities=(
                CombinedCapability(
                    [InvocationPolicyCapability(evaluator=_Policy(InvocationPolicyDecision.allow(), []))]
                ),
            ),
        )
    assert definition_error.value.code == "capability_scope_invalid"

    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=_tool_model("unused", {}),
    )
    with pytest.raises(DefinitionError) as run_error:
        executable.stream(
            "go",
            bindings=RunBindings.local(capabilities=(CombinedCapability([ClientToolsCapability()]),)),
        )
    assert run_error.value.code == "capability_scope_invalid"


@dataclass
class _DefinitionBecomesRunPolicy(AbstractCapability[Any]):
    async def for_run(self, ctx):
        del ctx
        return InvocationPolicyCapability(evaluator=_Policy(InvocationPolicyDecision.allow(), []))


@dataclass
class _DefinitionBecomesRunPolicySubclass(AbstractCapability[Any]):
    async def for_run(self, ctx):
        del ctx
        return _InvocationPolicySubclass(
            evaluator=_Policy(InvocationPolicyDecision.allow(), []),
        )


@dataclass
class _RunBecomesClientOwner(AbstractCapability[Any]):
    async def for_run(self, ctx):
        del ctx
        return ClientToolsCapability()


async def test_for_run_replacements_cannot_change_reserved_capability_provenance() -> None:
    def managed() -> str:
        return "done"

    stale_definition = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=_tool_model("managed", {}),
        capabilities=(
            Capability(tools=[HarnessTool(managed, harness_metadata=_metadata())], id="test-tools"),
            _DefinitionBecomesRunPolicy(),
        ),
    )
    with pytest.raises(DefinitionError) as stale_policy:
        await stale_definition.run("go", bindings=RunBindings.local())
    assert stale_policy.value.code == "capability_scope_invalid"

    subclass_definition = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=_tool_model("unused", {}),
        capabilities=(_DefinitionBecomesRunPolicySubclass(),),
    )
    with pytest.raises(DefinitionError) as subclass_policy:
        await subclass_definition.run("go", bindings=RunBindings.local())
    assert subclass_policy.value.code == "capability_scope_invalid"

    runtime_owner = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=_tool_model("unused", {}),
    )
    with pytest.raises(DefinitionError) as owner_error:
        await runtime_owner.run(
            "go",
            bindings=RunBindings.local(capabilities=(_RunBecomesClientOwner(),)),
        )
    assert owner_error.value.code == "capability_scope_invalid"


async def test_native_tool_return_policy_bounds_text_and_preserves_multimodal_values() -> None:
    metadata = HarnessToolMetadata(
        tool_id="native.read",
        effects=frozenset({"read"}),
        credential_audiences=(),
        idempotency="read_only",
        output_policy=ToolOutputPolicy(max_inline_bytes=2048, max_output_bytes=4096),
    )
    binary = BinaryContent(
        b"x" * 2048,
        media_type="image/png",
        identifier="Bearer binary-secret",
        vendor_metadata={"access_token": "binary-secret"},
    )
    image = ImageUrl(
        "https://example.com/image.png?api_key=unchanged",
        vendor_metadata={"api_key": "image-secret"},
    )
    uploaded = UploadedFile(
        "file-1",
        "openai",
        media_type="image/png",
        vendor_metadata={"authorization": "Bearer uploaded-secret"},
    )
    native_items = [
        binary,
        image,
        AudioUrl("https://example.com/audio.mp3"),
        DocumentUrl("https://example.com/document.pdf"),
        VideoUrl("https://example.com/video.mp4?variant=large"),
        uploaded,
        CachePoint(ttl="1h"),
    ]
    projected = await _apply_result_policy(
        ToolReturn(
            return_value={"authorization": "Bearer return-secret"},
            content=[
                "Bearer raw-secret",
                TextContent("Bearer text-secret", metadata={"api_key": "text-secret"}),
                *native_items,
            ],
            metadata={"secret": "application-secret"},
            tools=["Bearer tool-secret"],
        ),
        metadata,
    )

    assert isinstance(projected, ToolReturn)
    assert projected.return_value == {"authorization": "[REDACTED]"}
    assert isinstance(projected.content, list)
    assert projected.content[0] == "Bearer [REDACTED]"
    text = projected.content[1]
    assert isinstance(text, TextContent)
    assert text.content == "Bearer [REDACTED]"
    assert text.metadata == {"api_key": "[REDACTED]"}
    assert projected.content[2:] == native_items
    assert all(projected.content[index + 2] is item for index, item in enumerate(native_items))
    assert binary._identifier == "Bearer binary-secret"
    assert image.url.endswith("api_key=unchanged")
    assert uploaded.vendor_metadata == {"authorization": "Bearer uploaded-secret"}
    assert projected.metadata == {"secret": "[REDACTED]"}
    assert projected.tools == ["Bearer [REDACTED]"]


async def test_native_tool_return_text_overflow_is_explicit_without_filtering_media() -> None:
    metadata = HarnessToolMetadata(
        tool_id="native.read",
        effects=frozenset({"read"}),
        credential_audiences=(),
        idempotency="read_only",
        output_policy=ToolOutputPolicy(max_inline_bytes=512, max_output_bytes=2048),
    )
    image = ImageUrl("https://example.com/image.png?api_key=preserved")

    projected = await _apply_result_policy(
        ToolReturn("ok", content=[image], tools=["x" * 1024]),
        metadata,
    )

    assert isinstance(projected, ToolReturn)
    assert isinstance(projected.content, list)
    assert projected.content[0] is image
    assert projected.tools is not None
    assert len(projected.tools[0].encode("utf-8")) <= 512
    assert "tool result truncated" in projected.tools[0]


async def test_acknowledged_result_bypasses_only_generic_inline_projection() -> None:
    policy = ToolOutputPolicy(
        max_inline_bytes=512,
        max_output_bytes=2048,
        overflow="truncate",
        redact=True,
    )
    result = acknowledge_tool_output(
        {
            "content": "x" * 1_000,
            "authorization": "Bearer private-token",
        }
    )

    projected = await _apply_result_policy(result, policy)

    assert projected == {
        "content": "x" * 1_000,
        "authorization": "[REDACTED]",
    }


async def test_acknowledged_result_can_bypass_the_byte_inline_policy() -> None:
    policy = ToolOutputPolicy(
        max_inline_bytes=512,
        max_output_bytes=64 * 1024,
        overflow="truncate",
        redact=True,
    )
    result = acknowledge_tool_output({"content": "界" * 6_000})

    projected = await _apply_result_policy(result, policy)

    assert projected == {"content": "界" * 6_000}
    assert len(tool_output_bytes(projected)) > policy.max_inline_bytes


async def test_acknowledged_result_still_obeys_final_hard_ceiling() -> None:
    policy = ToolOutputPolicy(
        max_inline_bytes=512,
        max_output_bytes=64 * 1024,
        overflow="truncate",
        redact=True,
    )

    projected = await _apply_result_policy(
        acknowledge_tool_output({"content": "x" * 25_000}),
        policy,
    )

    assert isinstance(projected, dict)
    assert projected["truncated"] is True
    assert projected["output_file_path"] is None
    assert projected["output_chars"] > FINAL_TOOL_OUTPUT_HARD_CHARS
    assert projected["output_bytes"] > FINAL_TOOL_OUTPUT_HARD_CHARS
    assert len(tool_output_text(projected)) <= FINAL_TOOL_OUTPUT_HARD_CHARS
    assert len(tool_output_bytes(projected)) <= policy.max_inline_bytes


async def test_unacknowledged_result_still_obeys_final_character_ceiling() -> None:
    policy = ToolOutputPolicy(
        max_inline_bytes=256 * 1024,
        max_output_bytes=256 * 1024,
        overflow="truncate",
        redact=True,
    )

    projected = await _apply_result_policy({"content": "x" * 25_000}, policy)

    assert isinstance(projected, dict)
    assert projected["truncated"] is True
    assert projected["output_chars"] > FINAL_TOOL_OUTPUT_HARD_CHARS
    assert len(tool_output_text(projected)) <= FINAL_TOOL_OUTPUT_HARD_CHARS
    assert len(tool_output_bytes(projected)) <= policy.max_inline_bytes


async def test_large_json_spill_without_sink_returns_bounded_structured_preview() -> None:
    metadata = HarnessToolMetadata(
        tool_id="result.preview",
        effects=frozenset({"read"}),
        credential_audiences=(),
        idempotency="read_only",
        output_policy=ToolOutputPolicy(
            max_inline_bytes=512,
            max_output_bytes=2048,
            overflow="spill",
        ),
    )

    projected = await _apply_result_policy(
        {"content": "x" * 1_000, "hint": "continue"},
        metadata,
    )

    assert isinstance(projected, dict)
    assert projected["truncated"] is True
    assert projected["output_file_path"] is None
    assert projected["output_bytes"] > 1_000
    assert isinstance(projected["result"], dict)
    assert projected["result"]["hint"] == "continue"
    assert len(json.dumps(projected).encode("utf-8")) <= 512


async def test_duplicate_managed_identity_fails_before_model_request() -> None:
    model_called = False

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        nonlocal model_called
        del messages, info
        model_called = True
        yield "done"

    def one() -> str:
        return "one"

    def two() -> str:
        return "two"

    executable = HarnessBuilder().build_code(
        AgentSpec(model="logical:test"),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(
            Capability(
                tools=[
                    HarnessTool(one, harness_metadata=_metadata("duplicate")),
                    HarnessTool(two, harness_metadata=_metadata("duplicate")),
                ],
                id="test-tools",
            ),
        ),
    )
    with pytest.raises(DefinitionError) as exc_info:
        await executable.run("go", bindings=RunBindings.local())
    assert exc_info.value.code == "managed_tool_id_duplicate"
    assert model_called is False


@dataclass
class _CompetingBoundary(AbstractCapability[Any]):
    def get_ordering(self) -> CapabilityOrdering:
        from a13n_harness.tools.invocation import ToolExecutionBoundaryCapability

        return CapabilityOrdering(position="outermost", wraps=(ToolExecutionBoundaryCapability,))


async def test_competing_outer_boundary_creates_a_fail_closed_ordering_cycle() -> None:
    with pytest.raises(DefinitionError) as exc_info:
        HarnessBuilder().build_code(
            AgentSpec(model="logical:test"),
            output_type=str,
            model=_tool_model("unused", {}),
            capabilities=(_CompetingBoundary(),),
        )
    assert exc_info.value.code == "agent_build_failed"
    assert exc_info.value.__cause__ is not None
    assert "Circular ordering constraints" in str(exc_info.value.__cause__)
