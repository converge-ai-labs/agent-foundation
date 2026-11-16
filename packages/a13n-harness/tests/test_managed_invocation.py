from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from typing import Any

import pytest
from a13n_harness import (
    AgentContext,
    HarnessBuilder,
    HarnessEvent,
    RunBindings,
)
from a13n_harness.errors import DefinitionError
from a13n_harness.tools import (
    ClientToolsCapability,
    HarnessTool,
    HarnessToolMetadata,
    InvocationPolicyCapability,
    InvocationPolicyDecision,
    ToolOutputPolicy,
)
from a13n_harness.tools._output import _apply_result_policy
from a13n_harness.toolsets import (
    FINAL_TOOL_OUTPUT_HARD_CHARS,
    acknowledge_tool_output,
    tool_output_bytes,
    tool_output_text,
)
from pydantic import field_serializer, field_validator
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


@dataclass
class _HexPayload:
    data: bytes

    @field_validator("data", mode="before")
    @classmethod
    def decode(cls, value: str | bytes) -> bytes:
        return bytes.fromhex(value) if isinstance(value, str) else value

    @field_serializer("data", when_used="json")
    def encode(self, value: bytes) -> str:
        return value.hex()


@pytest.mark.parametrize("managed", [False, True])
async def test_tool_argument_projection_preserves_nested_dataclass_serializers(managed: bool) -> None:
    executed: list[bytes] = []
    resolved: list[bytes] = []

    def consume(payloads: list[_HexPayload]) -> str:
        executed.extend(payload.data for payload in payloads)
        return "accepted"

    async def resources(args, *, context):
        del context
        resolved.extend(payload.data for payload in args["payloads"])
        return ()

    metadata = replace(_metadata("consume"), resource_resolver=resources)
    policy = _Policy(InvocationPolicyDecision.allow(), [])
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=_tool_model("consume", {"payloads": [{"data": "ff"}]}),
        capabilities=(
            Capability(
                tools=[HarnessTool(consume, harness_metadata=metadata) if managed else consume], id="test-tools"
            ),
        ),
    )
    result = await executable.run(
        "go", bindings=RunBindings.embedded(capabilities=(InvocationPolicyCapability(evaluator=policy),))
    )
    assert result.status == "completed"
    assert executed == [b"\xff"]
    assert "accepted" in result.output_or_raise()
    assert resolved == ([b"\xff"] if managed else [])
    assert policy.calls == ([("consume", {"payloads": [{"data": "ff"}]})] if managed else [])


async def test_managed_tool_is_authorized_after_native_argument_validation() -> None:
    executed: list[int] = []

    def add(value: int) -> dict[str, int]:
        executed.append(value)
        return {"value": value + 1}

    policy = _Policy(InvocationPolicyDecision.allow(), [])
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=_tool_model("add", {"value": "2"}),
        capabilities=(Capability(tools=[HarnessTool(add, harness_metadata=_metadata())], id="test-tools"),),
    )

    async with executable.stream(
        "go",
        bindings=RunBindings.embedded(capabilities=(InvocationPolicyCapability(evaluator=policy),)),
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


async def test_managed_tool_without_fresh_policy_defaults_to_allow() -> None:
    executed = False

    def managed() -> str:
        nonlocal executed
        executed = True
        return "executed"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=_tool_model("managed", {}),
        capabilities=(
            Capability(
                tools=[HarnessTool(managed, harness_metadata=_metadata("managed"))],
                id="test-tools",
            ),
        ),
    )
    result = await executable.run("go", bindings=RunBindings.embedded())

    assert executed is True
    assert result.status == "completed"
    assert "executed" in result.output_or_raise()


async def test_explicit_invocation_policy_can_deny_managed_tool_dispatch() -> None:
    executed = False

    def dangerous() -> str:
        nonlocal executed
        executed = True
        return "should not run"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=_tool_model("dangerous", {}),
        capabilities=(
            Capability(
                tools=[HarnessTool(dangerous, harness_metadata=_metadata("dangerous"))],
                id="test-tools",
            ),
        ),
    )
    result = await executable.run(
        "go",
        bindings=RunBindings.embedded(
            capabilities=(
                InvocationPolicyCapability(
                    evaluator=_Policy(InvocationPolicyDecision.deny("blocked"), []),
                ),
            ),
        ),
    )

    assert executed is False
    assert result.status == "completed"
    error = json.loads(result.output_or_raise().removeprefix("result="))["error"]
    assert error == {
        "source": "system",
        "code": "tool_denied",
        "message": "Managed tool invocation was denied.",
        "details": {},
    }


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
    executable = HarnessBuilder().build(
        AgentSpec(),
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
        bindings=RunBindings.embedded(capabilities=(InvocationPolicyCapability(evaluator=policy),)),
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
    executable = HarnessBuilder().build(
        AgentSpec(),
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
        bindings=RunBindings.embedded(capabilities=(InvocationPolicyCapability(evaluator=policy),)),
    )

    assert result.status == "completed"
    assert executed is False
    assert policy.calls == []


async def test_strict_policy_rejects_unmanaged_final_function_surface() -> None:
    def native() -> str:
        return "unsafe"

    policy = _Policy(InvocationPolicyDecision.allow(), [])
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=_tool_model("native", {}),
        capabilities=(Capability(tools=[native], id="test-tools"),),
    )
    with pytest.raises(DefinitionError) as exc_info:
        await executable.run(
            "go",
            bindings=RunBindings.embedded(
                capabilities=(InvocationPolicyCapability(evaluator=policy, strict_managed_tools=True),)
            ),
        )
    assert exc_info.value.code == "unmanaged_tool_rejected"


