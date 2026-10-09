from __future__ import annotations

import base64
import io
from collections.abc import AsyncIterator
from copy import deepcopy
from pathlib import Path

import pytest
from a13n_harness import (
    AgentSpec,
    HarnessBuilder,
    HarnessModelCharacteristics,
    HarnessState,
    ImageInputPolicy,
    RunBindings,
)
from a13n_harness.content import (
    ContentItem,
    ContentMetadata,
    content_items,
    input_request,
    prompt_content,
    request_input_content,
)
from a13n_harness.filters import ImageFilterCapability
from a13n_harness.filters import image as image_module
from a13n_harness.filters.image import _project_images
from a13n_harness.models import SelfHealingModelCapability
from a13n_harness.toolsets.file_media import AgentMediaUnderstandingProvider, MediaUnderstandingRequest
from PIL import Image
from pydantic_ai import Agent, BinaryContent, BinaryImage, ImageUrl, ToolReturn
from pydantic_ai.agent.spec import AgentSpec as NativeAgentSpec
from pydantic_ai.capabilities import Capability
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextContent,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("content", ["text", ["text"], [ImageUrl("https://example.com/image.png")]])
async def test_noop_projection_does_not_copy_history(content, monkeypatch: pytest.MonkeyPatch) -> None:
    history = [ModelRequest(parts=[UserPromptPart(content)]), ModelResponse(parts=[TextPart("previous")])]

    def unexpected_copy(value):
        del value
        pytest.fail("A no-op image projection must not deepcopy history")

    monkeypatch.setattr(image_module, "deepcopy", unexpected_copy)
    assert _project_images(history, ImageInputPolicy()) is None


