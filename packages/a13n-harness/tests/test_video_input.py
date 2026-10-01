from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import httpx2
import pytest
from a13n_harness import (
    AgentSpec,
    HarnessBuilder,
    HarnessModelCharacteristics,
    ModelCapability,
    RunConfiguration,
    VideoInputPolicy,
)
from a13n_harness.content import ContentItem, ContentMetadata, content_items, request_input_content
from a13n_harness.filters.video_url import project_video_urls
from a13n_harness.models.self_healing import SelfHealingModel
from a13n_harness.tools._output import _apply_result_policy, _render_tool_return
from a13n_harness.tools.metadata import ToolOutputPolicy
from a13n_harness.toolsets.video_url import VideoUrlToolset
from a13n_harness.video_input import encoded_video_bytes
from pydantic_ai import BinaryContent, ToolReturn, VideoUrl
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolReturnPart, UserPromptPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.function import FunctionModel

pytestmark = pytest.mark.anyio
VIDEO = "https://cdn.example.com/video.mp4"
YOUTUBE = "https://www.youtube.com/watch?v=9hE5-98ZeCg"


def characteristics(limit=10 * 1024 * 1024):
    return HarnessModelCharacteristics(
        capabilities={ModelCapability.VIDEO_UNDERSTANDING},
        url_input={"video": ["youtube"]},
        video_input=VideoInputPolicy(max_video_bytes=limit),
    )


def binary_videos(messages):
    return [
        item
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, (UserPromptPart, ToolReturnPart))
        for item in (part.content if isinstance(part.content, (list, tuple)) else [part.content])
        if isinstance(item, BinaryContent) and item.media_type.startswith("video/")
    ]


@pytest.mark.parametrize("length", [0, 1, 2, 3, 4, 5, 6, 7864320, 7864321])
def test_base64_size_rounding_without_encoding(length):
    import base64

    assert encoded_video_bytes(length) == len(base64.b64encode(b"x" * length))
    policy = VideoInputPolicy()
    assert policy.max_raw_bytes == 7864320
    assert encoded_video_bytes(policy.max_raw_bytes) == policy.max_video_bytes
    assert encoded_video_bytes(policy.max_raw_bytes + 1) > policy.max_video_bytes


@pytest.mark.parametrize("surface", ["user", "tool-scalar", "tool-tuple"])
@pytest.mark.parametrize("size,kept", [(6, True), (7, False)])
def test_projection_bounds_all_inline_surfaces_and_detaches_bytes(surface, size, kept):
    native = BinaryContent(b"x" * size, media_type="video/mp4", identifier="original", vendor_metadata={"fps": 1.0})
    part = (
        UserPromptPart([native])
        if surface == "user"
        else ToolReturnPart("video", native if surface == "tool-scalar" else (native,), tool_call_id="call")
    )
    history = [ModelRequest(parts=[part], metadata={"application": {"source": "retained"}})]
    snapshot = deepcopy(history)
    projected = project_video_urls(history, characteristics(8))
    assert projected is not None and projected is not history
    assert bool(binary_videos(projected)) is kept
    if kept:
        assert binary_videos(projected)[0].data is native.data
        assert binary_videos(projected)[0].vendor_metadata == {"fps": 1.0}
    else:
        assert "video_input_too_large" in repr(projected)
    assert history == snapshot


def test_aggregate_budget_and_direct_url_do_not_delegate_download():
    first = BinaryContent(b"123", media_type="video/mp4")
    second = BinaryContent(b"4567", media_type="video/webm")
    history = [ModelRequest(parts=[UserPromptPart([first, "between", VideoUrl(YOUTUBE), second, VideoUrl(VIDEO)])])]
    projected = project_video_urls(history, characteristics(8))
    assert binary_videos(projected) == [first]
    assert "video_input_too_large" in repr(projected)
    assert "read_video_url" in repr(projected)
    assert "between" in repr(projected) and YOUTUBE in repr(projected)
    assert binary_videos(history) == [first, second]


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("annotated", [False, True])
@pytest.mark.parametrize("status,body", [(413, "payload too large"), (400, "Request payload size exceeds the limit")])
async def test_builder_heals_small_video_on_exact_provider_error_without_changing_history(
    streaming, annotated, status, body
):
    native = BinaryContent(b"small", media_type="video/mp4", identifier="source", vendor_metadata={"fps": 1.0})
    input = ["before", native, "after"]
    if annotated:
        input = [
            ContentItem("before", ContentMetadata(source_id="authored")),
            ContentItem(native, ContentMetadata(display=False, source_id="video", file_id="file-one")),
            ContentItem("after", ContentMetadata(source_id="authored")),
        ]
    snapshot = deepcopy(input)
    seen = []

    async def respond(messages, info):
        seen.append(deepcopy(messages))
        if len(seen) == 1:
            raise ModelHTTPError(status_code=status, model_name="test-video", body=body)
        yield "recovered"

    executable = HarnessBuilder().build(
        AgentSpec(model_characteristics=characteristics()),
        output_type=str,
        model=FunctionModel(stream_function=respond),
    )
    if streaming:
        async with executable.stream(input) as run:
            async for _ in run:
                pass
            result = run.result
    else:
        result = await executable.run(input)
    assert result.output_or_raise() == "recovered"
    assert len(seen) == 2 and result.usage.requests == 2
    assert binary_videos(seen[0]) == [native] and binary_videos(seen[1]) == []
    assert "A video was removed" in repr(seen[1])
    assert binary_videos(result.state.message_history) == [native]
    canonical = [
        item
        for message in result.state.message_history
        if isinstance(message, ModelRequest)
        for item in request_input_content(message)
        if isinstance(item.value, BinaryContent) or item.value in ("before", "after")
    ]
    assert canonical == content_items(input) and input == snapshot


