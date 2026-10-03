"""Canonical content annotations and native tool-result boundary regressions."""

import pytest
from a13n_harness import HarnessBuilder, HarnessState, RunBindings
from a13n_harness.content import (
    ContentItem,
    ContentMetadata,
    input_request,
    merge_request_history,
    request_input_content,
)
from a13n_harness.filters import ContentFilterCapability, ContentFilterConfiguration
from pydantic_ai import BinaryContent
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.messages import ModelMessagesTypeAdapter, ModelRequest, ToolReturnPart
from pydantic_ai.models.function import FunctionModel


@pytest.mark.anyio
async def test_primary_annotations_precede_context_and_survive_repeated_preparation() -> None:
    from types import SimpleNamespace
    from typing import cast

    from a13n_harness import AgentContext
    from a13n_harness.capabilities.input import InputCapability
    from a13n_harness.input import ModelInputState
    from a13n_harness.model_context import (
        ModelContextBlock,
        ModelContextCoordinatorCapability,
        ModelContextPlacement,
        ModelContextProjection,
        ModelContextProjectionRequest,
        ModelContextRequestKind,
        _commit_projection,
    )
    from pydantic_ai import RunContext
    from pydantic_ai.messages import ModelResponse, TextContent, TextPart, UserPromptPart
    from pydantic_ai.models import ModelRequestContext, ModelRequestParameters

    content = (
        ContentItem("same", ContentMetadata(source_id="authored")),
        ContentItem("hidden surface", ContentMetadata(display=False, source_id="surface")),
    )
    state = ModelInputState()
    state.begin("attempt-one", content, recovery=False)
    history = [ModelRequest(parts=[UserPromptPart([item.value for item in content])])]
    ctx = cast(
        RunContext[AgentContext],
        SimpleNamespace(deps=SimpleNamespace(_model_input=state), messages=history, run_id="attempt-one", run_step=1),
    )
    request_context = ModelRequestContext(
        model=FunctionModel(lambda messages, info: ModelResponse(parts=[TextPart("unused")])),
        messages=list(history),
        model_settings={},
        model_request_parameters=ModelRequestParameters(),
    )
    capability = InputCapability()
    assert ModelContextCoordinatorCapability in capability.get_ordering().wraps
    request_context = await capability.before_model_request(ctx, request_context)

    async def project_then_run_native_hooks(request_context: ModelRequestContext) -> ModelResponse:
        # The context deliberately duplicates authored text: provenance must not
        # be recovered by matching strings or by selecting the final prompt.
        projected = _commit_projection(
            request_context.messages,
            ModelContextProjectionRequest(kind=ModelContextRequestKind.INPUT),
            ModelContextProjection(
                blocks=(ModelContextBlock("guidance", ModelContextPlacement.REQUEST_EPILOGUE, "same"),)
            ),
        )
        ctx.messages[:] = projected
        request_context.messages = projected
        request_context = await capability.before_model_request(ctx, request_context)
        restored = ModelMessagesTypeAdapter.validate_json(ModelMessagesTypeAdapter.dump_json(request_context.messages))
        items = request_input_content(restored[0])
        assert [item.metadata.source_id for item in items] == ["authored", "surface", "guidance"]
        assert [item.metadata.display for item in items] == [True, False, False]
        assert [item.value.content if isinstance(item.value, TextContent) else item.value for item in items] == [
            "same",
            "hidden surface",
            "same",
        ]
        return ModelResponse(parts=[TextPart("done")])

    response = await project_then_run_native_hooks(request_context)
    assert response.parts == [TextPart("done")]


@pytest.mark.anyio
async def test_primary_input_is_observed_once_when_native_preparation_fails() -> None:
    from a13n_harness import HarnessEvent
    from a13n_harness.events import InputTextEvent
    from pydantic_ai.capabilities import AbstractCapability

    class UnavailableInstructions(AbstractCapability):
        def get_instructions(self):
            async def instructions(ctx):
                raise RuntimeError("instructions unavailable")

            return instructions

    async def model(messages, info):
        pytest.fail("preparation failure must not dispatch the model")
        yield "unreachable"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=[UnavailableInstructions()],
    )
    observed = []
    async with executable.stream(
        [ContentItem("Authored input", ContentMetadata(source_id="submission"))],
        bindings=RunBindings.embedded(),
    ) as stream:
        async for item in stream:
            if isinstance(item, HarnessEvent) and isinstance(item.event, InputTextEvent):
                observed.append(item.event)
        assert stream.result is not None and stream.result.status == "failed"
    assert len(observed) == 1
    assert observed[0].content == "Authored input"
    assert observed[0].source == "user"
    assert observed[0].metadata.source_id == "submission"


