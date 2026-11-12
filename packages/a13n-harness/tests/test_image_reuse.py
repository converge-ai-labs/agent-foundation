from __future__ import annotations

import base64
import json
import re
from collections.abc import AsyncIterator

import pytest
from a13n_harness import DeferredToolResume, HarnessBuilder, HarnessState, RunBindings
from a13n_harness.capabilities import ImageReuseCapability, ImageReuseLimits
from a13n_harness.tools import (
    HarnessTool,
    HarnessToolMetadata,
    InvocationPolicyCapability,
    InvocationPolicyDecision,
    ToolOutputPolicy,
)
from pydantic_ai import BinaryContent
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.capabilities import Capability
from pydantic_ai.messages import ModelMessage, ModelRequest, TextContent, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel

pytestmark = pytest.mark.anyio
_DATA = b"synthetic private image bytes\x00\xff" * 4096


def _refs(messages: list[ModelMessage]) -> list[str]:
    # The model receives the directory, never the encoded payload.
    text = "\n".join(
        item if isinstance(item, str) else item.content
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart)
        for item in ([part.content] if isinstance(part.content, str) else part.content)
        if isinstance(item, (str, TextContent))
    )
    assert base64.b64encode(_DATA).decode() not in text
    return list(dict.fromkeys(re.findall(r"image-base64:[0-9a-f]{32}", text)))


def _model(*, producer: bool = False, transform=None) -> FunctionModel:
    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        returns = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        if producer and not returns:
            yield {0: DeltaToolCall(name="produce", json_args="{}", tool_call_id="source-1")}
        elif not any(part.tool_name == "consume" for part in returns):
            refs = _refs(messages)
            assert refs
            value = transform(refs) if transform else refs[-1]
            yield {0: DeltaToolCall(name="consume", json_args=json.dumps({"image": value}), tool_call_id="sink-1")}
        else:
            yield "done"

    return FunctionModel(stream_function=stream)


def _build(*, producer=False, approval=False, limits=None, model=None, validate=None, capabilities=()):
    sources = []
    sinks = []

    def produce():
        sources.append(True)
        return BinaryContent(_DATA, media_type="image/png", identifier="same-short-id")

    def consume(image: str) -> str:
        sinks.append(base64.b64decode(image, validate=True))
        return "accepted"

    tool = HarnessTool(
        consume,
        harness_metadata=HarnessToolMetadata(
            tool_id="image.consume",
            effects=frozenset({"write"}),
            credential_audiences=(),
            idempotency="none",
            output_policy=ToolOutputPolicy(max_inline_bytes=1024, max_output_bytes=4096),
        ),
        requires_approval=approval,
        args_validator=validate,
    )
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=model or _model(producer=producer),
        capabilities=(ImageReuseCapability(limits), Capability(tools=[tool, produce], id="image-test"), *capabilities),
    )
    return executable, sources, sinks


async def test_direct_context_image_is_bound_byte_exact_without_model_base64() -> None:
    executable, sources, sinks = _build()
    policy = []

    async def evaluate(invocation, metadata, *, context):
        policy.append(invocation)
        assert base64.b64encode(_DATA).decode() not in str(invocation.normalized_arguments)
        assert invocation.normalized_arguments["image"]["image_binding"]["sha256"]
        return InvocationPolicyDecision.allow()

    result = await executable.run(
        ["send this image", BinaryContent(_DATA, media_type="image/png")],
        bindings=RunBindings.embedded(capabilities=(InvocationPolicyCapability(evaluator=evaluate),)),
    )
    assert result.status == "completed", result
    assert sources == [] and sinks == [_DATA]
    assert len(policy) == 1
    calls = [call for message in result.state.message_history for call in getattr(message, "tool_calls", ())]
    assert calls[0].args_as_dict()["image"].startswith("image-base64:")