@pytest.mark.parametrize(
    "status,body",
    [
        (400, "invalid MIME type"),
        (400, "unsupported model"),
        (401, "unauthorized"),
        (403, "forbidden"),
        (429, "rate limit"),
        (500, "internal error"),
    ],
)
async def test_unrelated_errors_do_not_remove_video_or_retry(status, body):
    seen = []

    def respond(messages, info):
        seen.append(deepcopy(messages))
        raise ModelHTTPError(status_code=status, model_name="test", body=body)

    model = SelfHealingModel(FunctionModel(respond))
    history = [ModelRequest(parts=[UserPromptPart([BinaryContent(b"video", media_type="video/mp4")])])]
    snapshot = deepcopy(history)
    with pytest.raises(ModelHTTPError):
        await model.request(history, None, ModelRequestParameters())
    assert len(seen) == 1 and history == snapshot


@pytest.mark.parametrize("mode", ["no-content", "opt-out", "second-failure"])
async def test_payload_replay_requires_repair_and_never_loops(mode):
    calls = []

    def respond(messages, info):
        calls.append(deepcopy(messages))
        raise ModelHTTPError(status_code=413, model_name="test", body="payload too large")

    model = SelfHealingModel(FunctionModel(respond), rules=() if mode == "opt-out" else None)
    value = VideoUrl(YOUTUBE) if mode == "no-content" else BinaryContent(b"video", media_type="video/mp4")
    with pytest.raises(ModelHTTPError):
        await model.request([ModelRequest(parts=[UserPromptPart([value])])], None, ModelRequestParameters())
    assert len(calls) == (2 if mode == "second-failure" else 1)


def mock_download(monkeypatch, handler):
    factory = httpx2.AsyncClient
    monkeypatch.setattr(
        "a13n_harness._video_urls.httpx2.AsyncClient",
        lambda **kwargs: factory(transport=httpx2.MockTransport(handler), **kwargs),
    )


async def read_url(url=VIDEO, *, limit=8, configuration=None, media_type=None):
    ctx = SimpleNamespace(
        deps=SimpleNamespace(
            model_characteristics=characteristics(limit), configuration=configuration or RunConfiguration()
        )
    )
    return await VideoUrlToolset().read_video_url(ctx, url, media_type=media_type)


async def test_direct_download_settlement_and_google_sdk_validation(monkeypatch):
    from google.genai.types import Part
    from pydantic_ai.models.google import GoogleModel
    from pydantic_ai.providers.google import GoogleProvider

    mock_download(
        monkeypatch, lambda request: httpx2.Response(200, content=b"123456", headers={"content-type": "video/mp4"})
    )
    result = await read_url()
    assert isinstance(result, ToolReturn) and result.content[0].data == b"123456"
    settled = _render_tool_return(
        await _apply_result_policy(result, ToolOutputPolicy(max_inline_bytes=256 * 1024, max_output_bytes=256 * 1024))
    )
    native = settled.return_value[1]
    assert isinstance(native, BinaryContent) and native.vendor_metadata is None
    model = GoogleModel("gemini-2.5-pro", provider=GoogleProvider(api_key="test-only-not-a-real-key"))
    part = Part.model_validate(await model._map_file_to_part(native))
    assert part.inline_data.data == b"123456" and part.inline_data.mime_type == "video/mp4"


@pytest.mark.parametrize(
    "status,headers,data,code",
    [
        (200, {"content-type": "video/mp4"}, b"1234567", "video_input_too_large"),
        (200, {"content-type": "text/html"}, b"page", "video_mime_invalid"),
        (200, {"content-type": "video/mp4"}, b"", "video_empty"),
        (401, {}, b"", "video_download_http_error"),
        (403, {}, b"", "video_download_http_error"),
        (413, {}, b"", "video_download_http_error"),
        (429, {}, b"", "video_download_http_error"),
    ],
)
async def test_download_errors_are_structured_not_model_payload_recovery(monkeypatch, status, headers, data, code):
    mock_download(monkeypatch, lambda request: httpx2.Response(status, headers=headers, content=data))
    result = await read_url()
    assert code in repr(result) and not isinstance(result, ToolReturn)


