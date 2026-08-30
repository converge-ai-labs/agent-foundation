from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any, cast

import a13n_harness.models.inference as inference_module
import httpx2
import pytest
from a13n_harness import ModelHttpRetryConfig, RequestHeadersModel, create_model_http_client, infer_model
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.providers import Provider
from pydantic_ai.providers.google import GoogleProvider
from pydantic_ai.settings import ModelSettings

pytestmark = pytest.mark.anyio


def _text_model() -> FunctionModel:
    async def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del messages, info
        return ModelResponse(parts=[TextPart("done")])

    return FunctionModel(function=respond, model_name="test-model")


async def test_model_http_client_owns_only_transport_timeouts() -> None:
    client = create_model_http_client(timeout=321, connect=7)
    try:
        assert client.timeout.connect == 7
        assert client.timeout.read == 321
        assert client.timeout.write == 321
        assert client.timeout.pool == 321
    finally:
        await client.aclose()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("timeout", 0),
        ("timeout", -1),
        ("timeout", True),
        ("connect", 0),
        ("connect", -1),
        ("connect", False),
    ],
)
def test_model_http_client_rejects_invalid_timeouts(field: str, value: int) -> None:
    with pytest.raises(ValueError, match=f"{field} must be a positive integer"):
        if field == "timeout":
            create_model_http_client(timeout=value)
        else:
            create_model_http_client(connect=value)


async def test_model_http_client_retries_transient_status_and_transport_errors() -> None:
    requests: list[httpx2.Request] = []

    async def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if len(requests) == 1:
            raise httpx2.ConnectError("temporary connection failure", request=request)
        if len(requests) == 2:
            return httpx2.Response(503, headers={"Retry-After": "0"})
        return httpx2.Response(200, json={"ok": True})

    client = create_model_http_client(
        transport=httpx2.MockTransport(handle),
        retry=ModelHttpRetryConfig(
            attempts=3,
            backoff_multiplier=0,
            max_wait_seconds=0,
            retry_after_max_wait_seconds=0,
        ),
    )
    try:
        response = await client.get("https://example.test/model")
    finally:
        await client.aclose()

    assert response.status_code == 200
    assert len(requests) == 3


async def test_model_http_client_one_attempt_still_validates_retryable_status() -> None:
    attempts = 0

    async def handle(request: httpx2.Request) -> httpx2.Response:
        nonlocal attempts
        del request
        attempts += 1
        return httpx2.Response(503)

    client = create_model_http_client(
        transport=httpx2.MockTransport(handle),
        retry=ModelHttpRetryConfig(attempts=1),
    )
    try:
        with pytest.raises(httpx2.HTTPStatusError):
            await client.get("https://example.test/model")
    finally:
        await client.aclose()

    assert attempts == 1


async def test_model_http_client_can_disable_automatic_retries() -> None:
    attempts = 0

    async def handle(request: httpx2.Request) -> httpx2.Response:
        nonlocal attempts
        del request
        attempts += 1
        return httpx2.Response(503)

    client = create_model_http_client(
        transport=httpx2.MockTransport(handle),
        retry=None,
    )
    try:
        response = await client.get("https://example.test/model")
    finally:
        await client.aclose()

    assert response.status_code == 503
    assert attempts == 1


def test_model_http_retry_config_rejects_invalid_values() -> None:
    with pytest.raises(ValueError, match="attempts must be a positive integer"):
        ModelHttpRetryConfig(attempts=0)
    with pytest.raises(ValueError, match="max_wait_seconds must be a finite non-negative number"):
        ModelHttpRetryConfig(max_wait_seconds=-1)
    with pytest.raises(ValueError, match="status_codes must contain valid HTTP status integers"):
        ModelHttpRetryConfig(status_codes=frozenset({99}))


async def test_google_request_settings_override_transport_and_carry_headers() -> None:
    requests: list[httpx2.Request] = []

    async def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {"parts": [{"text": "done"}], "role": "model"},
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 1,
                    "candidatesTokenCount": 1,
                    "totalTokenCount": 2,
                },
                "modelVersion": "gemini-test",
                "responseId": "response-1",
            },
        )

    client = create_model_http_client(
        timeout=900,
        connect=5,
        transport=httpx2.MockTransport(handle),
    )
    try:
        provider = GoogleProvider(
            api_key="test-key",
            http_client=client,
            base_url="https://example.test",
        )
        model = GoogleModel("gemini-2.5-flash", provider=provider)

        response = await model.request(
            [ModelRequest(parts=[UserPromptPart(content="hello")])],
            ModelSettings(timeout=321, extra_headers={"x-session-id": "session-1"}),
            ModelRequestParameters(),
        )
    finally:
        await client.aclose()

    assert response.text == "done"
    assert len(requests) == 1
    assert requests[0].headers["x-session-id"] == "session-1"
    assert requests[0].extensions["timeout"] == {
        "connect": 321.0,
        "read": 321.0,
        "write": 321.0,
        "pool": 321.0,
    }


def test_infer_model_normalizes_legacy_provider_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[str] = []
    base = _text_model()

    def fake_infer(
        model: Model | str,
        provider_factory: Callable[[str], Provider[Any]],
    ) -> Model:
        del provider_factory
        assert isinstance(model, str)
        observed.append(model)
        return base

    monkeypatch.setattr(inference_module, "_pydantic_infer_model", fake_infer)

    assert infer_model("openai:gpt-5") is base
    assert infer_model("gemini:gemini-2.5-pro") is base
    assert infer_model("google-gla:gemini-2.5-pro") is base
    assert infer_model("google-vertex:gemini-2.5-pro") is base
    assert observed == [
        "openai-responses:gpt-5",
        "google-cloud:gemini-2.5-pro",
        "google-cloud:gemini-2.5-pro",
        "google-cloud:gemini-2.5-pro",
    ]


