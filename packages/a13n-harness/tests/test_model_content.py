"""Native Google rendering never receives Harness presentation parameters."""

from __future__ import annotations

import json
from typing import Any

import httpx2
import pytest
from a13n_harness import HarnessBuilder, RunBindings
from a13n_harness.content import ContentItem, ContentMetadata, input_request, request_input_content
from a13n_harness.models import SelfHealingModelCapability, create_model_http_client
from a13n_harness.models.inference import RequestHeadersModel
from a13n_harness.tools._output import _render_tool_return
from google.genai import types
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.messages import (
    BinaryContent,
    ModelMessage,
    ModelRequest,
    ToolReturn,
    ToolReturnPart,
    UploadedFile,
    VideoUrl,
)
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.providers.google import GoogleProvider
from pydantic_ai.usage import UsageLimits

pytestmark = pytest.mark.anyio


def _media(kind: str) -> BinaryContent | VideoUrl | UploadedFile:
    if kind == "image":
        return BinaryContent(b"image", media_type="image/png", identifier="image-1")
    if kind == "video":
        return BinaryContent(b"video", media_type="video/mp4", vendor_metadata={"fps": 2})
    if kind == "url":
        return VideoUrl("https://www.youtube.com/watch?v=test", vendor_metadata={"fps": 2})
    return UploadedFile(
        file_id="https://generativelanguage.googleapis.com/v1beta/files/test",
        provider_name="google-gla",
        media_type="image/png",
        vendor_metadata={"media_resolution": {"level": "MEDIA_RESOLUTION_HIGH"}},
    )


@pytest.mark.parametrize("kind", ["image", "video", "url", "uploaded"])
@pytest.mark.parametrize("shape", ["list", "tuple", "tool"])
async def test_native_google_mapping_preserves_provider_options(kind: str, shape: str) -> None:
    original = _media(kind)
    if shape == "tool":
        result = _render_tool_return(ToolReturn("ok", content=[original]))
        assert result.content is None
        part = ToolReturnPart("view", content=result.return_value, metadata=result.metadata, tool_call_id="view-1")
        assert isinstance(part.content, list)
        outbound = part.content[1]
    else:
        values = [ContentItem(original, ContentMetadata(display=False, source_id="attachment"))]
        request = input_request(values if shape == "list" else tuple(values))
        assert request_input_content(request)[0].metadata.display is False
        outbound = request.parts[0].content[0]
    provider = GoogleProvider(api_key="test-key")
    model = GoogleModel("gemini-2.5-flash", provider=provider)
    try:
        mapped = await model._map_file_to_part(outbound)
        validated = types.Content(role="user", parts=[mapped])
    finally:
        await provider.client.aio.aclose()
    assert validated.parts is not None
    assert outbound.vendor_metadata == original.vendor_metadata == _media(kind).vendor_metadata
    if kind in {"video", "url"}:
        assert validated.parts[0].video_metadata.fps == 2
    if kind == "uploaded":
        assert validated.parts[0].media_resolution.level == "MEDIA_RESOLUTION_HIGH"


_RESPONSE = {
    "candidates": [{"content": {"parts": [{"text": "done"}], "role": "model"}, "finishReason": "STOP"}],
    "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1, "totalTokenCount": 2},
    "modelVersion": "gemini-test",
    "responseId": "response-1",
}


@pytest.mark.parametrize("operation", ["request", "stream", "count_tokens"])
async def test_native_google_operations_need_no_application_metadata_cleaning(operation: str) -> None:
    payloads: list[dict[str, Any]] = []

    async def handle(request: httpx2.Request) -> httpx2.Response:
        payloads.append(json.loads(request.content))
        if operation == "count_tokens":
            return httpx2.Response(200, json={"totalTokens": 1})
        if operation == "stream":
            return httpx2.Response(
                200, text=f"data: {json.dumps(_RESPONSE)}\n\n", headers={"content-type": "text/event-stream"}
            )
        return httpx2.Response(200, json=_RESPONSE)

    client = create_model_http_client(transport=httpx2.MockTransport(handle))
    media = BinaryContent(b"image", media_type="image/png")
    annotation = ContentMetadata(display=False, source_id="test", harness_ui={"attachment": {"name": "photo.png"}})
    history: list[ModelMessage] = [input_request([ContentItem(media, annotation)])]
    native = GoogleModel("gemini-2.5-flash", provider=GoogleProvider(api_key="test-key", http_client=client))
    model = RequestHeadersModel(native, common_headers={"x-test": "yes"})
    try:
        if operation == "count_tokens":
            assert (await model.count_tokens(history, None, ModelRequestParameters())).input_tokens == 1
        elif operation == "stream":
            async with model.request_stream(history, None, ModelRequestParameters()) as stream:
                async for _ in stream:
                    pass
        else:
            assert (await model.request(history, None, ModelRequestParameters())).text == "done"
    finally:
        await client.aclose()
    assert len(payloads) == 1
    assert payloads[0]["contents"][0]["parts"][0]["inlineData"]["data"] == "aW1hZ2U="
    assert "videoMetadata" not in payloads[0]["contents"][0]["parts"][0]
    assert "harness_ui" not in json.dumps(payloads)
    assert media.vendor_metadata is None
    assert request_input_content(history[0])[0].metadata == annotation


@pytest.mark.parametrize("repair", [False, True])
@pytest.mark.parametrize("count_before", [False, True])
async def test_harness_native_google_request_with_optional_history_repair(repair: bool, count_before: bool) -> None:
    requests: list[httpx2.Request] = []

    async def handle(request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith(":countTokens"):
            return httpx2.Response(200, json={"totalTokens": 1})
        requests.append(request)
        if repair and len(requests) == 1:
            return httpx2.Response(
                413, json={"error": {"code": 413, "message": "payload too large", "status": "RESOURCE_EXHAUSTED"}}
            )
        return httpx2.Response(
            200, text=f"data: {json.dumps(_RESPONSE)}\n\n", headers={"content-type": "text/event-stream"}
        )

    client = create_model_http_client(transport=httpx2.MockTransport(handle))
    native = GoogleModel("gemini-2.5-flash", provider=GoogleProvider(api_key="test-key", http_client=client))
    media = BinaryContent(b"image", media_type="image/png")
    try:
        executable = HarnessBuilder().build(
            AgentSpec(),
            output_type=str,
            model=native,
            capabilities=(SelfHealingModelCapability(),) if repair else (),
        )
        result = await executable.run(
            ["Inspect", ContentItem(media, ContentMetadata(display=False, source_id="test"))],
            bindings=RunBindings.embedded(),
            usage_limits=UsageLimits(count_tokens_before_request=count_before),
        )
    finally:
        await client.aclose()
    assert result.output_or_raise() == "done"
    assert len(requests) == (2 if repair else 1)
    content = [
        item
        for message in result.state.message_history
        if isinstance(message, ModelRequest)
        for item in request_input_content(message)
    ]
    saved = next(item for item in content if item.metadata.source_id in {"test", "a13n.model.self-healing"})
    assert saved.metadata.display is False
    assert media.vendor_metadata is None