@pytest.mark.parametrize(
    "target",
    [
        "https://denied.example/video.mp4",
        "http://cdn.example.com/video.mp4",
        YOUTUBE,
        "https://cdn.example.com/video.mp4?token=secret",
    ],
)
async def test_redirects_are_validated_before_following(monkeypatch, target):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx2.Response(302, headers={"location": target})

    mock_download(monkeypatch, handler)
    result = await read_url(configuration=RunConfiguration(allowed_hosts=frozenset({"cdn.example.com"})))
    assert not isinstance(result, ToolReturn) and calls == [VIDEO]


async def test_allowed_relative_redirect_and_header_mime_for_extensionless_url(monkeypatch):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if len(calls) == 1:
            return httpx2.Response(302, headers={"location": "/resource"})
        return httpx2.Response(200, content=b"123456", headers={"content-type": "video/webm"})

    mock_download(monkeypatch, handler)
    result = await read_url(configuration=RunConfiguration(allowed_hosts=frozenset({"cdn.example.com"})))
    assert isinstance(result, ToolReturn) and result.content[0].media_type == "video/webm"
    assert calls == [VIDEO, "https://cdn.example.com/resource"]


async def test_youtube_unsupported_or_restricted_never_downloads(monkeypatch):
    def no_network(request):
        raise AssertionError("YouTube must not download")

    mock_download(monkeypatch, no_network)
    ctx = SimpleNamespace(
        deps=SimpleNamespace(
            model_characteristics=HarnessModelCharacteristics(capabilities={ModelCapability.VIDEO_UNDERSTANDING}),
            configuration=RunConfiguration(),
        )
    )
    assert "video_url_unsupported" in repr(await VideoUrlToolset().read_video_url(ctx, YOUTUBE))
    assert "video_url_unsupported" in repr(
        await read_url(YOUTUBE, configuration=RunConfiguration(allowed_hosts={"youtube.com"}))
    )


async def test_download_timeout_is_structured(monkeypatch):
    def handler(request):
        raise httpx2.ReadTimeout("timed out", request=request)

    mock_download(monkeypatch, handler)
    assert "video_download_timeout" in repr(await read_url())


@pytest.mark.parametrize("preflight", [False, True])
async def test_oversized_download_stops_reading_and_closes_stream(monkeypatch, preflight):
    consumed = []
    closed = []

    class Stream(httpx2.AsyncByteStream):
        async def __aiter__(self):
            for chunk in (b"123", b"4567", b"never-read"):
                consumed.append(chunk)
                yield chunk

        async def aclose(self):
            closed.append(True)

    headers = {"content-type": "video/mp4"}
    if preflight:
        headers["content-length"] = "7"
    mock_download(monkeypatch, lambda request: httpx2.Response(200, headers=headers, stream=Stream()))
    assert "video_input_too_large" in repr(await read_url())
    assert consumed == ([] if preflight else [b"123", b"4567"])
    assert closed == [True]


async def test_cancelled_download_propagates_and_closes_response(monkeypatch):
    import asyncio

    entered = asyncio.Event()
    closed = []

    class Stream(httpx2.AsyncByteStream):
        async def __aiter__(self):
            entered.set()
            await asyncio.Future()
            yield b"never"

        async def aclose(self):
            closed.append(True)

    mock_download(
        monkeypatch, lambda request: httpx2.Response(200, headers={"content-type": "video/mp4"}, stream=Stream())
    )
    task = asyncio.create_task(read_url())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed == [True]


async def test_partial_stream_payload_failure_is_not_replayed():
    calls = []

    async def respond(messages, info):
        calls.append(deepcopy(messages))
        yield "partial"
        raise ModelHTTPError(status_code=413, model_name="test", body="payload too large")

    model = SelfHealingModel(FunctionModel(stream_function=respond))
    history = [ModelRequest(parts=[UserPromptPart([BinaryContent(b"video", media_type="video/mp4")])])]
    snapshot = deepcopy(history)
    with pytest.raises(ModelHTTPError):
        async with model.request_stream(history, None, ModelRequestParameters()) as stream:
            async for _ in stream:
                pass
    assert len(calls) == 1 and history == snapshot