async def test_native_tool_image_is_bound_byte_exact_and_source_sink_execute_once() -> None:
    executable, sources, sinks = _build(producer=True)
    result = await executable.run("copy the produced image")
    assert result.status == "completed", result
    assert sources == [True] and sinks == [_DATA]


async def test_image_approval_resume_preserves_reference_but_not_general_cross_run_access() -> None:
    executable, sources, sinks = _build(approval=True)
    first = await executable.run([BinaryContent(_DATA, media_type="image/png")])
    assert first.status == "suspended", first
    assert not sinks
    assert first.deferred is not None and first.state is not None
    assert base64.b64encode(_DATA).decode() not in json.dumps(first.deferred.metadata)
    restored = HarnessState.model_validate_json(first.state.model_dump_json())
    second = await executable.run(
        previous_state=restored,
        deferred_resume=DeferredToolResume(first.deferred, first.deferred.build_results(approve_all=True)),
    )
    assert second.status == "completed", second
    assert sources == [] and sinks == [_DATA]


async def test_identical_short_ids_keep_distinct_input_part_references() -> None:
    seen = []

    async def stream(messages, info):
        refs = _refs(messages)
        seen.extend(refs)
        yield "done"

    executable, _, _ = _build(model=FunctionModel(stream_function=stream))
    result = await executable.run(
        [
            BinaryContent(b"first", media_type="image/png", identifier="abcdef"),
            BinaryContent(b"second", media_type="image/png", identifier="abcdef"),
        ]
    )
    assert result.status == "completed"
    assert len(seen) == 2 and seen[0] != seen[1]
    images = result.state.agent_context_state.entries["a13n.image-reuse"].data["images"]
    assert [image["source"]["content_path"] for image in images] == [[0], [1]]


async def test_retention_limit_reports_omission_without_replaying_producer() -> None:
    executable, sources, _ = _build(
        producer=True,
        limits=ImageReuseLimits(max_image_bytes=1),
        model=FunctionModel(stream_function=_produce_then_finish),
    )
    result = await executable.run("produce")
    assert result.status == "completed"
    assert sources == [True]
    state = result.state.agent_context_state.entries["a13n.image-reuse"].data
    assert state["images"] == [] and state["omitted"] >= 1


async def _produce_then_finish(messages, info):
    if not any(
        isinstance(message, ModelRequest) and any(isinstance(part, ToolReturnPart) for part in message.parts)
        for message in messages
    ):
        yield {0: DeltaToolCall(name="produce", json_args="{}", tool_call_id="source-1")}
    else:
        yield "done"


def _single_call_model(arguments):
    calls = 0

    async def stream(messages, info):
        nonlocal calls
        calls += 1
        if calls == 1:
            value = arguments(messages) if callable(arguments) else arguments
            yield {0: DeltaToolCall(name="consume", json_args=json.dumps(value), tool_call_id="isolated-sink")}
        else:
            yield "done"

    return FunctionModel(stream_function=stream)


@pytest.mark.parametrize("fork", [False, True])
async def test_old_reference_is_rejected_in_new_run_or_fork(fork) -> None:
    executable, _, _ = _build(approval=True)
    first = await executable.run([BinaryContent(_DATA, media_type="image/png")])
    reference = first.deferred.approvals[0].target_call.args_as_dict()["image"]
    state = first.state.fork() if fork else first.state
    fresh, _, sinks = _build(model=_single_call_model({"image": reference}))
    result = await fresh.run("new request", previous_state=state)
    assert result.status == "completed"
    assert not sinks
    assert "unavailable in this Run" in str(result.state.message_history)


async def test_unknown_reference_and_embedded_reference_are_not_silently_substituted() -> None:
    executable, _, sinks = _build(model=_single_call_model({"image": "image-base64:unknown"}))
    result = await executable.run("go")
    assert result.status == "completed" and not sinks
    assert "unavailable in this Run" in str(result.state.message_history)


