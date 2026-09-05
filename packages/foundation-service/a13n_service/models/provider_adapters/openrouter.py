"""OpenRouter Provider adapter."""

from collections.abc import Mapping
from typing import Any

import httpx2
from pydantic_ai.providers.openrouter import OpenRouterProvider

from ..descriptions import positive_token_limit
from ..domain import ModelDescription, ModelLimits, ModelProfile
from ..service_common import ModelError
from ..settings import JsonObject, outbound_parameter, settings_schema, validate_settings
from .base import (
    ModelListSchema,
    ProviderIntegration,
    bearer_models_request,
    require_credential,
)
from .openai_provider import OpenAIModelDiscovery
from .types import EmptyProviderConfiguration, RuntimeProvider


def _build_provider(
    provider: RuntimeProvider,
    http_client: httpx2.AsyncClient,
    _pydantic_provider_name: str,
) -> OpenRouterProvider:
    return OpenRouterProvider(api_key=require_credential(provider), http_client=http_client)


class OpenRouterDiscovery(OpenAIModelDiscovery):
    def describe(
        self, model_api: str, upstream_model: str, display_name: str | None, metadata: Mapping[str, Any]
    ) -> ModelDescription:
        result = super().describe(model_api, upstream_model, display_name, metadata)
        schema = settings_schema(model_api)
        support = dict(result.parameter_support)
        profile = ModelProfile()
        defaults: JsonObject = {}
        parameters = metadata.get("supported_parameters")
        if isinstance(parameters, list):
            for name in schema["properties"]:
                if name.startswith("openrouter_") and name != "openrouter_reasoning":
                    support[f"/{name}"] = "supported"
                elif name not in {"extra_headers", "extra_body", "timeout", "thinking", "service_tier"}:
                    support[f"/{name}"] = "supported" if outbound_parameter(name) in parameters else "unsupported"
            architecture = metadata.get("architecture")
            modalities = architecture.get("input_modalities") if isinstance(architecture, Mapping) else None
            profile = ModelProfile(
                input_modalities=tuple(v for v in modalities if v in ("text", "image", "audio", "video"))
                if isinstance(modalities, list)
                else None,
                supports_tools="tools" in parameters,
                supports_json_schema_output="structured_outputs" in parameters,
                supports_json_object_output="response_format" in parameters,
                supports_thinking="reasoning" in parameters,
            )
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
                value = raw_defaults.get(outbound_parameter(key))
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
    configuration_model=EmptyProviderConfiguration,
    supported_model_apis=("openrouter.chat_completions",),
    build_provider=_build_provider,
    endpoint="https://openrouter.ai/api/v1",
    model_discovery=OpenRouterDiscovery(bearer_models_request, ModelListSchema("data", "id", ("name",))),
)
