"""OpenRouter Provider adapter."""

from collections.abc import Mapping
from typing import Any

import httpx2
from pydantic_ai.providers.openrouter import OpenRouterProvider

from ..candidates import positive_token_limit
from ..domain import ModelCandidate, ModelLimits, ModelProfile
from ..service_common import ModelError
from ..settings import JsonObject, settings_schema, validate_settings
from .base import (
    ModelListSchema,
    ProviderIntegration,
    bearer_models_request,
)
from .openai_provider import ClientEndpointProvider, OpenAIModelDiscovery, build
from .types import ProviderConfiguration, RuntimeProvider


class _OpenRouterProvider(ClientEndpointProvider, OpenRouterProvider):
    pass


def _build_provider(
    provider: RuntimeProvider,
    http_client: httpx2.AsyncClient,
    model_api: str,
) -> OpenRouterProvider:
    return build(provider, http_client, model_api, _OpenRouterProvider)


class OpenRouterDiscovery(OpenAIModelDiscovery):
    def candidate(
        self, model_api: str, upstream_model: str, display_name: str | None, metadata: Mapping[str, Any]
    ) -> ModelCandidate:
        result = super().candidate(model_api, upstream_model, display_name, metadata)
        schema = settings_schema(model_api)
        support = dict(result.parameter_support)
        profile_values: dict[str, object] = {}
        defaults: JsonObject = {}
        parameters = metadata.get("supported_parameters")
        if isinstance(parameters, list):
            for name in schema["properties"]:
                if name.startswith("openrouter_") and name != "openrouter_reasoning":
                    support[f"/{name}"] = "supported"
                elif name not in {"extra_headers", "extra_body", "timeout", "thinking", "service_tier"}:
                    support[f"/{name}"] = "supported" if _outbound_parameter(name) in parameters else "unsupported"
            profile_values.update(
                supports_tools="tools" in parameters,
                supports_json_schema_output="structured_outputs" in parameters,
                supports_json_object_output="response_format" in parameters,
                supports_thinking="reasoning" in parameters,
            )
        architecture = metadata.get("architecture")
        modalities = architecture.get("input_modalities") if isinstance(architecture, Mapping) else None
        if isinstance(modalities, list):
            profile_values["input_modalities"] = tuple(
                value for value in modalities if value in ("text", "image", "audio", "video")
            )
        profile = ModelProfile.model_validate(profile_values)
        top_provider = metadata.get("top_provider")
        limits = ModelLimits(
            context_window_tokens=positive_token_limit(metadata.get("context_length")),
            max_output_tokens=positive_token_limit(top_provider.get("max_completion_tokens"))
            if isinstance(top_provider, Mapping)
            else None,
        )
        raw_defaults = metadata.get("default_parameters")
        if isinstance(raw_defaults, Mapping):
            for key in schema["properties"]:
                value = raw_defaults.get(_outbound_parameter(key))
                if value is not None:
                    try:
                        validate_settings(model_api, {key: value})
                    except ModelError:
                        continue
                    defaults[key] = value
        return result.model_copy(
            update={
                "profile": profile,
                "limits": limits,
                "suggested_settings": validate_settings(model_api, defaults),
                "parameter_support": support,
            }
        )


INTEGRATION = ProviderIntegration(
    type="openrouter",
    display_name="OpenRouter",
    configuration_model=ProviderConfiguration,
    supported_model_apis=("openrouter.chat_completions",),
    build_provider=_build_provider,
    endpoint="https://openrouter.ai/api/v1",
    model_discovery=OpenRouterDiscovery(bearer_models_request, ModelListSchema("data", "id", ("name",))),
    model_profile=OpenRouterProvider.model_profile,
)


def _outbound_parameter(name: str) -> str:
    """Map native names only for advisory OpenRouter catalog information."""
    if name == "stop_sequences":
        return "stop"
    return name.removeprefix("openrouter_")