async def test_target_custom_validator_sees_original_payload_but_failure_is_payload_free() -> None:
    from pydantic_ai import ModelRetry

    validated = []

    def validate(ctx, image):
        validated.append(base64.b64decode(image))
        raise ModelRetry(f"rejected: {image}")

    executable, _, sinks = _build(
        validate=validate,
        model=_single_call_model(lambda messages: {"image": _refs(messages)[-1]}),
    )
    result = await executable.run([BinaryContent(_DATA, media_type="image/png")])
    assert result.status == "completed" and validated == [_DATA] and not sinks
    assert "failed the target tool's validation" in str(result.state.message_history)
    text = str(result.state.message_history)
    assert base64.b64encode(_DATA).decode() not in text


async def test_policy_deny_stops_image_bound_dispatch() -> None:
    async def deny(invocation, metadata, *, context):
        assert invocation.normalized_arguments["image"]["image_binding"]["size_bytes"] == len(_DATA)
        return InvocationPolicyDecision.deny("denied for test")

    executable, _, sinks = _build()
    result = await executable.run(
        [BinaryContent(_DATA, media_type="image/png")],
        bindings=RunBindings.embedded(capabilities=(InvocationPolicyCapability(evaluator=deny),)),
    )
    assert result.status == "completed" and not sinks


async def test_reviewer_sees_image_digest_and_provenance_without_base64() -> None:
    from a13n_harness.capabilities import ToolReviewAssessment, ToolReviewConfig, ToolReviewResult, ToolRiskLevel
    from a13n_harness.tools import ToolPermissions, ToolPermissionsCapability

    requests = []

    class Reviewer:
        async def review(self, request, *, context):
            requests.append(request)
            assert base64.b64encode(_DATA).decode() not in request.to_prompt()
            assert request.arguments["image"]["image_binding"]["source"]["kind"] == "input"
            return ToolReviewResult(assessment=ToolReviewAssessment(risk=ToolRiskLevel.HIGH, reason="test deny"))

    executable, _, sinks = _build(
        capabilities=(
            ToolPermissionsCapability(
                ToolPermissions(rules={"image.consume": "review"}),
                review=ToolReviewConfig(model="mock-review", risk_threshold=ToolRiskLevel.HIGH, on_flagged="deny"),
                reviewer=Reviewer(),
            ),
        )
    )
    result = await executable.run([BinaryContent(_DATA, media_type="image/png")])
    assert result.status == "completed" and not sinks and len(requests) == 1


async def test_native_binary_state_uses_existing_store_dedup_and_resumes_approval() -> None:
    from hashlib import sha256

    from a13n_harness import StateStore, StoredRef

    saved = {}

    class Store(StateStore):
        async def save(self, data, kind):
            key = f"{kind}/{sha256(data).hexdigest()}"
            saved[key] = data
            return StoredRef(key=key, digest=sha256(data).hexdigest(), size=len(data))

        async def load(self, ref):
            return saved[ref.key]

    store = Store(content_threshold=1)
    executable, _, sinks = _build(approval=True)
    first = await executable.run(
        [BinaryContent(_DATA, media_type="image/png")],
        bindings=RunBindings.embedded(state_store=store),
    )
    assert len(first.state.refs) == 1
    assert list(saved.values()) == [_DATA]
    second = await executable.run(
        previous_state=HarnessState.model_validate_json(first.state.to_json_bytes()),
        bindings=RunBindings.embedded(state_store=store),
        deferred_resume=DeferredToolResume(first.deferred, first.deferred.build_results(approve_all=True)),
    )
    assert second.status == "completed" and sinks == [_DATA]
    assert len(saved) == 1


