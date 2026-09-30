from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

import httpx2
import pytest
from a13n_harness import HarnessBuilder, RunBindings
from a13n_harness.models import SelfHealingModel, SelfHealingModelCapability, create_model_http_client
from a13n_harness.models.content import ModelContentCapability, _google_messages, _provider_content_model
from a13n_harness.models.inference import RequestHeadersModel
from a13n_harness.tools._output import _model_only_tool_return
from google.genai import types
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.messages import (
    BinaryContent,
    ModelMessage,
    ModelRequest,
    TextContent,
    ToolReturn,
    ToolReturnPart,
    UploadedFile,
    UserPromptPart,
    VideoUrl,
)
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.models.function import FunctionModel
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
async def test_google_native_mapping_preserves_provider_options_and_canonical_visibility(kind: str, shape: str) -> None:
    original = _media(kind)
    supplement = _model_only_tool_return(ToolReturn("ok", content=[original])).content
    assert isinstance(supplement, list)
    hidden = supplement[0]
    snapshot = deepcopy(hidden)
    part = (
        ToolReturnPart("view", content=tuple(supplement), tool_call_id="view-1")
        if shape == "tool"
        else UserPromptPart(supplement if shape == "list" else tuple(supplement))
    )
    messages: list[ModelMessage] = [ModelRequest(parts=[part])]
    projected = _google_messages(messages)
    outbound = projected[0].parts[0].content[0]
    provider = GoogleProvider(api_key="test-key")
    model = GoogleModel("gemini-2.5-flash", provider=provider)
    try:
        # Exercise the installed native adapter and the SDK's strict validation,
        # not just a FunctionModel that ignores provider parameter schemas.
        mapped = await model._map_file_to_part(outbound)
        validated = types.Content(role="user", parts=[mapped])
    finally:
        await provider.client.aio.aclose()

    assert validated.parts is not None
    assert hidden == snapshot
    assert hidden.vendor_metadata["display"] is False
    assert hidden.vendor_metadata["source_id"] == "a13n.tool"
    assert original.vendor_metadata == _media(kind).vendor_metadata
    assert outbound.vendor_metadata == original.vendor_metadata
    assert outbound.identifier == hidden.identifier
    if isinstance(hidden, BinaryContent):
        assert outbound.data == hidden.data
    if kind in {"video", "url"}:
        assert validated.parts[0].video_metadata.fps == 2
    if kind == "uploaded":
        assert validated.parts[0].media_resolution.level == "MEDIA_RESOLUTION_HIGH"
    if shape in {"tuple", "tool"}:
        assert isinstance(projected[0].parts[0].content, tuple)


_RESPONSE = {
    "candidates": [{"content": {"parts": [{"text": "done"}], "role": "model"}, "finishReason": "STOP"}],
    "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1, "totalTokenCount": 2},
    "modelVersion": "gemini-test",
    "responseId": "response-1",
}


@pytest.mark.parametrize("operation", ["request", "stream", "count_tokens"])
async def test_google_projection_covers_native_operations_without_mutating_history(operation: str) -> None:
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
    media = BinaryContent(b"image", media_type="image/png", vendor_metadata={"display": False, "source_id": "test"})
    history: list[ModelMessage] = [ModelRequest(parts=[UserPromptPart([media])])]
    native = GoogleModel("gemini-2.5-flash", provider=GoogleProvider(api_key="test-key", http_client=client))
    model = _provider_content_model(RequestHeadersModel(native, common_headers={"x-test": "yes"}))
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
    assert media.vendor_metadata == {"display": False, "source_id": "test"}
    assert model is not native
    assert _provider_content_model(model) is model


@pytest.mark.parametrize("repair", [False, True])
@pytest.mark.parametrize("count_before", [False, True])
async def test_harness_installs_google_projection_below_optional_history_repair(
    repair: bool, count_before: bool
) -> None:
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
    media = BinaryContent(b"image", media_type="image/png", vendor_metadata={"display": False, "source_id": "test"})
    try:
        executable = HarnessBuilder().build(
            AgentSpec(),
            output_type=str,
            model=native,
            capabilities=(SelfHealingModelCapability(),) if repair else (),
        )
        result = await executable.run(
            ["Inspect", media],
            bindings=RunBindings.embedded(),
            usage_limits=UsageLimits(count_tokens_before_request=count_before),
        )
    finally:
        await client.aclose()

    assert result.output_or_raise() == "done"
    assert len(requests) == (2 if repair else 1)
    assert result.state is not None
    content = [
        item
        for message in result.state.message_history
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart) and not isinstance(part.content, str)
        for item in part.content
    ]
    if repair:
        reminder = next(
            item for item in content if isinstance(item, TextContent) and "image was removed" in item.content
        )
        assert reminder.metadata["display"] is False
        assert not any(isinstance(item, BinaryContent) for item in content)
    else:
        saved = next(item for item in content if isinstance(item, BinaryContent))
        assert saved.vendor_metadata == media.vendor_metadata
    assert media.vendor_metadata == {"display": False, "source_id": "test"}


async def test_google_projection_retains_native_fallback_and_wrapper_ownership() -> None:
    provider = GoogleProvider(api_key="test-key")
    google = GoogleModel("gemini-2.5-flash", provider=provider)
    other = FunctionModel(lambda messages, info: "unused")
    fallback = FallbackModel(other, google)
    original = SelfHealingModel(fallback)
    try:
        projected = _provider_content_model(original)
        assert isinstance(projected, SelfHealingModel)
        assert isinstance(projected.wrapped, FallbackModel)
        assert projected.wrapped.models[0] is other
        assert projected.wrapped.models[1].wrapped is google
        assert original.wrapped is fallback
        assert fallback.models == [other, google]
        assert _provider_content_model(other) is other
    finally:
        await provider.client.aio.aclose()


def test_definition_cannot_replace_mandatory_model_content_boundary() -> None:
    from a13n_harness.errors import DefinitionError

    with pytest.raises(DefinitionError) as error:
        HarnessBuilder().build(
            AgentSpec(),
            output_type=str,
            model=FunctionModel(lambda messages, info: "unused"),
            capabilities=(ModelContentCapability(),),
        )
    assert error.value.code == "capability_scope_invalid"