def test_annotations_follow_duplicate_values_through_canonical_merge_and_codec() -> None:
    image = BinaryContent(b"same", media_type="image/png", vendor_metadata={"detail": "high"})
    first = input_request([ContentItem(image, ContentMetadata(source_id="first"))])
    second = input_request([ContentItem(image, ContentMetadata(source_id="second", display=False))])
    merged = merge_request_history([first, second])
    assert len(merged) == 1
    restored = ModelMessagesTypeAdapter.validate_json(ModelMessagesTypeAdapter.dump_json(list(merged)))
    request = restored[0]
    assert isinstance(request, ModelRequest)
    items = request_input_content(request)
    assert [item.metadata.source_id for item in items] == ["first", "second"]
    assert [item.metadata.display for item in items] == [True, False]
    assert image.vendor_metadata == {"detail": "high"}


@pytest.mark.anyio
@pytest.mark.parametrize("filtered", [False, True])
async def test_input_envelope_reaches_canonical_history_not_model(filtered: bool) -> None:
    image = BinaryContent(b"image", media_type="image/png", vendor_metadata={"detail": "high"})
    input = [ContentItem(image, ContentMetadata(source_id="image#1", display=False))]

    async def model(messages, info):
        request = messages[-1]
        assert isinstance(request, ModelRequest)
        for part in request.parts:
            if isinstance(part, ToolReturnPart):
                continue
            content = part.content
            if isinstance(content, list):
                assert not any(isinstance(item, ContentItem) for item in content)
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        model=FunctionModel(stream_function=model),
        output_type=str,
        capabilities=[ContentFilterCapability(ContentFilterConfiguration(max_binary_bytes=0))] if filtered else [],
    )
    result = await executable.run(input, bindings=RunBindings.embedded())
    assert result.output_or_raise() == "done"
    restored = HarnessState.model_validate_json(result.state.model_dump_json())
    request = next(message for message in restored.message_history if isinstance(message, ModelRequest))
    items = request_input_content(request)
    # Filtering is outbound-only; canonical history retains its original media.
    item = next(item for item in items if item.metadata.source_id == "image#1")
    assert item.metadata.display is False
    assert image.vendor_metadata == {"detail": "high"}


@pytest.mark.anyio
async def test_media_steering_and_new_run_preserve_annotations_with_changed_system_prompt() -> None:
    import asyncio

    from a13n_harness import AgentSpec as HarnessAgentSpec
    from a13n_harness.capabilities.steering import steering_input_ids
    from pydantic_ai.messages import SystemPromptPart

    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def model(messages, info):
        nonlocal calls
        calls += 1
        if calls == 1:
            started.set()
            await release.wait()
        yield "done"

    image = BinaryContent(b"same", media_type="image/png", vendor_metadata={"detail": "high"})

    def build(prompt):
        return HarnessBuilder().build(
            HarnessAgentSpec(system_prompt=prompt), model=FunctionModel(stream_function=model), output_type=str
        )

    async with build("old system").stream(
        [ContentItem(image, ContentMetadata(source_id="initial", display=False))]
    ) as run:

        async def consume():
            return [item async for item in run]

        consumer = asyncio.create_task(consume())
        await asyncio.wait_for(started.wait(), timeout=3)
        await run.steer(
            [ContentItem(image, ContentMetadata(source_id="steering", attachment_id="file-one"))], input_id="input-one"
        )
        release.set()
        await asyncio.wait_for(consumer, timeout=3)
    assert run.result is not None
    first = HarnessState.model_validate_json(run.result.state.model_dump_json())
    result = await build(("new system", "second block")).run(
        [ContentItem(image, ContentMetadata(source_id="next-run"))],
        previous_state=first,
    )
    assert result.output_or_raise() == "done"
    state = HarnessState.model_validate_json(result.state.model_dump_json())
    items = [
        item
        for message in state.message_history
        if isinstance(message, ModelRequest)
        for item in request_input_content(message)
        if isinstance(item.value, BinaryContent)
    ]
    assert [item.metadata.source_id for item in items] == ["initial", "steering", "next-run"]
    assert [item.metadata.display for item in items] == [False, True, True]
    assert items[1].metadata.model_extra == {"attachment_id": "file-one"}
    assert steering_input_ids(state.message_history) == ("input-one",)
    assert [
        part.content
        for message in state.message_history
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, SystemPromptPart)
    ] == ["new system", "second block"]