async def test_codeact_native_tool_result_becomes_same_reference_before_json_conversion() -> None:
    from a13n_harness.capabilities import CodeActCapability
    from a13n_harness.toolsets import CodeActPolicyToolset, CodeActToolPolicy
    from pydantic_ai import FunctionToolset

    sources, sinks = [], []

    def produce():
        sources.append(True)
        return BinaryContent(b"private synthetic bytes", media_type="image/png")

    def consume(image: str):
        sinks.append(base64.b64decode(image, validate=True))
        return "accepted"

    async def stream(messages, info):
        if not any(
            isinstance(message, ModelRequest) and any(isinstance(part, ToolReturnPart) for part in message.parts)
            for message in messages
        ):
            yield {
                0: DeltaToolCall(
                    name="run_code",
                    json_args=json.dumps(
                        {"code": "image = await produce()\nawait consume(image=image['base64_reference'])"}
                    ),
                    tool_call_id="code-1",
                )
            }
        else:
            yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(
            ImageReuseCapability(),
            CodeActCapability(),
            Capability(
                toolsets=[CodeActPolicyToolset(FunctionToolset([produce, consume]), CodeActToolPolicy(default=True))]
            ),
        ),
    )
    result = await executable.run("copy image with code")
    assert result.status == "completed"
    assert sources == [True] and sinks == [b"private synthetic bytes"]
    images = result.state.agent_context_state.entries["a13n.image-reuse"].data["images"]
    assert len(images) == 1
    assert images[0]["source"]["tool_call_id"] != "code-1"


async def test_same_run_compaction_keeps_original_image_reusable_after_history_replacement() -> None:
    from a13n_harness.capabilities import CompactionCapability, CompactionPolicy
    from pydantic_ai.messages import ModelResponse, TextPart
    from pydantic_ai.usage import RequestUsage

    calls = 0
    chosen = []

    async def stream(messages, info):
        nonlocal calls
        calls += 1
        if calls == 1:
            # This is the real nested compaction request.
            yield "The original private image still needs to be sent."
        elif calls == 2:
            references = _refs(messages)
            assert references
            chosen.append(references[-1])
            yield {
                0: DeltaToolCall(
                    name="consume", json_args=json.dumps({"image": references[-1]}), tool_call_id="after-compaction"
                )
            }
        else:
            yield "done"

    previous = HarnessState.new(
        message_history=(
            ModelRequest(parts=[UserPromptPart(["copy this", BinaryContent(_DATA, media_type="image/png")])]),
            ModelResponse(
                parts=[TextPart("Need to send it next.")], usage=RequestUsage(input_tokens=2100, output_tokens=100)
            ),
        )
    )
    executable, _, sinks = _build(
        model=FunctionModel(stream_function=stream),
        capabilities=(CompactionCapability(CompactionPolicy(trigger_tokens=2000)),),
    )
    result = await executable.run("continue", previous_state=previous)
    assert result.status == "completed" and calls == 3 and sinks == [_DATA]
    images = result.state.agent_context_state.entries["a13n.image-reuse"].data["images"]
    assert any(image["reference"] == chosen[0] for image in images)


async def test_url_only_input_is_not_fetched_or_issued_a_byte_reference() -> None:
    from pydantic_ai import ImageUrl

    async def stream(messages, info):
        assert _refs(messages) == []
        yield "done"

    executable, _, sinks = _build(model=FunctionModel(stream_function=stream))
    result = await executable.run([ImageUrl("https://unreachable.invalid/private.png")])
    assert result.status == "completed" and not sinks
    assert result.state.agent_context_state.entries["a13n.image-reuse"].data["images"] == []