async def test_run_bindings_reject_feature_capabilities_and_reserved_subclasses() -> None:
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=_tool_model("unused", {}),
    )

    with pytest.raises(DefinitionError) as feature_error:
        executable.stream(
            "go",
            bindings=RunBindings.embedded(capabilities=(Capability(tools=[lambda: "injected"], id="injected"),)),
        )
    assert feature_error.value.code == "capability_scope_invalid"

    subclass = _InvocationPolicySubclass(
        evaluator=_Policy(InvocationPolicyDecision.allow(), []),
    )
    with pytest.raises(DefinitionError) as subclass_error:
        executable.stream(
            "go",
            bindings=RunBindings.embedded(capabilities=(subclass,)),
        )
    assert subclass_error.value.code == "capability_scope_invalid"

    with pytest.raises(DefinitionError) as definition_subclass_error:
        HarnessBuilder().build(
            AgentSpec(),
            output_type=str,
            model=_tool_model("unused", {}),
            capabilities=(subclass,),
        )
    assert definition_subclass_error.value.code == "capability_scope_invalid"


async def test_run_policy_cannot_be_installed_as_stale_definition_authority() -> None:
    with pytest.raises(DefinitionError) as exc_info:
        HarnessBuilder().build(
            AgentSpec(),
            output_type=str,
            model=_tool_model("unused", {}),
            capabilities=(InvocationPolicyCapability(evaluator=_Policy(InvocationPolicyDecision.allow(), [])),),
        )
    assert exc_info.value.code == "capability_scope_invalid"


async def test_reserved_capabilities_are_rejected_inside_nested_combined_sources() -> None:
    with pytest.raises(DefinitionError) as definition_error:
        HarnessBuilder().build(
            AgentSpec(),
            output_type=str,
            model=_tool_model("unused", {}),
            capabilities=(
                CombinedCapability(
                    [InvocationPolicyCapability(evaluator=_Policy(InvocationPolicyDecision.allow(), []))]
                ),
            ),
        )
    assert definition_error.value.code == "capability_scope_invalid"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=_tool_model("unused", {}),
    )
    with pytest.raises(DefinitionError) as run_error:
        executable.stream(
            "go",
            bindings=RunBindings.embedded(capabilities=(CombinedCapability([ClientToolsCapability()]),)),
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

    stale_definition = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=_tool_model("managed", {}),
        capabilities=(
            Capability(tools=[HarnessTool(managed, harness_metadata=_metadata())], id="test-tools"),
            _DefinitionBecomesRunPolicy(),
        ),
    )
    with pytest.raises(DefinitionError) as stale_policy:
        await stale_definition.run("go", bindings=RunBindings.embedded())
    assert stale_policy.value.code == "capability_scope_invalid"

    subclass_definition = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=_tool_model("unused", {}),
        capabilities=(_DefinitionBecomesRunPolicySubclass(),),
    )
    with pytest.raises(DefinitionError) as subclass_policy:
        await subclass_definition.run("go", bindings=RunBindings.embedded())
    assert subclass_policy.value.code == "capability_scope_invalid"

    runtime_owner = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=_tool_model("unused", {}),
    )
    with pytest.raises(DefinitionError) as owner_error:
        await runtime_owner.run(
            "go",
            bindings=RunBindings.embedded(capabilities=(_RunBecomesClientOwner(),)),
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
        metadata.output_policy,
    )

    assert isinstance(projected, ToolReturn)
    assert projected.return_value == {"authorization": "[REDACTED]"}
    assert isinstance(projected.content, list)
    assert projected.content[0] == "Bearer [REDACTED]"
    text = projected.content[1]
    assert isinstance(text, TextContent)
    assert text.content == "Bearer [REDACTED]"
    assert text.metadata == {"api_key": "[REDACTED]"}
    for original, detached in zip(native_items[:-1], projected.content[2:-1], strict=True):
        assert detached is original
        assert detached.vendor_metadata == original.vendor_metadata
    assert projected.content[-1] is native_items[-1]
    assert projected.content[2].data is binary.data
    assert projected.content[3].url == image.url
    assert binary._identifier == "Bearer binary-secret"
    assert image.url.endswith("api_key=unchanged")
    assert uploaded.vendor_metadata == {"authorization": "Bearer uploaded-secret"}
    assert projected.metadata == {"secret": "[REDACTED]"}
    assert projected.tools == ["Bearer [REDACTED]"]