@pytest.mark.anyio
@pytest.mark.parametrize("repair", ["orphan", "dangling", "interrupted"])
async def test_native_execution_repairs_history_without_annotation_reconstruction(repair: str) -> None:
    from dataclasses import replace

    from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart

    image = BinaryContent(b"same", media_type="image/png")
    request = input_request(
        [
            ContentItem(image, ContentMetadata(source_id="first", display=False)),
            ContentItem(image, ContentMetadata(source_id="second", attachment_id="two")),
        ]
    )
    if repair == "orphan":
        request = replace(
            request,
            parts=[ToolReturnPart("missing", "orphan", "gone"), *request.parts],
            metadata={"a13n.content": {"1": request.metadata["a13n.content"]["0"]}},
        )
        history = [request]
    else:
        response = ModelResponse(
            parts=[ToolCallPart("missing", "{}", "call-one")],
            state="interrupted" if repair == "interrupted" else "complete",
        )
        history = (
            [response, request, ModelResponse(parts=[TextPart("later")], model_name="test")]
            if repair == "dangling"
            else [request, response]
        )

    async def model(messages, info):
        tool_results = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        assert [part.tool_call_id for part in tool_results] == ([] if repair == "orphan" else ["call-one"])
        yield "done"

    executable = HarnessBuilder().build(AgentSpec(), model=FunctionModel(stream_function=model), output_type=str)
    previous = HarnessState.new(message_history=tuple(history))
    original = previous.model_dump_json()
    result = await executable.run("Continue", previous_state=HarnessState.model_validate_json(original))
    assert result.output_or_raise() == "done"
    restored = HarnessState.model_validate_json(result.state.model_dump_json())
    items = [
        item
        for message in restored.message_history
        if isinstance(message, ModelRequest)
        for item in request_input_content(message)
        if isinstance(item.value, BinaryContent)
    ]
    assert [(item.value.data, item.value.media_type) for item in items] == [(b"same", "image/png")] * 2
    if repair == "orphan":
        # Native removal moves the prompt. Missing annotations use normal
        # defaults rather than reconstructing the old positional association.
        assert [item.metadata for item in items] == [ContentMetadata(), ContentMetadata()]
    assert previous.model_dump_json() == original


@pytest.mark.parametrize(
    "annotations",
    [None, [], {"0": "invalid"}, {"0": []}, {"0": [{"display": False}, {}]}, {"0": [{"display": []}]}],
)
def test_unusable_prompt_annotations_use_native_defaults(annotations: object) -> None:
    from dataclasses import replace

    request = replace(input_request("hello"), metadata={"a13n.content": annotations})
    assert request_input_content(request) == [ContentItem("hello")]


def test_request_merge_leaves_tool_pairing_and_response_repair_to_native_execution() -> None:
    from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart

    history = (
        ModelRequest(parts=[ToolReturnPart("missing", "orphan", "gone")]),
        ModelResponse(parts=[TextPart("partial")]),
        ModelResponse(parts=[ToolCallPart("pending", "{}", "call-one")], state="interrupted"),
    )
    assert merge_request_history(history) == history


def test_adjacent_serialized_steering_requests_keep_all_delivered_ids() -> None:
    from a13n_harness.capabilities.steering import steering_input_ids

    requests = [
        input_request(
            [ContentItem("same", ContentMetadata(source_id=input_id))], metadata={"a13n.steering-input": input_id}
        )
        for input_id in ("input-one", "input-two")
    ]
    normalized = merge_request_history(requests)
    restored = ModelMessagesTypeAdapter.validate_json(ModelMessagesTypeAdapter.dump_json(list(normalized)))
    assert steering_input_ids(restored) == ("input-one", "input-two")
    assert [item.metadata.source_id for item in request_input_content(restored[0])] == ["input-one", "input-two"]