async def test_nested_native_arguments_are_payload_free_in_review_and_approval() -> None:
    from a13n_harness.capabilities import ToolReviewAssessment, ToolReviewConfig, ToolReviewResult, ToolRiskLevel
    from a13n_harness.tools import ToolPermissions, ToolPermissionsCapability
    from pydantic import BaseModel, Field

    class ImageArg(BaseModel):
        image: str = Field(alias="encoded_image")

    requests, sinks = [], []

    class Reviewer:
        async def review(self, request, *, context):
            requests.append(request)
            assert base64.b64encode(_DATA).decode() not in request.to_prompt()
            assert request.arguments["body"]["image"]["image_binding"]["source"]["kind"] == "input"
            return ToolReviewResult(assessment=ToolReviewAssessment(risk=ToolRiskLevel.HIGH, reason="ask"))

    def consume(body):
        sinks.append(base64.b64decode(body.image))
        return "accepted"

    # Resolve the local model type explicitly with postponed annotations enabled.
    consume.__annotations__["body"] = ImageArg
    from pydantic_ai import Tool

    tool = Tool(consume)
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=_single_call_model(lambda messages: {"body": {"encoded_image": _refs(messages)[-1]}}),
        capabilities=(
            ImageReuseCapability(),
            Capability(tools=[tool]),
            ToolPermissionsCapability(
                ToolPermissions(rules={"*": "review"}),
                review=ToolReviewConfig(
                    model="mock", risk_threshold=ToolRiskLevel.HIGH, on_flagged="approval_required"
                ),
                reviewer=Reviewer(),
            ),
        ),
    )
    result = await executable.run([BinaryContent(_DATA, media_type="image/png")])
    assert result.status == "suspended" and not sinks and len(requests) == 1
    assert base64.b64encode(_DATA).decode() not in json.dumps(result.deferred.metadata)


async def test_identical_bytes_preserve_each_bound_argument_source() -> None:
    observed, sinks = [], []

    def consume(first: str, second: str):
        sinks.extend([base64.b64decode(first), base64.b64decode(second)])
        return "accepted"

    async def policy(invocation, metadata, *, context):
        observed.append(invocation.normalized_arguments)
        return InvocationPolicyDecision.allow()

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=_single_call_model(lambda messages: dict(zip(("first", "second"), _refs(messages), strict=True))),
        capabilities=(
            ImageReuseCapability(),
            Capability(
                tools=[
                    HarnessTool(
                        consume,
                        harness_metadata=HarnessToolMetadata(
                            tool_id="image.pair",
                            effects=frozenset({"write"}),
                            credential_audiences=(),
                            idempotency="none",
                            output_policy=ToolOutputPolicy(max_inline_bytes=1024, max_output_bytes=4096),
                        ),
                    )
                ]
            ),
        ),
    )
    result = await executable.run(
        [BinaryContent(_DATA, media_type="image/png"), BinaryContent(_DATA, media_type="image/png")],
        bindings=RunBindings.embedded(capabilities=(InvocationPolicyCapability(evaluator=policy),)),
    )
    assert result.status == "completed" and sinks == [_DATA, _DATA]
    first, second = (observed[0][key]["image_binding"] for key in ("first", "second"))
    assert first["base64_reference"] != second["base64_reference"]
    assert first["source"]["content_path"] == [0]
    assert second["source"]["content_path"] == [1]


async def test_denied_approval_does_not_authorize_a_later_reused_call_id() -> None:
    executable, _, sinks = _build(approval=True)
    first = await executable.run([BinaryContent(_DATA, media_type="image/png")])
    pending = first.deferred.approvals[0].target_call
    old_reference = pending.args_as_dict()["image"]
    effects = []

    def other(image: str):
        effects.append(image)
        return "unexpected"

    calls = 0

    async def model(messages, info):
        nonlocal calls
        calls += 1
        if calls == 1:
            yield {
                0: DeltaToolCall(
                    name="other", json_args=json.dumps({"image": old_reference}), tool_call_id=pending.tool_call_id
                )
            }
        else:
            yield "done"

    # Keep the captured pending function present while adding a distinct target.
    fresh, _, _ = _build(
        approval=True, model=FunctionModel(stream_function=model), capabilities=(Capability(tools=[other]),)
    )
    result = await fresh.run(
        previous_state=first.state,
        deferred_resume=DeferredToolResume(
            first.deferred, first.deferred.build_results(approvals={pending.tool_call_id: False})
        ),
    )
    assert result.status == "completed" and not effects and not sinks
    assert "unavailable in this Run" in str(result.state.message_history)