@pytest.mark.parametrize("configured", [True, False, "secret-value", {"api_key": "secret-value"}, [True], 1, None])
async def test_tool_result_preserves_only_boolean_credential_status(configured) -> None:
    body = {
        "credential_configured": configured,
        "credential": {"api_key": "private-key"},
        "api_key": "private-key",
        "authorization": "Bearer private-key",
        "password": "private-password",
        "token": "private-token",
        "nested": [{"credential_configured": False, "credential": True}],
    }
    projected = await _apply_result_policy({"status": 200, "body": body}, _metadata().output_policy)

    assert projected == {
        "status": 200,
        "body": {
            "credential_configured": configured if isinstance(configured, bool) else "[REDACTED]",
            "credential": "[REDACTED]",
            "api_key": "[REDACTED]",
            "authorization": "[REDACTED]",
            "password": "[REDACTED]",
            "token": "[REDACTED]",
            "nested": [{"credential_configured": False, "credential": "[REDACTED]"}],
        },
    }
    assert body["credential"] == {"api_key": "private-key"}


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
        metadata.output_policy,
    )

    assert isinstance(projected, ToolReturn)
    assert isinstance(projected.content, list)
    assert projected.content[0] is image
    assert projected.content[0].url == image.url
    assert projected.content[0].vendor_metadata is None
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

    executable = HarnessBuilder().build(
        AgentSpec(),
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
        await executable.run("go", bindings=RunBindings.embedded())
    assert exc_info.value.code == "managed_tool_id_duplicate"
    assert model_called is False


@dataclass
class _CompetingBoundary(AbstractCapability[Any]):
    def get_ordering(self) -> CapabilityOrdering:
        from a13n_harness.tools.invocation import ToolExecutionBoundaryCapability

        return CapabilityOrdering(position="outermost", wraps=(ToolExecutionBoundaryCapability,))


async def test_competing_outer_boundary_creates_a_fail_closed_ordering_cycle() -> None:
    with pytest.raises(DefinitionError) as exc_info:
        HarnessBuilder().build(
            AgentSpec(),
            output_type=str,
            model=_tool_model("unused", {}),
            capabilities=(_CompetingBoundary(),),
        )
    assert exc_info.value.code == "agent_build_failed"
    assert exc_info.value.__cause__ is not None
    assert "Circular ordering constraints" in str(exc_info.value.__cause__)


async def test_failed_delivery_after_write_does_not_replay_the_tool():
    writes = []

    def write() -> str:
        writes.append("committed")
        return "x" * 2048

    metadata = replace(
        _metadata("write"),
        effects=frozenset({"write"}),
        idempotency="none",
        output_policy=ToolOutputPolicy(max_inline_bytes=512, max_output_bytes=4096, overflow="fail"),
    )
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=_tool_model("write", {}),
        capabilities=(Capability(tools=[HarnessTool(write, harness_metadata=metadata)], id="test-tools"),),
    )
    result = await executable.run("go", bindings=RunBindings.embedded())
    assert writes == ["committed"]
    error = json.loads(result.output_or_raise().removeprefix("result="))["error"]
    assert error["source"] == "system"
    assert error["code"] == "tool_result_too_large"
    assert "512 inline bytes" in error["message"]
    assert "before repeating" in error["details"]["hint"]


