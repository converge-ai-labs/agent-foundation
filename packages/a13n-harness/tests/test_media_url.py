"""Direct media acquisition, native-first dispatch, and fallback admission."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx2
import pytest
from a13n_harness import (
    AgentSpec,
    HarnessBuilder,
    HarnessModelCharacteristics,
    ModelCapability,
    RunBindings,
    RunConfiguration,
)
from a13n_harness.capabilities.tool_review import ToolReviewAssessment, ToolReviewPolicy, ToolReviewResult
from a13n_harness.metering import ModelUsageBinding
from a13n_harness.tools import (
    InvocationPolicyCapability,
    InvocationPolicyDecision,
    ToolPermissions,
    ToolPermissionsCapability,
)
from a13n_harness.toolsets.file_media import (
    AgentMediaUnderstandingProvider,
    MediaUnderstandingError,
    MediaUnderstandingResult,
)
from a13n_harness.toolsets.media_url import MediaUrlToolset
from a13n_harness.usage import ModelUsageRecord
from pydantic_ai import ToolReturn
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.usage import RequestUsage

pytestmark = pytest.mark.anyio
KINDS = [("image", "image/png"), ("video", "video/mp4"), ("audio", "audio/wav")]
URL = "https://cdn.example.com/resource"


class Understanding:
    def __init__(self, kinds=("image", "video", "audio"), *, error=None):
        self.kinds = kinds
        self.error = error
        self.requests = []

    def supports(self, kind):
        return kind in self.kinds

    async def understand(self, request, *, usage=None):
        self.requests.append(request)
        if self.error:
            raise self.error
        return MediaUnderstandingResult(text="focused analysis")


def context(*, native=None, provider=None, limit=8):
    return SimpleNamespace(
        deps=SimpleNamespace(
            model_characteristics=HarnessModelCharacteristics(
                capabilities={ModelCapability(f"{native}_understanding")} if native else set(),
                image_input=None,
                video_input={"max_video_bytes": limit},
            ),
            configuration=RunConfiguration(),
            file_media_understanding=provider,
            usage_attribution=ModelUsageBinding.standalone(source="test").ledger,
        ),
        tool_call_id="call-media",
    )


def mock_download(monkeypatch, mime, *, content=b"media bytes", headers=None):
    calls = []
    factory = httpx2.AsyncClient

    def handler(request):
        calls.append(str(request.url))
        return httpx2.Response(200, content=content, headers=headers if headers is not None else {"content-type": mime})

    monkeypatch.setattr(
        "a13n_harness._media_urls.httpx2.AsyncClient",
        lambda **kwargs: factory(transport=httpx2.MockTransport(handler), **kwargs),
    )
    return calls


@pytest.mark.parametrize("kind,mime", KINDS)
@pytest.mark.parametrize("native", [True, False])
async def test_all_kinds_share_native_first_dispatch(monkeypatch, kind, mime, native):
    calls = mock_download(monkeypatch, mime)
    provider = Understanding()
    result = await MediaUrlToolset().read_media_url(
        context(native=kind if native else None, provider=provider, limit=1024), URL, instructions="Read the label."
    )
    assert calls == [URL]
    assert isinstance(result, ToolReturn)
    if native:
        assert result.content[0].data == b"media bytes"
        assert result.content[0].media_type == mime
        assert "Read the label." in result.return_value
        assert not provider.requests
    else:
        assert result.return_value == "focused analysis"
        request = provider.requests[0]
        assert request.kind == kind and request.media_type == mime
        assert request.source is None and request.source_name == URL
        assert request.source_bytes == b"media bytes"
        assert request.instructions == "Read the label."


@pytest.mark.parametrize("kind,mime", KINDS)
@pytest.mark.parametrize("header", [None, "application/octet-stream"])
async def test_mime_inference_from_explicit_type_or_url(monkeypatch, kind, mime, header):
    headers = {"content-type": header} if header else {}
    mock_download(monkeypatch, mime, headers=headers)
    ctx = context(native=kind, limit=1024)
    explicit = await MediaUrlToolset().read_media_url(ctx, URL, media_type=mime)
    inferred = await MediaUrlToolset().read_media_url(
        ctx, URL + {"image": ".png", "video": ".mp4", "audio": ".wav"}[kind]
    )
    assert isinstance(explicit, ToolReturn) and explicit.content[0].media_type == mime
    assert isinstance(inferred, ToolReturn) and inferred.content[0].media_type == mime


async def test_response_mime_is_authoritative_and_cannot_enable_unconfigured_kind(monkeypatch):
    mock_download(monkeypatch, "video/mp4")
    provider = Understanding(kinds=("image",))
    result = await MediaUrlToolset().read_media_url(context(provider=provider), URL + ".png", media_type="image/png")
    assert result["error"]["code"] == "media_understanding_unavailable"
    assert not provider.requests


@pytest.mark.parametrize("mode", ["native", "fallback"])
async def test_source_budget_and_actual_receiver_video_budget_are_separate(monkeypatch, mode):
    mock_download(monkeypatch, "video/mp4", content=b"1234567")
    provider = Understanding(kinds=("video",))
    result = await MediaUrlToolset().read_media_url(
        context(native="video" if mode == "native" else None, provider=provider), URL
    )
    if mode == "native":
        assert result["error"]["code"] == "media_input_too_large"
        assert not provider.requests
    else:
        assert isinstance(result, ToolReturn) and provider.requests[0].source_bytes == b"1234567"


@pytest.mark.parametrize("kind,mime", KINDS)
async def test_fallback_source_limits_are_enforced_before_inference(monkeypatch, kind, mime):
    mock_download(monkeypatch, mime, content=b"1234567")
    monkeypatch.setattr("a13n_harness.toolsets.media_url.media_source_byte_limit", lambda kind: 6)
    provider = Understanding()
    result = await MediaUrlToolset().read_media_url(context(provider=provider), URL)
    assert result["error"]["code"] == "media_input_too_large"
    assert not provider.requests


@pytest.mark.parametrize(
    "error,code",
    [
        (MediaUnderstandingError("media_understanding_timeout"), "media_understanding_timeout"),
        (RuntimeError("private provider details"), "media_understanding_failed"),
    ],
)
async def test_fallback_failure_is_structured_without_ambient_retry(monkeypatch, error, code):
    mock_download(monkeypatch, "audio/wav")
    provider = Understanding(error=error)
    monkeypatch.setattr(
        AgentMediaUnderstandingProvider, "from_environment", lambda **kwargs: pytest.fail("unexpected fallback")
    )
    result = await MediaUrlToolset().read_media_url(context(provider=provider), URL)
    assert result["error"]["code"] == code
    assert "private provider details" not in repr(result)
    assert len(provider.requests) == 1


async def test_fallback_cancellation_propagates(monkeypatch):
    mock_download(monkeypatch, "audio/wav")
    with pytest.raises(asyncio.CancelledError):
        await MediaUrlToolset().read_media_url(context(provider=Understanding(error=asyncio.CancelledError())), URL)


@pytest.mark.parametrize("kind,mime", KINDS)
async def test_visibility_discovers_only_configured_kind_without_resolving(monkeypatch, kind, mime):
    for candidate, _ in KINDS:
        monkeypatch.delenv(f"A13N_HARNESS_{candidate.upper()}_UNDERSTANDING_MODEL", raising=False)
    monkeypatch.setenv(f"A13N_HARNESS_{kind.upper()}_UNDERSTANDING_MODEL", "invalid-but-not-yet-resolved")
    monkeypatch.setenv(f"A13N_HARNESS_{kind.upper()}_UNDERSTANDING_MODEL_SETTINGS", "invalid-json")
    monkeypatch.setattr(
        AgentMediaUnderstandingProvider, "from_environment", lambda **kwargs: pytest.fail("eager resolution")
    )
    seen = []

    async def respond(messages, info):
        seen.extend(info.function_tools)
        yield "done"

    agent = HarnessBuilder().build(AgentSpec(), output_type=str, model=FunctionModel(stream_function=respond))
    await agent.run("List tools")
    tool = next(tool for tool in seen if tool.name == "read_media_url")
    assert f"HTTP(S) {kind} downloads" in tool.description
    assert all(f"HTTP(S) {other} downloads" not in tool.description for other, _ in KINDS if other != kind)


class Reviewer:
    async def review(self, request, *, context):
        assert request.tool_id == "media.read_video_url"
        return ToolReviewResult(assessment=ToolReviewAssessment(risk="extra_high"))


class Allow:
    async def __call__(self, invocation, metadata, *, context):
        return InvocationPolicyDecision.allow()


@pytest.mark.parametrize("mode", ["allow", "deny", "ask", "review"])
@pytest.mark.parametrize("kind,mime", KINDS)
async def test_new_tool_uses_existing_permission_identity(monkeypatch, mode, kind, mime):
    calls = mock_download(monkeypatch, mime, content=b"123")
    seen = []

    async def stream(messages, info):
        if any(
            isinstance(part, ToolReturnPart)
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
        ):
            yield "done"
        else:
            seen.extend(info.function_tools)
            yield {0: DeltaToolCall(name="read_media_url", json_args=json.dumps({"url": URL}), tool_call_id="url-call")}

    agent = HarnessBuilder().build(
        AgentSpec(
            model_characteristics=HarnessModelCharacteristics(
                capabilities={ModelCapability(f"{kind}_understanding")}, image_input=None
            )
        ),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(
            ToolPermissionsCapability(
                ToolPermissions(rules={"media.read_video_url": mode}),
                reviewer=Reviewer(),
                policy=ToolReviewPolicy(on_flagged="deny"),
            ),
        ),
    )
    result = await agent.run(
        "Read URL", bindings=RunBindings.embedded(capabilities=(InvocationPolicyCapability(evaluator=Allow()),))
    )
    assert [tool.name for tool in seen].count("read_media_url") == 1
    assert calls == ([URL] if mode == "allow" else [])
    if mode == "ask":
        assert result.deferred.approvals[0].tool_name == "read_media_url"


async def test_environment_fallback_records_nested_model_usage(monkeypatch):
    mock_download(monkeypatch, "video/mp4", content=b"1234567")
    monkeypatch.setenv("A13N_HARNESS_VIDEO_UNDERSTANDING_MODEL", "test:video")
    requests = []

    def infer(messages, info):
        requests.append(messages)
        return ModelResponse(parts=[TextPart("video analysis")], usage=RequestUsage(input_tokens=7, output_tokens=3))

    monkeypatch.setattr("a13n_harness.toolsets.file_media.infer_model", lambda model: FunctionModel(infer))

    async def stream(messages, info):
        if any(
            isinstance(part, ToolReturnPart)
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
        ):
            assert "video analysis" in repr(messages)
            yield "done"
        else:
            yield {
                0: DeltaToolCall(
                    name="read_media_url",
                    json_args=json.dumps({"url": URL, "instructions": "Read speech"}),
                    tool_call_id="url-call",
                )
            }

    agent = HarnessBuilder().build(
        AgentSpec(model_characteristics=HarnessModelCharacteristics(video_input={"max_video_bytes": 4})),
        output_type=str,
        model=FunctionModel(stream_function=stream),
    )
    result = await agent.run(
        "Read URL", bindings=RunBindings.embedded(capabilities=(InvocationPolicyCapability(evaluator=Allow()),))
    )
    assert result.output_or_raise() == "done"
    assert len(requests) == 1 and "Read speech" in repr(requests)
    records = [
        record
        for record in result.usage_records
        if isinstance(record, ModelUsageRecord) and record.source == "media.url_understanding"
    ]
    assert len(records) == 1
    assert records[0].tool_id == "media.read_video_url" and records[0].tool_call_id == "url-call"
    assert records[0].request_usage.input_tokens == 7
    assert records[0].request_usage.output_tokens == 3


async def test_explicit_provider_discovery_is_per_kind_and_overrides_environment(monkeypatch):
    from a13n_harness.toolsets._media import available_media_kinds

    monkeypatch.setenv("A13N_HARNESS_VIDEO_UNDERSTANDING_MODEL", "test:video")
    assert available_media_kinds(context(provider=Understanding(kinds=("image",)))) == ("image",)

    class LegacyProvider:
        async def understand(self, request, *, usage=None):
            return MediaUnderstandingResult(text="legacy file provider")

    assert available_media_kinds(context(provider=LegacyProvider())) == ()
    assert available_media_kinds(context(native="audio", provider=LegacyProvider())) == ("audio",)


async def test_configured_invalid_settings_fail_lazily_as_tool_error(monkeypatch):
    mock_download(monkeypatch, "video/mp4")
    monkeypatch.setenv("A13N_HARNESS_VIDEO_UNDERSTANDING_MODEL", "test:video")
    monkeypatch.setenv("A13N_HARNESS_VIDEO_UNDERSTANDING_MODEL_SETTINGS", "invalid-json")
    ctx = context()
    assert MediaUrlToolset().get_toolset(ctx) is not None
    result = await MediaUrlToolset().read_media_url(ctx, URL)
    assert result["error"]["code"] == "media_understanding_configuration_invalid"