def test_infer_model_routes_gateway_provider_construction_to_the_caller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_models: list[str] = []
    observed_routes: list[tuple[str, str]] = []
    sentinel_provider = cast(Provider[Any], object())
    base = _text_model()

    def gateway_provider_factory(gateway_name: str, provider_name: str) -> Provider[Any]:
        observed_routes.append((gateway_name, provider_name))
        return sentinel_provider

    def fake_infer(
        model: Model | str,
        provider_factory: Callable[[str], Provider[Any]],
    ) -> Model:
        assert isinstance(model, str)
        observed_models.append(model)
        assert provider_factory("openai-responses") is sentinel_provider
        return base

    monkeypatch.setattr(inference_module, "_pydantic_infer_model", fake_infer)

    inferred = infer_model(
        "company@openai:gpt-5",
        gateway_provider_factory=gateway_provider_factory,
    )

    assert inferred is base
    assert observed_models == ["openai-responses:gpt-5"]
    assert observed_routes == [("company", "openai-responses")]


def test_infer_model_uses_pydantic_gateway_for_the_literal_gateway_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_models: list[str] = []
    observed_providers: list[str] = []
    sentinel_provider = cast(Provider[Any], object())
    base = _text_model()

    def fake_gateway_provider(provider_name: str) -> Provider[Any]:
        observed_providers.append(provider_name)
        return sentinel_provider

    def fake_infer(
        model: Model | str,
        provider_factory: Callable[[str], Provider[Any]],
    ) -> Model:
        assert isinstance(model, str)
        observed_models.append(model)
        assert provider_factory("openai-responses") is sentinel_provider
        return base

    monkeypatch.setattr(inference_module, "gateway_provider", fake_gateway_provider)
    monkeypatch.setattr(inference_module, "_pydantic_infer_model", fake_infer)

    def custom_gateway_provider_factory(gateway_name: str, provider_name: str) -> Provider[Any]:
        pytest.fail(f"literal gateway@ must not use custom factory: {gateway_name}, {provider_name}")

    assert (
        infer_model(
            "gateway@openai:gpt-5",
            gateway_provider_factory=custom_gateway_provider_factory,
        )
        is base
    )
    assert observed_models == ["openai-responses:gpt-5"]
    assert observed_providers == ["openai-responses"]


def test_infer_model_rejects_implicit_named_gateway_configuration() -> None:
    with pytest.raises(ValueError, match="gateway_provider_factory"):
        infer_model("company@openai:gpt-5")


@pytest.mark.parametrize(
    "model_name",
    ["@openai:gpt-5", "company@@openai:gpt-5", "company@openai", "openai:"],
)
def test_infer_model_rejects_invalid_routes(model_name: str) -> None:
    with pytest.raises(ValueError, match="format"):
        infer_model(model_name, gateway_provider_factory=lambda gateway, provider: cast(Provider[Any], object()))


def test_infer_model_applies_patches_in_order_and_headers_last() -> None:
    base = _text_model()
    observed: list[tuple[str, Model]] = []

    def first(model: Model) -> Model:
        observed.append(("first", model))
        return model

    def second(model: Model) -> Model:
        observed.append(("second", model))
        return model

    inferred = infer_model(
        base,
        patches=(first, second),
        common_headers={"X-Session-ID": "session-1"},
    )

    assert observed == [("first", base), ("second", base)]
    assert isinstance(inferred, RequestHeadersModel)
    assert inferred.wrapped is base
    assert inferred.model_name == base.model_name
    assert inferred.system == base.system
    assert inferred.profile is base.profile


def test_infer_model_requires_native_model_patch_results() -> None:
    def invalid_patch(model: Model) -> Model:
        del model
        return cast(Model, object())

    with pytest.raises(TypeError, match="patches"):
        infer_model(_text_model(), patches=(invalid_patch,))


async def test_request_headers_model_merges_headers_without_mutating_inputs() -> None:
    observed: list[ModelSettings | None] = []

    async def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del messages
        observed.append(info.model_settings)
        return ModelResponse(parts=[TextPart("done")])

    common_headers = {"X-Session-ID": "common", "X-Shared": "shared"}
    request_settings = ModelSettings(
        temperature=0.25,
        extra_headers={"x-session-id": "request", "X-Request": "request"},
    )
    model = RequestHeadersModel(
        FunctionModel(function=respond),
        common_headers=common_headers,
    )

    response = await model.request([], request_settings, ModelRequestParameters())

    assert response.text == "done"
    assert observed == [
        ModelSettings(
            temperature=0.25,
            extra_headers={
                "X-Shared": "shared",
                "x-session-id": "request",
                "X-Request": "request",
            },
        )
    ]
    assert request_settings["extra_headers"] == {
        "x-session-id": "request",
        "X-Request": "request",
    }
    assert common_headers == {"X-Session-ID": "common", "X-Shared": "shared"}


async def test_request_headers_model_applies_headers_to_streaming_requests() -> None:
    observed: list[ModelSettings | None] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages
        observed.append(info.model_settings)
        yield "streamed"

    model = RequestHeadersModel(
        FunctionModel(stream_function=stream),
        common_headers={"X-Thread-ID": "thread-1"},
    )

    async with model.request_stream([], None, ModelRequestParameters()) as response:
        _ = [event async for event in response]

    assert observed == [ModelSettings(extra_headers={"X-Thread-ID": "thread-1"})]