async def test_codeact_program_inputs_resolve_only_at_the_nested_target(tmp_path) -> None:
    from a13n_harness.capabilities import CodeActCapability
    from a13n_harness.toolsets import CodeActPolicyToolset, CodeActToolPolicy
    from pydantic_ai import FunctionToolset

    from .test_codeact import _local_environment

    (tmp_path / "image.codeact.py").write_text(
        "async def main(inputs):\n    return await consume(image=inputs['image'])\n", encoding="utf-8"
    )
    sinks, observed = [], []

    def consume(image: str):
        sinks.append(base64.b64decode(image, validate=True))
        return "accepted"

    calls = 0

    async def model(messages, info):
        nonlocal calls
        calls += 1
        if calls == 1:
            yield {
                0: DeltaToolCall(
                    name="run_program",
                    json_args=json.dumps(
                        {"path": "/workspace/image.codeact.py", "inputs": {"image": _refs(messages)[-1]}}
                    ),
                    tool_call_id="program-1",
                )
            }
        else:
            yield "done"

    async def policy(invocation, metadata, *, context):
        observed.append(invocation.normalized_arguments)
        assert base64.b64encode(_DATA).decode() not in str(invocation.normalized_arguments)
        assert invocation.normalized_arguments["image"]["image_binding"]["size_bytes"] == len(_DATA)
        return InvocationPolicyDecision.allow()

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(
            ImageReuseCapability(),
            CodeActCapability(),
            Capability(
                toolsets=[
                    CodeActPolicyToolset(
                        FunctionToolset(
                            [
                                HarnessTool(
                                    consume,
                                    harness_metadata=HarnessToolMetadata(
                                        tool_id="image.program",
                                        effects=frozenset({"write"}),
                                        credential_audiences=(),
                                        idempotency="none",
                                        output_policy=ToolOutputPolicy(max_inline_bytes=1024, max_output_bytes=4096),
                                    ),
                                )
                            ]
                        ),
                        CodeActToolPolicy(default=True),
                    )
                ]
            ),
        ),
    )
    result = await executable.run(
        [BinaryContent(_DATA, media_type="image/png")],
        bindings=RunBindings.embedded(
            environment=_local_environment(tmp_path), capabilities=(InvocationPolicyCapability(evaluator=policy),)
        ),
    )
    assert result.status == "completed" and sinks == [_DATA] and len(observed) == 1


async def test_request_image_resizing_preserves_original_bytes_for_the_consumer() -> None:
    from io import BytesIO

    from a13n_harness.filters import ImageFilterCapability
    from a13n_harness.image_input import ImageInputPolicy
    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (256, 256), "blue").save(buffer, format="PNG", compress_level=0)
    original = buffer.getvalue()
    assert len(original) > 65536
    calls = 0

    async def model(messages, info):
        nonlocal calls
        calls += 1
        if calls == 1:
            images = [
                value
                for message in messages
                if isinstance(message, ModelRequest)
                for part in message.parts
                if isinstance(part, UserPromptPart) and not isinstance(part.content, str)
                for value in part.content
                if isinstance(value, BinaryContent)
            ]
            assert images and all(value.data != original for value in images)
            yield {
                0: DeltaToolCall(
                    name="consume", json_args=json.dumps({"image": _refs(messages)[-1]}), tool_call_id="resized"
                )
            }
        else:
            yield "done"

    executable, _, sinks = _build(
        model=FunctionModel(stream_function=model),
        capabilities=(ImageFilterCapability(ImageInputPolicy(max_image_dimension=64)),),
    )
    result = await executable.run([BinaryContent(original, media_type="image/png")])
    assert result.status == "completed" and sinks == [original]