@pytest.mark.anyio
async def test_compaction_rebuilds_annotations_without_inheriting_template_slots() -> None:
    from a13n_harness.capabilities import CompactionCapability, CompactionPolicy
    from pydantic_ai.messages import ModelResponse, TextPart
    from pydantic_ai.usage import RequestUsage

    original = input_request(
        [
            ContentItem("first", ContentMetadata(source_id="first")),
            ContentItem("second", ContentMetadata(source_id="second", display=False)),
        ]
    )
    previous = HarnessState.new(
        message_history=(
            original,
            ModelResponse(parts=[TextPart("old response")], usage=RequestUsage(input_tokens=2100)),
        )
    )
    calls = 0

    async def model(messages, info):
        nonlocal calls
        calls += 1
        yield "summary" if calls == 1 else "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        model=FunctionModel(stream_function=model),
        output_type=str,
        capabilities=[CompactionCapability(CompactionPolicy(trigger_tokens=2000))],
    )
    result = await executable.run("Continue", previous_state=previous)
    assert result.output_or_raise() == "done"
    assert calls == 2
    restored = HarnessState.model_validate_json(result.state.model_dump_json())
    items = [
        item
        for message in restored.message_history
        if isinstance(message, ModelRequest)
        for item in request_input_content(message)
    ]
    assert [item.value for item in items if item.metadata.display] == ["Continue"]


def test_handoff_rebuilds_annotations_and_preserves_replayed_request_annotations() -> None:
    from a13n_harness._handoff import _HandoffState
    from a13n_harness.capabilities.context import _build_restored_history

    original = input_request(
        [
            ContentItem("first", ContentMetadata(source_id="first")),
            ContentItem("second", ContentMetadata(source_id="second", display=False)),
        ]
    )
    messages = _build_restored_history(
        [original], _HandoffState(summary="summary", operation_id="handoff-one"), retained_requests=(original,)
    )
    restored = ModelMessagesTypeAdapter.validate_json(ModelMessagesTypeAdapter.dump_json(messages))
    summary_items = request_input_content(restored[0])
    assert all(item.metadata.source_id not in {"first", "second"} for item in summary_items)
    assert [item.metadata.source_id for item in request_input_content(restored[1])] == ["first", "second"]
    assert [item.metadata.display for item in request_input_content(restored[1])] == [True, False]


@pytest.mark.anyio
@pytest.mark.parametrize("factory", [False, True])
async def test_recovery_attempt_annotations_follow_current_prompt_not_initial_input(factory: bool) -> None:
    from a13n_harness import ModelRecoveryPolicy

    recovery_input = [
        ContentItem(
            "private recovery prompt",
            ContentMetadata(display=False, source_id="recovery", attachment_id="recovery-file"),
        )
    ]
    calls = 0

    async def model(messages, info):
        nonlocal calls
        calls += 1
        if calls == 1:
            yield "partial"
            raise ConnectionError("interrupted")
        yield "done"

    policy = ModelRecoveryPolicy(
        enabled=True,
        max_attempts=2,
        backoff_initial_seconds=0,
        backoff_max_seconds=0,
        **(
            {"prompt_factory": lambda error, attempt, messages: recovery_input}
            if factory
            else {"continuation_prompt": recovery_input}
        ),
    )
    executable = HarnessBuilder().build(
        AgentSpec(), model=FunctionModel(stream_function=model), output_type=str, model_recovery=policy
    )
    result = await executable.run([ContentItem("initial", ContentMetadata(source_id="initial"))])
    assert result.output_or_raise() == "done"
    assert calls == 2
    restored = HarnessState.model_validate_json(result.state.model_dump_json())
    items = [
        item
        for message in restored.message_history
        if isinstance(message, ModelRequest)
        for item in request_input_content(message)
    ]
    initial = next(item for item in items if item.value == "initial")
    continuation = next(item for item in items if item.value == "private recovery prompt")
    assert initial.metadata.source_id == "initial"
    assert continuation.metadata.source_id == "recovery"
    assert continuation.metadata.display is False
    assert continuation.metadata.model_extra == {"attachment_id": "recovery-file"}
