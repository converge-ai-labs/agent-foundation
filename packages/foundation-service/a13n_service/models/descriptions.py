"""Local parameter descriptions with optional adapter-owned catalog information."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from .domain import ModelDescription
from .settings import settings_schema

if TYPE_CHECKING:
    from .providers import ProviderRegistry


def default_description(model_api: str, upstream_model: str, display_name: str | None = None) -> ModelDescription:
    schema = settings_schema(model_api)
    return ModelDescription(
        upstream_model=upstream_model,
        display_name=display_name,
        suggested_model_api=model_api,
        settings_schema=schema,
        parameter_support={f"/{name}": "unknown" for name in schema["properties"]},
    )


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
    discovery = registry.integration(provider_type).model_discovery
    if discovery is None or metadata is None:
        return default_description(selected, upstream_model, display_name)
    return discovery.describe(selected, upstream_model, display_name, metadata)


def positive_token_limit(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None
