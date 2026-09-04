"""Trusted mappings from optional catalog data to editable Model suggestions."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from .domain import ModelLimits, ModelProfile
from .providers import ModelDescription, ProviderRegistry
from .service_common import ModelError
from .settings import JsonObject, outbound_parameter, settings_schema, validate_settings


def describe_model(
    registry: ProviderRegistry,
    provider_type: str,
    upstream_model: str,
    *,
    model_api: str | None = None,
    display_name: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> ModelDescription:
    selected = model_api or registry.definition(provider_type).default_model_api
    registry.validate_model_api(provider_type, selected)
    schema = settings_schema(selected)
    support: dict[str, Literal["supported", "unsupported", "unknown"]] = {
        f"/{name}": "unknown" for name in schema["properties"]
    }
    profile = ModelProfile()
    limits = ModelLimits()
    defaults: JsonObject = {}
    if provider_type == "openrouter" and metadata is not None:
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
            context_window_tokens=_positive(metadata.get("context_length")),
            max_output_tokens=_positive(top_provider.get("max_completion_tokens"))
            if isinstance(top_provider, Mapping)
            else None,
        )
        raw_defaults = metadata.get("default_parameters")
        if isinstance(raw_defaults, Mapping):
            for key in schema["properties"]:
                value = raw_defaults.get(outbound_parameter(key))
                if value is not None:
                    try:
                        validate_settings(selected, {key: value})
                    except ModelError:
                        continue
                    defaults[key] = value
    if provider_type in {"google_gemini", "google_vertex"} and metadata is not None:
        limits = ModelLimits(
            context_window_tokens=_positive(metadata.get("inputTokenLimit")),
            max_output_tokens=_positive(metadata.get("outputTokenLimit")),
        )
    return ModelDescription(
        upstream_model=upstream_model,
        display_name=display_name,
        suggested_model_api=selected,
        suggested_settings=validate_settings(selected, defaults),
        suggested_profile=profile,
        suggested_limits=limits,
        settings_schema=schema,
        parameter_support=support,
    )


def _positive(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None