@pytest.mark.parametrize("limits", [ImageReuseLimits(max_images=1), ImageReuseLimits(max_total_bytes=len(_DATA))])
async def test_image_count_and_aggregate_retention_bounds(limits) -> None:
    async def model(messages, info):
        assert len(_refs(messages)) == 1
        yield "done"

    executable, _, _ = _build(limits=limits, model=FunctionModel(stream_function=model))
    result = await executable.run(
        [BinaryContent(_DATA, media_type="image/png"), BinaryContent(_DATA, media_type="image/png")]
    )
    state = result.state.agent_context_state.entries["a13n.image-reuse"].data
    assert len(state["images"]) == 1 and state["omitted"] == 1


@pytest.mark.parametrize("limits", [ImageReuseLimits(max_images=1), ImageReuseLimits(max_total_bytes=len(_DATA))])
async def test_fresh_run_admits_current_input_before_older_history(limits) -> None:
    async def finish(messages, info):
        yield "done"

    first, _, _ = _build(limits=limits, model=FunctionModel(stream_function=finish))
    saved = await first.run([BinaryContent(_DATA, media_type="image/png")])
    old_reference = saved.state.agent_context_state.entries["a13n.image-reuse"].data["images"][0]["reference"]
    current = b"x" * len(_DATA)
    second, _, sinks = _build(limits=limits, model=_single_call_model(lambda messages: {"image": _refs(messages)[-1]}))
    result = await second.run([BinaryContent(current, media_type="image/png")], previous_state=saved.state)
    assert result.status == "completed" and sinks == [current]
    state = result.state.agent_context_state.entries["a13n.image-reuse"].data
    assert len(state["images"]) == 1 and state["omitted"] is True
    assert state["images"][0]["reference"] != old_reference


async def test_fresh_run_admits_newest_history_when_current_input_has_no_image() -> None:
    async def finish(messages, info):
        yield "done"

    first, _, _ = _build(model=FunctionModel(stream_function=finish))
    oldest = await first.run([BinaryContent(_DATA, media_type="image/png")])
    newest_bytes = b"x" * len(_DATA)
    newest = await first.run([BinaryContent(newest_bytes, media_type="image/png")], previous_state=oldest.state)
    next_run, _, sinks = _build(
        limits=ImageReuseLimits(max_images=1),
        model=_single_call_model(lambda messages: {"image": _refs(messages)[-1]}),
    )
    result = await next_run.run("send the newest image", previous_state=newest.state)
    assert result.status == "completed" and sinks == [newest_bytes]
    state = result.state.agent_context_state.entries["a13n.image-reuse"].data
    assert len(state["images"]) == 1 and state["omitted"] is True


@pytest.mark.parametrize("limits", [ImageReuseLimits(max_images=1), ImageReuseLimits(max_total_bytes=len(_DATA))])
async def test_same_logical_run_keeps_issued_reference_when_new_input_exceeds_capacity(limits) -> None:
    async def finish(messages, info):
        yield "done"

    bindings = RunBindings.embedded(context_recovery_scope_id="image-priority-same-run")
    first, _, _ = _build(limits=limits, model=FunctionModel(stream_function=finish))
    saved = await first.run([BinaryContent(_DATA, media_type="image/png")], bindings=bindings)
    reference = saved.state.agent_context_state.entries["a13n.image-reuse"].data["images"][0]["reference"]
    resumed, _, sinks = _build(limits=limits, model=_single_call_model({"image": reference}))
    result = await resumed.run(
        [BinaryContent(b"x" * len(_DATA), media_type="image/png")], previous_state=saved.state, bindings=bindings
    )
    assert result.status == "completed" and sinks == [_DATA]
    state = result.state.agent_context_state.entries["a13n.image-reuse"].data
    assert len(state["images"]) == 1 and state["omitted"] is True
    assert state["images"][0]["reference"] == reference


