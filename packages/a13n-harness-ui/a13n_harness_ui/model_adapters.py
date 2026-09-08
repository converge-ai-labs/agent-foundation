"""Harness UI-owned trusted Model adapter validation and provenance."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from a13n_harness_ui.errors import CompositionError
from a13n_harness_ui.model_presets import API_PROVIDER_BY_ROUTE, validate_base_url

_BUILTIN_ADAPTER_DISTRIBUTION = "a13n-harness-ui"
_PYDANTIC_AI_ADAPTER_KEY = "a13n.pydantic-ai"
_MODEL_ROUTE = re.compile(r"^[a-z0-9][a-z0-9._-]*:[^\s:@/][^\s:@]*$")
_SUPPORTED_PROVIDERS = frozenset(API_PROVIDER_BY_ROUTE) | frozenset(
    {
        "anthropic",
        "cohere",
        "gemini",
        "google-cloud",
        "google-gla",
        "google-vertex",
        "grok",
        "grok-build",
        "groq",
        "mistral",
        "openai",
        "openai-codex",
        "openai-responses",
    }
)


@dataclass(frozen=True, slots=True)
class ModelAdapterRegistration:
    """Installed provenance for one Harness UI-owned Model adapter."""

    adapter_key: str
    distribution_name: str
    distribution_version: str


class NormalizedModelConfiguration(BaseModel):
    """Pydantic AI recipe with unresolved Host authentication and opaque settings."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    route: str = Field(min_length=3, max_length=512)
    settings: dict[str, JsonValue] = Field(default_factory=dict)
    model_cfg: dict[str, JsonValue] = Field(default_factory=dict)


class _ModelConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    base_url: str | None = Field(default=None, max_length=2048)

    @field_validator("base_url")
    @classmethod
    def _base_url(cls, value: str | None) -> str | None:
        return validate_base_url(value) if value is not None else None


class PydanticAiModelAdapter:
    """Validate Host-owned Model wiring and pass native request settings through."""

    key = _PYDANTIC_AI_ADAPTER_KEY

    def validate(
        self,
        *,
        route: str,
        settings: Mapping[str, JsonValue],
        model_cfg: Mapping[str, JsonValue],
    ) -> NormalizedModelConfiguration:
        """Validate behavior without resolving credentials or constructing a native Model."""

        if not _MODEL_ROUTE.fullmatch(route) or route.partition(":")[0] not in _SUPPORTED_PROVIDERS:
            raise CompositionError(
                "The Model route is not supported by the Harness UI Pydantic AI adapter.",
                code="model_route_unsupported",
            )
        provider, _, model_id = route.partition(":")
        if provider == "openrouter" and ("/" not in model_id or not all(model_id.split("/", 1))):
            raise CompositionError(
                "OpenRouter model IDs must include the upstream provider, for example anthropic/claude-sonnet-4.6.",
                code="model_route_unsupported",
            )
        try:
            configuration = _ModelConfiguration.model_validate(dict(model_cfg), strict=True)
            if configuration.base_url is not None and provider not in {*API_PROVIDER_BY_ROUTE, "openai"}:
                raise ValueError("This route does not support an API-key base URL override")
        except ValueError as exc:
            raise CompositionError(
                "Model construction configuration is invalid or unsupported.",
                code="model_configuration_unsupported",
            ) from exc
        return NormalizedModelConfiguration(
            route=route,
            settings=dict(settings),
            model_cfg=configuration.model_dump(mode="json", exclude_none=True),
        )


def service_tier_setting(route: str) -> str:
    """Native tier key used by terminal display and explicit per-Run overrides."""
    provider = route.partition(":")[0]
    if provider == "anthropic":
        return "anthropic_service_tier"
    if provider in {"google", "google-cloud", "google-gla", "google-vertex", "gemini"}:
        return "google_cloud_service_tier"
    preset = API_PROVIDER_BY_ROUTE.get(provider)
    if provider in {"openai", "openai-chat", "openai-responses", "openai-codex"} or (
        preset is not None and preset.transport == "openai-client"
    ):
        return "openai_service_tier"
    return "service_tier"


def model_adapter_registration(adapter_key: str) -> ModelAdapterRegistration | None:
    """Return the exact built-in registration for one supported adapter key."""

    if adapter_key != _PYDANTIC_AI_ADAPTER_KEY:
        return None
    try:
        distribution_version = version(_BUILTIN_ADAPTER_DISTRIBUTION)
    except PackageNotFoundError:
        return None
    return ModelAdapterRegistration(
        adapter_key=adapter_key,
        distribution_name=_BUILTIN_ADAPTER_DISTRIBUTION,
        distribution_version=distribution_version,
    )


__all__ = [
    "ModelAdapterRegistration",
    "NormalizedModelConfiguration",
    "PydanticAiModelAdapter",
    "model_adapter_registration",
    "service_tier_setting",
]