@pytest.mark.parametrize("enabled", [False, True])
async def test_retry_respects_request_budget_and_builder_opt_out(enabled):
    from pydantic_ai.usage import UsageLimits

    seen = []

    async def respond(messages, info):
        seen.append(deepcopy(messages))
        raise ModelHTTPError(status_code=413, model_name="test", body="payload too large")
        yield "never"

    native = BinaryContent(b"video", media_type="video/mp4")
    executable = HarnessBuilder(self_healing_enabled=enabled).build(
        AgentSpec(model_characteristics=characteristics(), usage_limits=UsageLimits(request_limit=1)),
        output_type=str,
        model=FunctionModel(stream_function=respond),
    )
    result = await executable.run([native])
    assert result.status == "failed" and len(seen) == 1 and result.usage.requests == 1
    assert binary_videos(result.state.message_history) == [native]


async def test_repair_removes_video_and_images_but_leaves_audio_documents_and_native_urls():
    calls = []

    def respond(messages, info):
        calls.append(deepcopy(messages))
        if len(calls) == 1:
            raise ModelHTTPError(status_code=413, model_name="test", body="payload too large")
        return ModelResponse(parts=[TextPart("done")])

    image = BinaryContent(b"image", media_type="image/png")
    video = BinaryContent(b"video", media_type="video/mp4")
    retained = [
        BinaryContent(b"audio", media_type="audio/wav"),
        BinaryContent(b"pdf", media_type="application/pdf"),
        VideoUrl(YOUTUBE),
    ]
    history = [
        ModelRequest(
            parts=[UserPromptPart([image, video, *retained]), ToolReturnPart("read", video, tool_call_id="call")]
        )
    ]
    await SelfHealingModel(FunctionModel(respond)).request(history, None, ModelRequestParameters())
    assert len(calls) == 2 and not binary_videos(calls[1])
    assert calls[1][0].parts[0].content[2:] == retained
    assert "An image was removed" in repr(calls[1]) and "A video was removed" in repr(calls[1])


def test_video_defaults_keep_characteristics_serialization_compatible():
    legacy = HarnessModelCharacteristics(capabilities={ModelCapability.VIDEO_UNDERSTANDING})
    value = legacy.model_dump(mode="json")
    assert "url_input" not in value and "video_input" not in value
    assert HarnessModelCharacteristics.model_validate(value) == legacy
    with pytest.raises(ValueError):
        HarnessModelCharacteristics(video_input=None)
    with pytest.raises(ValueError):
        VideoInputPolicy(max_video_bytes=0)


@pytest.mark.parametrize("status,body", [(413, "payload too large"), (400, "Request payload size exceeds the limit")])
@pytest.mark.parametrize("count_before", [False, True])
async def test_google_wire_inline_video_payload_repair_preserves_canonical_annotations(status, body, count_before):
    import json

    from a13n_harness.models import create_model_http_client
    from pydantic_ai.models.google import GoogleModel
    from pydantic_ai.providers.google import GoogleProvider
    from pydantic_ai.usage import UsageLimits

    payloads = []
    response = {
        "candidates": [{"content": {"role": "model", "parts": [{"text": "done"}]}, "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1, "totalTokenCount": 2},
        "modelVersion": "gemini-2.5-flash",
    }

    async def handle(request):
        if request.url.path.endswith(":countTokens"):
            return httpx2.Response(200, json={"totalTokens": 1})
        payloads.append(json.loads(request.content))
        if len(payloads) == 1:
            return httpx2.Response(
                status, json={"error": {"code": status, "message": body, "status": "INVALID_ARGUMENT"}}
            )
        return httpx2.Response(
            200, text=f"data: {json.dumps(response)}\n\n", headers={"content-type": "text/event-stream"}
        )

    native = BinaryContent(b"raw-video", media_type="video/mp4")
    annotation = ContentMetadata(display=False, source_id="attachment", file_id="file-one")
    async with create_model_http_client(transport=httpx2.MockTransport(handle)) as client:
        model = GoogleModel("gemini-2.5-flash", provider=GoogleProvider(api_key="test-only", http_client=client))
        executable = HarnessBuilder().build(
            AgentSpec(model_characteristics=characteristics()), output_type=str, model=model
        )
        result = await executable.run(
            ["Inspect", ContentItem(native, annotation)],
            usage_limits=UsageLimits(count_tokens_before_request=count_before),
        )
    assert result.output_or_raise() == "done" and result.usage.requests == 2
    assert len(payloads) == 2
    assert any(
        part.get("inlineData", {}).get("mime_type") == "video/mp4"
        for content in payloads[0]["contents"]
        for part in content["parts"]
    ), payloads[0]["contents"]
    assert not any("inlineData" in part for content in payloads[1]["contents"] for part in content["parts"])
    assert "A video was removed" in json.dumps(payloads[1])
    saved = [
        item
        for message in result.state.message_history
        if isinstance(message, ModelRequest)
        for item in request_input_content(message)
        if isinstance(item.value, BinaryContent)
    ]
    assert saved == [ContentItem(native, annotation)] and native.vendor_metadata is None