async def test_approved_successor_keeps_pending_image_when_retention_is_full() -> None:
    executable, _, sinks = _build(approval=True, limits=ImageReuseLimits(max_images=1))
    pending = await executable.run(
        [BinaryContent(_DATA, media_type="image/png"), BinaryContent(b"other", media_type="image/png")]
    )
    assert pending.status == "suspended" and not sinks
    assert pending.state.agent_context_state.entries["a13n.image-reuse"].data["omitted"] is True
    restored = HarnessState.model_validate_json(pending.state.model_dump_json())
    resumed = await executable.run(
        previous_state=restored,
        deferred_resume=DeferredToolResume(pending.deferred, pending.deferred.build_results(approve_all=True)),
    )
    assert resumed.status == "completed" and sinks == [_DATA]


async def test_same_logical_run_restores_reference_after_original_leaves_active_history() -> None:
    from pydantic_ai.messages import ModelResponse, TextPart

    bindings = RunBindings.embedded(context_recovery_scope_id="host-logical-image-run")

    async def finish(messages, info):
        yield "summarized"

    first, _, _ = _build(model=FunctionModel(stream_function=finish))
    result = await first.run([BinaryContent(_DATA, media_type="image/png")], bindings=bindings)
    reference = result.state.agent_context_state.entries["a13n.image-reuse"].data["images"][0]["reference"]
    # Restore the same Host logical Run with the canonical image only in capability
    # state, as after compaction. No input/tool producer can recreate these bytes.
    restored = HarnessState.model_validate_json(result.state.to_json_bytes())
    checkpoint = HarnessState.new(
        thread_id=restored.thread_id,
        agent_context_state=restored.agent_context_state,
        message_history=(
            ModelRequest(parts=[UserPromptPart("Image summarized")]),
            ModelResponse(parts=[TextPart("Continue")]),
        ),
    )
    resumed, sources, sinks = _build(model=_single_call_model({"image": reference}))
    second = await resumed.run("send retained image", previous_state=checkpoint, bindings=bindings)
    assert second.status == "completed" and not sources and sinks == [_DATA]


async def test_native_schema_validation_error_does_not_echo_expanded_image() -> None:
    executable, _, sinks = _build(model=_single_call_model(lambda messages: {"image": [_refs(messages)[-1]]}))
    result = await executable.run([BinaryContent(_DATA, media_type="image/png")])
    assert result.status == "completed" and not sinks
    assert "failed the target tool's validation" in str(result.state.message_history)
    assert base64.b64encode(_DATA).decode() not in str(result.state.message_history)


async def test_tool_return_keeps_distinct_native_return_and_supplemental_paths() -> None:
    from pydantic_ai import ToolReturn

    def produce():
        return ToolReturn(
            return_value=[BinaryContent(b"one", media_type="image/png"), BinaryContent(b"two", media_type="image/png")],
            content=[BinaryContent(b"three", media_type="image/png")],
        )

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=_produce_then_finish),
        capabilities=(ImageReuseCapability(), Capability(tools=[produce])),
    )
    result = await executable.run("produce")
    assert result.status == "completed"
    images = result.state.agent_context_state.entries["a13n.image-reuse"].data["images"]
    assert [image["source"] for image in images] == [
        {"kind": "tool", "tool_call_id": "source-1", "content_path": ["return_value", 0]},
        {"kind": "tool", "tool_call_id": "source-1", "content_path": ["return_value", 1]},
        {"kind": "tool", "tool_call_id": "source-1", "content_path": ["content", 0]},
    ]


async def test_restored_same_run_state_must_fit_current_retention_limits() -> None:
    from a13n_harness.errors import DefinitionError

    async def finish(messages, info):
        yield "done"

    bindings = RunBindings.embedded(context_recovery_scope_id="same-image-run")
    first, _, _ = _build(model=FunctionModel(stream_function=finish))
    saved = await first.run([BinaryContent(_DATA, media_type="image/png")], bindings=bindings)
    second, _, _ = _build(limits=ImageReuseLimits(max_image_bytes=1), model=FunctionModel(stream_function=finish))
    with pytest.raises(DefinitionError, match="current retention limits"):
        await second.run("continue", previous_state=saved.state, bindings=bindings)