@pytest.mark.parametrize("split", [False, True])
async def test_png_compression_reuses_validated_pixels(split: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    from PIL import PngImagePlugin

    native = BinaryImage(_png((80, 2600 if split else 80)), media_type="image/png", identifier="source")
    decoded = 0
    original_load = PngImagePlugin.PngImageFile.load

    def counted_load(image, *args, **kwargs):
        nonlocal decoded
        if image.tile:
            decoded += 1
        return original_load(image, *args, **kwargs)

    monkeypatch.setattr(PngImagePlugin.PngImageFile, "load", counted_load)
    projected = _project_images(
        [ModelRequest(parts=[UserPromptPart([native])])],
        ImageInputPolicy(image_split_max_height=1000, image_split_overlap=100, max_image_dimension=40),
    )
    assert projected is not None
    assert decoded == 1
    assert len(_images(projected)) == (3 if split else 1)
    assert all(image.media_type == "image/jpeg" for image in _images(projected))


async def test_jpeg_resize_preserves_decoder_draft_pixels() -> None:
    buffer = io.BytesIO()
    with Image.effect_noise((512, 512), 50).convert("RGB") as image:
        image.save(buffer, format="JPEG")
    native = BinaryImage(buffer.getvalue(), media_type="image/jpeg")
    expected = io.BytesIO()
    with Image.open(io.BytesIO(native.data)) as image:
        image.thumbnail((32, 32), Image.Resampling.LANCZOS)
        image.save(expected, format="JPEG", quality=95, optimize=True)
    projected = _project_images(
        [ModelRequest(parts=[UserPromptPart([native])])], ImageInputPolicy(max_image_dimension=32, max_image_bytes=0)
    )
    assert projected is not None
    assert _images(projected)[0].data == expected.getvalue()


def _png(size: tuple[int, int], *, mode: str = "RGB", color: object = "red") -> bytes:
    with Image.new(mode, size, color) as image:
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
    return buffer.getvalue()


def _animated(format: str = "GIF", size: tuple[int, int] = (20, 5000)) -> bytes:
    frames = [Image.new("RGB", size, color) for color in ("red", "blue")]
    buffer = io.BytesIO()
    try:
        frames[0].save(buffer, format=format, save_all=True, append_images=frames[1:], loop=0)
    finally:
        for frame in frames:
            frame.close()
    return buffer.getvalue()


def _images(messages: list[ModelMessage] | tuple[ModelMessage, ...]) -> list[BinaryContent | ImageUrl]:
    found = []
    for message in messages:
        if not isinstance(message, ModelRequest):
            continue
        for part in message.parts:
            if not isinstance(part, (UserPromptPart, ToolReturnPart)):
                continue
            items = part.content if isinstance(part.content, (list, tuple)) else [part.content]
            found.extend(
                item
                for item in items
                if isinstance(item, ImageUrl) or (isinstance(item, BinaryContent) and item.is_image)
            )
    return found


async def _project(
    history: list[ModelMessage], policy: ImageInputPolicy
) -> tuple[list[ModelMessage], list[ModelMessage]]:
    seen: list[list[ModelMessage]] = []

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del info
        seen.append(deepcopy(messages))
        return ModelResponse(parts=[TextPart("done")])

    agent = Agent(FunctionModel(respond), capabilities=[ImageFilterCapability(policy)])
    coherent: list[ModelMessage] = []
    for message in history:
        if isinstance(message, ModelRequest):
            calls = [
                ToolCallPart(part.tool_name, {}, tool_call_id=part.tool_call_id)
                for part in message.parts
                if isinstance(part, ToolReturnPart)
            ]
            if calls:
                coherent.append(ModelResponse(parts=calls))
        coherent.append(message)
    result = await agent.run("Inspect", message_history=coherent)
    assert result.output == "done"
    return seen[0], result.all_messages()


@pytest.mark.parametrize("surface", ["user", "tool-scalar", "tool-list"])
async def test_split_geometry_overlap_and_native_identity(surface: str) -> None:
    with Image.new("RGB", (12, 2600)) as original:
        original.putdata([(y % 256, y // 256, 0) for y in range(2600) for _ in range(12)])
        buffer = io.BytesIO()
        original.save(buffer, format="PNG")
        native = BinaryImage(
            buffer.getvalue(), media_type="image/png", identifier="original", vendor_metadata={"detail": "high"}
        )
        part = (
            UserPromptPart(("before", native, "after"))
            if surface == "user"
            else ToolReturnPart(
                "view", native if surface == "tool-scalar" else ["before", native, "after"], tool_call_id="view-1"
            )
        )
        history = [ModelRequest(parts=[part])]
        seen, retained = await _project(
            history,
            ImageInputPolicy(
                image_split_max_height=1000, image_split_overlap=100, max_image_bytes=0, max_image_dimension=0
            ),
        )
        images = _images(seen)
        assert [image.identifier for image in images] == [
            "original-segment-1",
            "original-segment-2",
            "original-segment-3",
        ]
        assert len({image.identifier for image in images}) == 3
        for image, top, bottom in zip(images, (0, 900, 1800), (1000, 1900, 2600), strict=True):
            assert isinstance(image, BinaryImage)
            assert image.vendor_metadata == {"detail": "high"}
            with Image.open(io.BytesIO(image.data)) as decoded, original.crop((0, top, 12, bottom)) as expected:
                assert decoded.size == expected.size
                assert decoded.tobytes() == expected.tobytes()
        assert _images(retained) == [native]
        assert _images(history) == [native]
        assert native.identifier == "original"
        projected_part = next(message for message in seen if isinstance(message, ModelRequest)).parts[0]
        assert isinstance(projected_part, (UserPromptPart, ToolReturnPart))
        if surface == "user":
            assert isinstance(projected_part.content, tuple)
            assert projected_part.content[0] == "before" and projected_part.content[-1] == "after"


@pytest.mark.parametrize("shape", [list, tuple])
@pytest.mark.parametrize("max_images", [1, 20])
async def test_split_prompt_annotations_follow_segments_without_changing_history(shape, max_images: int) -> None:
    native = BinaryImage(_png((12, 2600)), media_type="image/png", identifier="attachment")
    items = [
        ContentItem("before", ContentMetadata(source_id="authored-before")),
        ContentItem(native, ContentMetadata(display=False, source_id="attachment", media=True, file_id="file-one")),
        ContentItem("after", ContentMetadata(source_id="authored-after")),
    ]
    request = input_request(items, metadata={"application": {"reference": "retained"}})
    request.parts[0].content = shape(request.parts[0].content)
    original = deepcopy(request)
    messages = _project_images(
        [request],
        ImageInputPolicy(image_split_max_height=1000, image_split_overlap=100, max_images=max_images),
    )
    assert messages is not None
    projected = messages[0]
    assert isinstance(projected, ModelRequest)
    annotated = prompt_content(projected, 0)
    assert [item.metadata for item in annotated] == [
        items[0].metadata,
        *([items[1].metadata] * 3),
        items[2].metadata,
    ]
    assert annotated[0].value == "before" and annotated[-1].value == "after"
    assert isinstance(projected.parts[0].content, shape)
    assert projected.metadata["application"] == {"reference": "retained"}
    assert request == original
    assert prompt_content(request, 0) == items
    assert request.metadata == original.metadata


@pytest.mark.parametrize("height,expected_count", [(4096, 1), (5000, 2)])
async def test_default_split_threshold(height: int, expected_count: int) -> None:
    native = BinaryContent(_png((12, height)), media_type="image/png")
    seen, retained = await _project([ModelRequest(parts=[UserPromptPart([native])])], ImageInputPolicy())
    assert len(_images(seen)) == expected_count
    assert _images(retained) == [native]
    if expected_count == 1:
        assert _images(seen) == [native]


async def test_prepared_segments_are_compressed_then_counted_newest_first() -> None:
    native = BinaryContent(_png((200, 2600)), media_type="image/png", identifier="long")
    seen, retained = await _project(
        [ModelRequest(parts=[UserPromptPart([native])])],
        ImageInputPolicy(image_split_max_height=1000, image_split_overlap=100, max_image_dimension=100, max_images=2),
    )
    images = _images(seen)
    assert [image.identifier for image in images] == ["long-segment-2", "long-segment-3"]
    assert all(isinstance(image, BinaryContent) and image.media_type == "image/jpeg" for image in images)
    for image in images:
        with Image.open(io.BytesIO(image.data)) as decoded:
            assert max(decoded.size) <= 100
    assert _images(retained) == [native]


async def test_count_policy_keeps_latest_across_user_and_tool_parts_without_counting_corruption() -> None:
    old = ImageUrl("https://example.com/old.png")
    first = BinaryContent(_png((20, 20)), media_type="image/png", identifier="first")
    last = ImageUrl("data:image/png;base64," + base64.b64encode(first.data).decode(), identifier="last")
    corrupt = BinaryContent(b"invalid image", media_type="image/png")
    history = [
        ModelRequest(parts=[UserPromptPart([old])]),
        ModelResponse(parts=[TextPart("previous")]),
        ModelRequest(parts=[ToolReturnPart("view", [first], tool_call_id="view-1"), UserPromptPart([last, corrupt])]),
    ]
    seen, retained = await _project(history, ImageInputPolicy(max_images=2))
    assert _images(seen) == [first, last]
    assert "max_images=2" in str(seen[0].parts[0].content)
    assert "broken or corrupted" in str(seen)
    assert _images(retained) == [old, first, last, corrupt]
    assert history[0].parts[0].content == [old]


@pytest.mark.parametrize("corruption", ["png-crc", "jpeg-truncated"])
@pytest.mark.parametrize("surface", ["user", "tool-scalar", "tool-list"])
@pytest.mark.parametrize("streaming", [False, True])
async def test_corrupt_images_do_not_consume_count_or_mutate_canonical_history(
    corruption: str, surface: str, streaming: bool
) -> None:
    if corruption == "png-crc":
        damaged = bytearray(_png((32, 32)))
        damaged[damaged.index(b"IDAT") + 4] ^= 1
        data = bytes(damaged)
        media_type = "image/png"
        with Image.open(io.BytesIO(data)) as source, pytest.raises(SyntaxError):
            source.verify()
    else:
        buffer = io.BytesIO()
        with Image.new("RGB", (32, 32), "red") as source:
            source.save(buffer, format="JPEG")
        data = buffer.getvalue()[:-2]
        media_type = "image/jpeg"
        with Image.open(io.BytesIO(data)) as source:
            source.verify()
        with Image.open(io.BytesIO(data)) as source, pytest.raises(OSError):
            source.load()
    native = BinaryImage(data, media_type=media_type, identifier="corrupt", vendor_metadata={"source": "retained"})
    old = ImageUrl("https://example.com/valid-old.png")
    history: list[ModelMessage] = [
        ModelRequest(parts=[UserPromptPart([old])]),
        ModelResponse(parts=[TextPart("previous")]),
    ]
    if surface == "user":
        history.append(ModelRequest(parts=[UserPromptPart([native])]))
    else:
        history.extend(
            [
                ModelResponse(parts=[ToolCallPart("view", {}, tool_call_id="view-1")]),
                ModelRequest(
                    parts=[
                        ToolReturnPart("view", native if surface == "tool-scalar" else [native], tool_call_id="view-1")
                    ]
                ),
            ]
        )
    original = deepcopy(history)
    previous = HarnessState.new(message_history=tuple(history))
    seen: list[list[ModelMessage]] = []

    async def respond(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del info
        seen.append(deepcopy(messages))
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(model_characteristics=HarnessModelCharacteristics(image_input=ImageInputPolicy(max_images=1))),
        output_type=str,
        model=FunctionModel(stream_function=respond),
    )
    if streaming:
        async with executable.stream("Inspect", previous_state=previous, bindings=RunBindings.embedded()) as run:
            async for _ in run:
                pass
            result = run.result
            assert result is not None
    else:
        result = await executable.run("Inspect", previous_state=previous, bindings=RunBindings.embedded())
    assert result.output_or_raise() == "done"
    assert _images(seen[0]) == [old]
    assert "broken or corrupted" in str(seen[0])
    assert "max_images=1" not in str(seen[0])
    assert result.state is not None
    assert _images(result.state.message_history) == [old, native]
    assert _images(result.all_messages()) == [old, native]
    assert previous.message_history == tuple(original)
    assert history == original
    assert native.data == data and native.vendor_metadata == {"source": "retained"}


@pytest.mark.parametrize("size", [(8100, 81), (81, 8100)])
async def test_dimension_only_compression_is_proportional_and_preserves_identity(size: tuple[int, int]) -> None:
    native = BinaryImage(_png(size), media_type="image/png", identifier="same", vendor_metadata={"detail": "low"})
    seen, _ = await _project(
        [ModelRequest(parts=[UserPromptPart([native])])],
        ImageInputPolicy(split_large_images=False, max_image_bytes=0),
    )
    image = _images(seen)[0]
    assert isinstance(image, BinaryImage)
    assert image.media_type == "image/jpeg"
    assert image.identifier == "same" and image.vendor_metadata == {"detail": "low"}
    with Image.open(io.BytesIO(image.data)) as decoded:
        assert decoded.size == ((8000, 80) if size[0] > size[1] else (80, 8000))


async def test_encoded_byte_budget_not_raw_byte_budget_triggers_compression() -> None:
    data = _png((500, 500))
    encoded_limit = len(data)
    native = BinaryContent(data, media_type="image/png")
    seen, retained = await _project(
        [ModelRequest(parts=[ToolReturnPart("view", native, tool_call_id="view-1")])],
        ImageInputPolicy(split_large_images=False, max_image_bytes=encoded_limit, max_image_dimension=0),
    )
    image = _images(seen)[0]
    assert isinstance(image, BinaryContent)
    assert image.data != data
    assert len(image.data) <= (encoded_limit // 4) * 3
    assert len(base64.b64encode(image.data)) <= encoded_limit
    assert _images(retained) == [native]


@pytest.mark.parametrize("encoded_limit", [1, 2, 3, 4, 10])
async def test_impossible_encoded_limits_replace_images_with_explanation(encoded_limit: int) -> None:
    seen, retained = await _project(
        [ModelRequest(parts=[UserPromptPart([BinaryContent(_png((20, 20)), media_type="image/png")])])],
        ImageInputPolicy(max_image_bytes=encoded_limit),
    )
    assert _images(seen) == []
    assert "could not be prepared" in str(seen[0].parts[0].content)
    assert len(_images(retained)) == 1


async def test_exact_dimension_and_disabled_compression_keep_bytes() -> None:
    native = BinaryContent(_png((8000, 80)), media_type="image/png")
    history = [ModelRequest(parts=[UserPromptPart([native])])]
    for policy in (
        ImageInputPolicy(split_large_images=False),
        ImageInputPolicy(split_large_images=False, max_image_bytes=0, max_image_dimension=0),
    ):
        seen, _ = await _project(history, policy)
        assert _images(seen) == [native]


async def test_transparency_is_composited_onto_white_when_jpeg_is_needed() -> None:
    native = BinaryContent(_png((200, 200), mode="RGBA", color=(255, 0, 0, 0)), media_type="image/png")
    seen, _ = await _project(
        [ModelRequest(parts=[UserPromptPart([native])])], ImageInputPolicy(max_image_dimension=100)
    )
    with Image.open(io.BytesIO(_images(seen)[0].data)) as decoded:
        assert decoded.mode == "RGB"
        assert all(channel > 250 for channel in decoded.getpixel((50, 50)))


@pytest.mark.parametrize("format,media_type", [("GIF", "image/gif"), ("WEBP", "image/webp")])
async def test_animation_is_not_flattened_by_splitting_or_compression(format: str, media_type: str) -> None:
    native = BinaryContent(_animated(format), media_type=media_type)
    history = [ModelRequest(parts=[UserPromptPart([native])])]
    seen, _ = await _project(history, ImageInputPolicy())
    assert _images(seen) == [native]
    seen, _ = await _project(history, ImageInputPolicy(max_image_dimension=100))
    assert _images(seen) == []
    assert "could not be prepared" in str(seen[0].parts[0].content)


async def test_gif_policy_applies_after_counting_and_preserves_other_images() -> None:
    native = BinaryContent(_animated(size=(20, 20)), media_type="image/gif")
    url = ImageUrl("https://example.com/unchanged.gif")
    seen, retained = await _project(
        [ModelRequest(parts=[ToolReturnPart("view", [native, url], tool_call_id="view-1")])],
        ImageInputPolicy(support_gif=False),
    )
    assert _images(seen) == [url]
    assert "does not support GIF" in str(seen)
    assert _images(retained) == [native, url]


@pytest.mark.parametrize(
    "policy",
    [
        ImageInputPolicy(),
        ImageInputPolicy(split_large_images=False, max_image_bytes=0, max_image_dimension=0),
    ],
)
async def test_processing_pixel_limit_rejects_before_decoding_large_image(
    policy: ImageInputPolicy, monkeypatch: pytest.MonkeyPatch
) -> None:
    native = BinaryContent(_png((9000, 9000), mode="1", color=0), media_type="image/png")

    def fail_load(*args, **kwargs):
        pytest.fail("source pixels must not be decoded above the processing bound")

    monkeypatch.setattr(Image.Image, "load", fail_load)
    seen, retained = await _project([ModelRequest(parts=[UserPromptPart([native])])], policy)
    assert _images(seen) == []
    assert "could not be prepared" in str(seen[0].parts[0].content)
    assert _images(retained) == [native]


async def test_nested_tool_json_is_not_reinterpreted_as_model_image_content() -> None:
    native = BinaryContent(_png((12, 5000)), media_type="image/png")
    nested = {"items": [native], "tuple": (native,)}
    history = [ModelRequest(parts=[ToolReturnPart("view", [nested, (native,)], tool_call_id="view-1")])]
    seen, retained = await _project(history, ImageInputPolicy(max_images=0))
    assert next(message for message in seen if isinstance(message, ModelRequest)).parts[0].content == [
        nested,
        (native,),
    ]
    assert next(message for message in retained if isinstance(message, ModelRequest)).parts[0].content == [
        nested,
        (native,),
    ]
    assert history[0].parts[0].content == [nested, (native,)]


@pytest.mark.parametrize("model_source", ["definition", "resolver", "inference"])
@pytest.mark.parametrize("streaming", [False, True])
async def test_default_builder_filter_preserves_original_files_and_history_across_continuation(
    model_source: str, streaming: bool, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "original.png"
    data = _png((12, 5000))
    source.write_bytes(data)
    native = BinaryImage(data, media_type="image/png", identifier="attachment", vendor_metadata={"custom": "retained"})
    seen: list[list[ModelMessage]] = []

    async def respond(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del info
        seen.append(deepcopy(messages))
        yield "done"

    model = FunctionModel(stream_function=respond)
    if model_source == "inference":
        monkeypatch.setattr("a13n_harness.builder.infer_model", lambda *args, **kwargs: model)
    executable = HarnessBuilder().build(
        AgentSpec(model=None if model_source == "definition" else "logical:image"),
        model=model if model_source == "definition" else None,
        output_type=str,
    )
    bindings = RunBindings.embedded(
        model_resolver=(lambda ctx, model_id: _resolve(model)) if model_source == "resolver" else None
    )
    previous = HarnessState.new(
        message_history=(ModelRequest(parts=[UserPromptPart([native])]), ModelResponse(parts=[TextPart("old")]))
    )
    if streaming:
        async with executable.stream("continue", previous_state=previous, bindings=bindings) as run:
            async for _ in run:
                pass
            result = run.result
            assert result is not None
    else:
        result = await executable.run("continue", previous_state=previous, bindings=bindings)
    assert result.output_or_raise() == "done"
    assert len(_images(seen[0])) == 2
    assert result.state is not None
    assert _images(result.state.message_history) == [native]
    assert _images(result.all_messages()) == [native]
    assert _images(previous.message_history) == [native]
    await executable.run("again", previous_state=result.state, bindings=bindings)
    assert len(_images(seen[-1])) == 2
    assert source.read_bytes() == data
    assert native.data == data and native.identifier == "attachment"
    assert native.vendor_metadata == {"custom": "retained"}


async def _resolve(model: FunctionModel) -> FunctionModel:
    return model


@pytest.mark.parametrize(
    "spec,expected_count",
    [
        (AgentSpec(model_characteristics=HarnessModelCharacteristics(image_input=None)), 1),
        (NativeAgentSpec(), 2),
        (AgentSpec(model_characteristics=HarnessModelCharacteristics(image_input=ImageInputPolicy(max_images=0))), 0),
    ],
)
async def test_agent_spec_policy_and_opt_out(spec: NativeAgentSpec, expected_count: int) -> None:
    observed: list[int] = []

    async def respond(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del info
        observed.append(len(_images(messages)))
        yield "done"

    executable = HarnessBuilder().build(spec, output_type=str, model=FunctionModel(stream_function=respond))
    result = await executable.run(
        [BinaryContent(_png((12, 5000)), media_type="image/png")], bindings=RunBindings.embedded()
    )
    assert result.output_or_raise() == "done"
    assert observed == [expected_count]


@pytest.mark.parametrize("policy", [ImageInputPolicy(), ImageInputPolicy(max_images=0), None])
async def test_authored_image_capability_replaces_builder_default(policy: ImageInputPolicy | None) -> None:
    observed = []

    async def respond(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del info
        observed.extend(_images(messages))
        yield "done"

    native = BinaryContent(_png((12, 5000)), media_type="image/png")
    executable = HarnessBuilder().build(
        AgentSpec(model_characteristics=HarnessModelCharacteristics(image_input=policy)),
        output_type=str,
        model=FunctionModel(stream_function=respond),
        capabilities=(ImageFilterCapability(ImageInputPolicy(split_large_images=False)),),
    )
    result = await executable.run([native], bindings=RunBindings.embedded())
    assert result.output_or_raise() == "done"
    assert observed == [native]


async def test_real_function_tool_attachment_uses_the_same_request_filter() -> None:
    native = BinaryImage(_png((12, 5000)), media_type="image/png", identifier="tool")
    seen = []

    def view() -> ToolReturn:
        return ToolReturn("original reference", content=[native])

    async def respond(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        seen.append(deepcopy(messages))
        if len(seen) == 1:
            yield {0: DeltaToolCall(name="view", json_args="{}", tool_call_id="view-1")}
        else:
            yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=respond),
        capabilities=(Capability(tools=[view], id="test.view"),),
    )
    result = await executable.run("View the file", bindings=RunBindings.embedded())
    assert result.output_or_raise() == "done"
    assert len(_images(seen[-1])) == 2
    assert _images(result.all_messages()) == [native]


@pytest.mark.parametrize(
    "policy,expected_count", [(ImageInputPolicy(), 2), (ImageInputPolicy(max_images=1), 1), (None, 1)]
)
async def test_auxiliary_image_target_selects_its_own_policy(
    policy: ImageInputPolicy | None, expected_count: int
) -> None:
    observed = []
    native = _png((12, 5000))

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del info
        observed.append(len(_images(messages)))
        return ModelResponse(parts=[TextPart("image analysis")])

    provider = AgentMediaUnderstandingProvider(models={"image": FunctionModel(respond)}, image_input=policy)
    result = await provider.understand(
        MediaUnderstandingRequest(kind="image", media_type="image/png", source_name="image.png", source_bytes=native)
    )
    assert result.text == "image analysis"
    assert observed == [expected_count]


@pytest.mark.parametrize(
    "configuration",
    [{"image_split_overlap": 4096}, {"image_split_max_height": 0}, {"max_image_bytes": -1}, {"max_images": -1}],
)
async def test_image_policy_rejects_invalid_limits(configuration: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        ImageInputPolicy.model_validate(configuration)


async def test_image_policy_has_validated_native_spec_round_trip_and_schema() -> None:
    policy = ImageInputPolicy(max_images=3, split_large_images=False)
    characteristics = HarnessModelCharacteristics(image_input=policy)
    spec = AgentSpec(model_characteristics=characteristics)
    assert AgentSpec.model_validate_json(spec.model_dump_json()).model_characteristics == characteristics
    disabled = spec.with_updates(model_characteristics=characteristics.model_copy(update={"image_input": None}))
    assert disabled.model_characteristics is not None and disabled.model_characteristics.image_input is None
    assert spec.model_characteristics == characteristics
    schema = AgentSpec.model_json_schema_with_capabilities()
    assert "image_filter" not in schema["properties"] and "image_filter" not in AgentSpec.model_fields
    assert schema["$defs"]["HarnessModelCharacteristics"]["properties"]["image_input"]["anyOf"] == [
        {"$ref": "#/$defs/ImageInputPolicy"},
        {"type": "null"},
    ]
    assert schema["$defs"]["ImageInputPolicy"]["properties"]["max_image_bytes"]["default"] == 5 * 1024 * 1024


async def test_encoder_failure_becomes_request_only_explanation(monkeypatch: pytest.MonkeyPatch) -> None:
    native = BinaryContent(_png((100, 100)), media_type="image/png")

    def fail_save(*args, **kwargs):
        raise OSError("encoding failed")

    monkeypatch.setattr(Image.Image, "save", fail_save)
    seen, retained = await _project(
        [ModelRequest(parts=[UserPromptPart([native])])], ImageInputPolicy(max_image_dimension=50)
    )
    assert _images(seen) == []
    assert "could not be prepared" in str(seen[0].parts[0].content)
    assert _images(retained) == [native]


async def test_cmyk_jpeg_can_be_split_without_losing_spatial_coverage() -> None:
    buffer = io.BytesIO()
    with Image.new("CMYK", (20, 5000), (0, 255, 255, 0)) as source:
        source.save(buffer, format="JPEG")
    native = BinaryContent(buffer.getvalue(), media_type="image/jpeg", identifier="cmyk")
    seen, retained = await _project([ModelRequest(parts=[UserPromptPart([native])])], ImageInputPolicy())
    images = _images(seen)
    assert len(images) == 2
    for image, height in zip(images, (4096, 954), strict=True):
        with Image.open(io.BytesIO(image.data)) as decoded:
            assert decoded.size == (20, height)
            assert decoded.mode == "RGB"
    assert _images(retained) == [native]


async def test_detected_binary_format_drives_gif_policy_without_rewriting_original_metadata() -> None:
    native = BinaryContent(_animated(size=(20, 20)), media_type="image/png", vendor_metadata={"custom": "source"})
    seen, retained = await _project(
        [ModelRequest(parts=[UserPromptPart([native])])], ImageInputPolicy(support_gif=False)
    )
    assert _images(seen) == []
    assert "does not support GIF" in str(seen[0].parts[0].content)
    assert _images(retained) == [native]
    assert native.media_type == "image/png" and native.vendor_metadata == {"custom": "source"}


async def test_default_image_filter_and_self_healing_compose_once_without_changing_history() -> None:
    native = BinaryImage(_png((12, 5000)), media_type="image/png", identifier="original")
    reasoning = ThinkingPart(content="retained reasoning", id="rs_old")
    history = (
        ModelRequest(parts=[UserPromptPart([native])]),
        ModelResponse(parts=[reasoning, TextPart("previous")]),
    )
    original = deepcopy(history)
    previous = HarnessState.new(message_history=history)
    seen: list[list[ModelMessage]] = []

    async def respond(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del info
        seen.append(deepcopy(messages))
        if len(seen) == 1:
            raise ModelHTTPError(
                status_code=404,
                model_name="image-test",
                body={"code": 5008, "message": "Item with id 'rs_old' not found."},
            )
        yield "recovered"

    executable = HarnessBuilder().build(AgentSpec(), output_type=str, model=FunctionModel(stream_function=respond))
    leaves = []
    executable._agent.root_capability.apply(leaves.append)
    assert sum(isinstance(capability, ImageFilterCapability) for capability in leaves) == 1
    assert sum(isinstance(capability, SelfHealingModelCapability) for capability in leaves) == 1
    result = await executable.run("Continue", previous_state=previous, bindings=RunBindings.embedded())
    assert result.output_or_raise() == "recovered"
    assert len(seen) == 2
    for messages in seen:
        assert [image.identifier for image in _images(messages)] == ["original-segment-1", "original-segment-2"]
    assert any(isinstance(part, ThinkingPart) for message in seen[0] for part in message.parts)
    assert not any(isinstance(part, ThinkingPart) for message in seen[1] for part in message.parts)
    assert result.usage.requests == 2
    assert result.state is not None
    assert _images(result.state.message_history) == [native]
    assert result.state.message_history[:2] == original
    assert previous.message_history == original and history == original


@pytest.mark.parametrize("annotations", [None, "invalid"])
async def test_resumed_image_splitting_ignores_unusable_annotation_containers(annotations: object) -> None:
    native = BinaryImage(_png((20, 5000)), media_type="image/png", identifier="attachment")
    previous = HarnessState.new(
        message_history=(
            ModelRequest(parts=[UserPromptPart([native])], metadata={"a13n.content": annotations}),
            ModelResponse(parts=[TextPart("previous response")]),
        )
    )
    original = previous.model_dump_json()

    async def respond(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        assert [image.identifier for image in _images(messages)] == ["attachment-segment-1", "attachment-segment-2"]
        media = [
            item
            for message in messages
            if isinstance(message, ModelRequest)
            for item in request_input_content(message)
            if isinstance(item.value, BinaryContent)
        ]
        assert [item.metadata for item in media] == [ContentMetadata(), ContentMetadata()]
        yield "done"

    executable = HarnessBuilder().build(AgentSpec(), output_type=str, model=FunctionModel(stream_function=respond))
    result = await executable.run("Continue", previous_state=previous)
    assert result.output_or_raise() == "done"
    assert _images(result.state.message_history) == [native]
    assert previous.model_dump_json() == original


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("annotated_input", [False, True])
async def test_default_builder_split_images_retry_oversized_payload_without_changing_input(
    streaming: bool, annotated_input: bool
) -> None:
    native = BinaryImage(
        _png((10, 5000)), media_type="image/png", identifier="attachment", vendor_metadata={"detail": "high"}
    )
    input = ["look", native, "after"]
    if annotated_input:
        input = [
            ContentItem("look", ContentMetadata(source_id="authored-before")),
            ContentItem(
                native,
                ContentMetadata(display=False, source_id="attachment", media=True, file_id="file-one"),
            ),
            ContentItem("after", ContentMetadata(source_id="authored-after")),
        ]
    original = deepcopy(input)
    expected = content_items(input)
    seen: list[list[ModelMessage]] = []

    async def respond(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del info
        seen.append(deepcopy(messages))
        if len(seen) == 1:
            raise ModelHTTPError(status_code=413, model_name="image-test", body="payload too large")
        yield "recovered"

    executable = HarnessBuilder().build(AgentSpec(), output_type=str, model=FunctionModel(stream_function=respond))
    if streaming:
        async with executable.stream(input, bindings=RunBindings.embedded()) as run:
            async for _ in run:
                pass
            result = run.result
            assert result is not None
    else:
        result = await executable.run(input, bindings=RunBindings.embedded())
    assert result.output_or_raise() == "recovered"
    assert len(seen) == 2 and result.usage.requests == 2
    assert [image.identifier for image in _images(seen[0])] == ["attachment-segment-1", "attachment-segment-2"]
    assert _images(seen[1]) == []
    for attempt, messages in enumerate(seen):
        items = [
            item for message in messages if isinstance(message, ModelRequest) for item in request_input_content(message)
        ]
        assert expected[0] in items and expected[-1] in items
        media = [
            item
            for item in items
            if isinstance(item.value, BinaryContent)
            or (isinstance(item.value, TextContent) and "provider's size limit" in item.value.content)
        ]
        assert len(media) == 2
        metadata = expected[1].metadata
        if attempt == 1:
            metadata = metadata.model_copy(update={"display": False, "source_id": "a13n.model.self-healing"})
        assert [item.metadata for item in media] == [metadata, metadata]
    assert result.state is not None
    canonical = [
        item
        for message in result.state.message_history
        if isinstance(message, ModelRequest)
        for item in request_input_content(message)
        if isinstance(item.value, BinaryContent) or item.value in ("look", "after")
    ]
    assert canonical == expected
    assert _images(result.all_messages()) == [native]
    assert input == original
    assert native == (original[1].value if annotated_input else original[1])


@pytest.mark.parametrize("image_input", [{}, {"image_input": {}}])
def test_default_image_input_preserves_legacy_characteristics_json(image_input: dict[str, object]) -> None:
    legacy = (
        '{"capabilities":["audio_understanding","image_understanding"],'
        '"context_window_tokens":32000,"proactive_context_management_threshold":0.65,"compact_threshold":0.9}'
    )
    import json

    characteristics = HarnessModelCharacteristics.model_validate({**json.loads(legacy), **image_input})
    assert characteristics.image_input == ImageInputPolicy()
    assert characteristics.model_dump_json() == legacy
    assert "image_input" not in characteristics.model_dump()


def test_explicit_null_image_input_survives_serialization_and_revalidation() -> None:
    characteristics = HarnessModelCharacteristics(image_input=None)
    assert characteristics.model_dump(mode="json")["image_input"] is None
    assert HarnessModelCharacteristics.model_validate_json(characteristics.model_dump_json()).image_input is None
    spec = AgentSpec(model_characteristics=characteristics)
    assert AgentSpec.model_validate_json(spec.model_dump_json()).model_characteristics == characteristics


def test_image_input_object_uses_native_defaults_and_is_frozen() -> None:
    characteristics = HarnessModelCharacteristics.model_validate({"image_input": {"support_gif": False}})
    policy = characteristics.image_input
    assert policy is not None
    assert policy == ImageInputPolicy(support_gif=False)
    assert characteristics.model_dump(mode="json")["image_input"] == policy.model_dump(mode="json")
    with pytest.raises(ValueError, match="frozen"):
        policy.max_images = 1
    with pytest.raises(ValueError, match="frozen"):
        characteristics.image_input = None
