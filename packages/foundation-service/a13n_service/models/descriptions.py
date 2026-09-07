"""Local parameter descriptions with optional adapter-owned catalog information."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from .domain import ModelCandidate, ModelDescription
from .settings import settings_schema

if TYPE_CHECKING:
    from .providers import ProviderRegistry


def default_candidate(model_api: str, upstream_model: str, display_name: str | None = None) -> ModelCandidate:
    return ModelCandidate(
        upstream_model=upstream_model,
        display_name=display_name,
        suggested_model_api=model_api,
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
    candidate = describe_candidate(
        registry, provider_type, upstream_model, model_api=model_api, display_name=display_name, metadata=metadata
    )
    return ModelDescription(**candidate.model_dump(), settings_schema=settings_schema(candidate.suggested_model_api))


def describe_candidate(
    registry: ProviderRegistry,
    provider_type: str,
    upstream_model: str,
    *,
    model_api: str | None = None,
    display_name: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> ModelCandidate:
    integration = registry.integration(provider_type)
    selected = model_api or integration.supported_model_apis[0]
    registry.validate_model_api(provider_type, selected)
    discovery = integration.model_discovery
    if discovery is None or metadata is None:
        return default_candidate(selected, upstream_model, display_name)
    return discovery.describe(selected, upstream_model, display_name, metadata)


def positive_token_limit(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None