@pytest.mark.parametrize("stage", ["validator", "execution", "schema"])
@pytest.mark.parametrize("delivery", ["reference", "inline", "markerless", "unmapped"])
async def test_model_retry_guidance_preserves_native_results_and_retry_budget(stage, delivery) -> None:
    from pydantic_ai import ModelRetry, Tool
    from pydantic_ai.exceptions import ToolRetryError

    observed = []
    calls = []

    def validate(ctx, value: int) -> None:
        if stage == "validator" and value == 1:
            raise ModelRetry("Choose another value")

    def lookup(value: int) -> int:
        calls.append(value)
        if stage == "execution" and value == 1:
            raise ModelRetry("Choose another value")
        return value

    class Observe(AbstractCapability):
        async def wrap_tool_execute(self, ctx, *, call, tool_def, args, handler):
            try:
                return await handler(args)
            except ToolRetryError as error:
                observed.append(error.tool_retry)
                raise

    async def model(messages, info):
        returns = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        if returns and returns[-1].outcome == "success":
            yield "done"
        else:
            value = 2 if returns else ("invalid" if stage == "schema" else 1)
            yield {
                0: DeltaToolCall(
                    name="lookup", json_args=json.dumps({"value": value}), tool_call_id=f"lookup-{len(returns)}"
                )
            }

    page = "web" if delivery == "markerless" else "files"
    metadata = {} if delivery == "unmapped" else {"a13n.guidance": page}
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(
            Capability(tools=[Tool(lookup, args_validator=validate, max_retries=1, metadata=metadata)]),
            Observe(),
        ),
    )
    result = await executable.run(
        "go",
        bindings=RunBindings.embedded(instruction_reference_root=None if delivery == "inline" else "/release-guidance"),
    )
    assert result.output_or_raise() == "done"
    retry = next(
        part
        for message in result.all_messages()
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart) and part.outcome == "retried"
    )
    assert retry.tool_call_id == "lookup-0" and retry.tool_name == "lookup"
    assert retry.retry.attempt == 1 and retry.retry.max_retries == 1
    if stage == "schema":
        assert retry.retry.cause == "validation_error"
        assert isinstance(retry.content, list)
        assert retry.content == [
            {
                "type": "int_parsing",
                "loc": ["value"],
                "msg": "Input should be a valid integer, unable to parse string as an integer",
                "input": "invalid",
            }
        ]
        assert calls == [2]
    else:
        assert retry.retry.cause == "model_retry"
        hint = "Guidance: `/release-guidance/harness/files.md`. Read with view before deciding whether to retry."
        assert retry.content == "Choose another value" + ("\n" + hint if delivery == "reference" else "")
        if stage == "execution":
            assert len(observed) == 1
            assert replace(observed[0], content=retry.content) == retry
        assert calls == ([2] if stage == "validator" else [1, 2])


@pytest.mark.parametrize("failure", ["failed", "denied"])
async def test_guidance_does_not_change_terminal_failures(failure) -> None:
    from a13n_harness.tools import ToolPermissions, ToolPermissionsCapability
    from pydantic_ai import Tool
    from pydantic_ai.exceptions import ToolFailed

    def lookup() -> str:
        raise ToolFailed("Keep this terminal message")

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=_tool_model("lookup", {}),
        capabilities=(
            Capability(tools=[Tool(lookup, metadata={"a13n.guidance": "files"})]),
            ToolPermissionsCapability(ToolPermissions(default="deny" if failure == "denied" else "allow")),
        ),
    )
    result = await executable.run("go", bindings=RunBindings.embedded(instruction_reference_root="/release-guidance"))
    assert result.output_or_raise()
    part = next(
        part
        for message in result.all_messages()
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    )
    assert part.outcome == "failed"
    assert "Guidance:" not in str(part.content)
    if failure == "failed":
        assert part.content == "Keep this terminal message"
    else:
        assert "Tool invocation is denied" in str(part.content)


@pytest.mark.parametrize("structured", [False, True])
async def test_guidance_rebuild_preserves_all_native_retry_fields(structured) -> None:
    from copy import deepcopy
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from a13n_harness.tools.invocation import ToolExecutionBoundaryCapability
    from pydantic_ai import ToolDefinition
    from pydantic_ai.exceptions import ToolRetryError
    from pydantic_ai.messages import RetryInfo

    content = (
        [{"loc": ["value"], "type": "value_error", "msg": "Choose another value"}]
        if structured
        else "Choose another value"
    )
    original = ToolReturnPart(
        tool_name="actual_target",
        content=content,
        tool_call_id="original-call",
        tool_kind="tool_search",
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        metadata={"retain": {"value": 1}},
        outcome="retried",
        retry=RetryInfo(cause="model_retry", attempt=2, max_retries=3),
    )
    error = ToolRetryError(original)

    async def handler(args):
        raise error

    ctx = SimpleNamespace(deps=SimpleNamespace(instruction_reference_root="/release-guidance"))
    with pytest.raises(ToolRetryError) as raised:
        await ToolExecutionBoundaryCapability().wrap_tool_execute(
            ctx,
            call=ToolCallPart("envelope", {}, tool_call_id="envelope-call"),
            tool_def=ToolDefinition(name="envelope", metadata={"a13n.guidance": "files"}),
            args={},
            handler=handler,
        )
    part = raised.value.tool_retry
    expected = (
        content
        if structured
        else (
            "Choose another value\nGuidance: `/release-guidance/harness/files.md`. "
            "Read with view before deciding whether to retry."
        )
    )
    assert part == replace(original, content=expected)
    assert deepcopy(part) == part
    if structured:
        assert raised.value is error
    else:
        from a13n_harness.guidance import retry_guidance_page

        assert raised.value.__cause__ is error
        assert retry_guidance_page(ToolRetryError(deepcopy(part))) == "files"
