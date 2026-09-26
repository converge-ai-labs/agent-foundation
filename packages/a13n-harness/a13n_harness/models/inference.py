"""Developer-facing native Model inference and request patch composition."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from copy import deepcopy
from typing import Any

from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import Model, ModelRequestParameters, StreamedResponse
from pydantic_ai.models import infer_model as _pydantic_infer_model
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.providers import Provider, infer_provider
from pydantic_ai.providers.gateway import gateway_provider
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tools import RunContext

type ModelProviderFactory = Callable[[str], Provider[Any]]
type GatewayModelProviderFactory = Callable[[str, str], Provider[Any]]
type ModelPatch = Callable[[Model], Model]

ROUTE_ALIASES: dict[str, str] = {
    "gemini": "google-cloud",
    "google-gla": "google-cloud",
    "google-vertex": "google-cloud",
    "openai": "openai-responses",
}


def _normalize_model_string(model: str) -> str:
    if "@" in model:
        gateway, separator, route = model.partition("@")
        if not gateway or "@" in route:
            raise ValueError("Gateway model strings must use format gateway@provider:model")
    else:
        gateway, separator, route = "", "", model

    provider_name, provider_separator, model_name = route.partition(":")
    if provider_separator:
        if not provider_name or not model_name:
            raise ValueError("Model strings with a provider must use format provider:model")
        provider_name = ROUTE_ALIASES.get(provider_name, provider_name)
        route = f"{provider_name}:{model_name}"

    return f"{gateway}@{route}" if separator else route


def _validated_headers(headers: Mapping[str, str]) -> dict[str, str]:
    copied: dict[str, str] = {}
    for name, value in headers.items():
        if not isinstance(name, str) or not name:
            raise ValueError("Model request header names must be non-empty strings")
        if not isinstance(value, str):
            raise TypeError("Model request header values must be strings")
        copied[name] = value
    return copied


def _merge_headers(
    defaults: Mapping[str, str],
    overrides: Mapping[str, str] | None,
) -> dict[str, str]:
    merged: dict[str, str] = {}
    names_by_lowercase: dict[str, str] = {}
    for headers in (defaults, overrides):
        if headers is None:
            continue
        for name, value in headers.items():
            lowercase_name = name.lower()
            previous_name = names_by_lowercase.get(lowercase_name)
            if previous_name is not None and previous_name != name:
                del merged[previous_name]
            merged[name] = value
            names_by_lowercase[lowercase_name] = name
    return merged


class RequestHeadersModel(WrapperModel):
    """Apply common request headers while preserving native per-request precedence."""

    def __init__(self, wrapped: Model, *, common_headers: Mapping[str, str]) -> None:
        super().__init__(wrapped)
        self._common_headers = _validated_headers(common_headers)

    @property
    def common_headers(self) -> Mapping[str, str]:
        """Return a detached snapshot of the configured common headers."""

        return self._common_headers.copy()

    def __copy__(self) -> RequestHeadersModel:
        return RequestHeadersModel(self.wrapped, common_headers=self._common_headers)

    def __deepcopy__(self, memo: dict[int, Any]) -> RequestHeadersModel:
        return RequestHeadersModel(deepcopy(self.wrapped, memo), common_headers=self._common_headers)

    def _settings_with_headers(self, model_settings: ModelSettings | None) -> ModelSettings:
        settings = ModelSettings(**model_settings) if model_settings is not None else ModelSettings()
        settings["extra_headers"] = _merge_headers(
            self._common_headers,
            settings.get("extra_headers"),
        )
        return settings

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        return await self.wrapped.request(
            messages,
            self._settings_with_headers(model_settings),
            model_request_parameters,
        )

    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: RunContext[Any] | None = None,
    ) -> AsyncGenerator[StreamedResponse]:
        async with self.wrapped.request_stream(
            messages,
            self._settings_with_headers(model_settings),
            model_request_parameters,
            run_context,
        ) as response:
            yield response


def infer_model(
    model: Model | str,
    *,
    provider_factory: ModelProviderFactory = infer_provider,
    gateway_provider_factory: GatewayModelProviderFactory | None = None,
    common_headers: Mapping[str, str] | None = None,
    patches: Sequence[ModelPatch] = (),
) -> Model:
    """Build a native Model with optional gateway routing, patches, and headers.

    Provider and gateway factories own provider clients and their resource
    lifecycle. Callers may bypass this helper and pass any native Model directly
    to ``HarnessBuilder.build(model=...)``.
    """

    if isinstance(model, Model):
        inferred = model
    elif isinstance(model, str):
        normalized = _normalize_model_string(model)
        gateway_name, separator, routed_model = normalized.partition("@")
        selected_provider_factory: ModelProviderFactory = provider_factory
        if separator:
            provider_name, provider_separator, _ = routed_model.partition(":")
            if not provider_separator or not provider_name:
                raise ValueError("Gateway model strings must use format gateway@provider:model")
            if gateway_provider_factory is None and gateway_name != "gateway":
                raise ValueError(
                    "Named gateway model strings require gateway_provider_factory; "
                    "use gateway@provider:model for the Pydantic AI Gateway"
                )

            def routed_provider_factory(requested_provider_name: str, /) -> Provider[Any]:
                if gateway_name == "gateway":
                    return gateway_provider(requested_provider_name)
                assert gateway_provider_factory is not None
                return gateway_provider_factory(gateway_name, requested_provider_name)

            selected_provider_factory = routed_provider_factory
            normalized = routed_model
        inferred = _pydantic_infer_model(normalized, selected_provider_factory)
    else:
        raise TypeError("model must be a native Pydantic AI Model or string")

    for patch in patches:
        inferred = patch(inferred)
        if not isinstance(inferred, Model):
            raise TypeError("Model patches must return a native Pydantic AI Model")

    if common_headers:
        inferred = RequestHeadersModel(inferred, common_headers=common_headers)
    return inferred


__all__ = [
    "GatewayModelProviderFactory",
    "ModelPatch",
    "ModelProviderFactory",
    "RequestHeadersModel",
    "infer_model",
]
