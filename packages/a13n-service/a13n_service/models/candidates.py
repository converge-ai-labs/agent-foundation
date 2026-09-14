"""Catalog-to-candidate projection for optional Model authoring suggestions."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from pydantic_ai.exceptions import UserError

from .domain import ModelCandidate, ModelProfile

if TYPE_CHECKING:
    from .providers import ProviderRegistry


def default_candidate(model_api: str, upstream_model: str, display_name: str | None = None) -> ModelCandidate:
    return ModelCandidate(
        upstream_model=upstream_model,
        display_name=display_name,
        suggested_model_api=model_api,
    )


def candidate_from_catalog(
    registry: ProviderRegistry,
    provider_type: str,
    upstream_model: str,
    *,
    display_name: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> ModelCandidate:
    integration = registry.integration(provider_type)
    selected = integration.supported_model_apis[0]
    discovery = integration.model_discovery
    candidate = (
        default_candidate(selected, upstream_model, display_name)
        if discovery is None or metadata is None
        else discovery.candidate(selected, upstream_model, display_name, metadata)
    )
    try:
        native = integration.model_profile(upstream_model) if integration.model_profile is not None else None
    except UserError:
        native = None
    if native is None:
        return candidate
    projected = ModelProfile.model_validate(
        {name: native[name] for name in ModelProfile.model_fields if name in native}
    )
    profile = ModelProfile.model_validate(
        {
            **projected.model_dump(exclude_unset=True),
            **candidate.profile.model_dump(exclude_unset=True),
        }
    )
    return candidate.model_copy(update={"profile": profile})


def positive_token_limit(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None
