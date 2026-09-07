"""Harness UI-owned trusted Model adapter validation and provenance."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from a13n_harness_ui.errors import CompositionError

_BUILTIN_ADAPTER_DISTRIBUTION = "a13n-harness-ui"
_PYDANTIC_AI_ADAPTER_KEY = "a13n.pydantic-ai"
_MODEL_ROUTE = re.compile(r"^[a-z0-9][a-z0-9._-]*:[^\s:@/][^\s:@]*$")
_SUPPORTED_PROVIDERS = frozenset(
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
    """Credential-free Pydantic AI construction recipe accepted by Harness UI."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    route: str = Field(min_length=3, max_length=512)
    settings: dict[str, JsonValue] = Field(default_factory=dict)
    model_cfg: dict[str, JsonValue] = Field(default_factory=dict)


class _SupportedModelSettings(BaseModel):
    """The intentionally small Pydantic AI settings surface owned by Harness UI."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    max_tokens: int | None = Field(default=None, ge=1)
    temperature: float | None = None
    top_p: float | None = Field(default=None, ge=0, le=1)
    top_k: int | None = Field(default=None, ge=1)
    timeout: float | None = Field(default=None, gt=0)
    parallel_tool_calls: bool | None = None
    tool_choice: Literal["none", "required", "auto"] | None = None
    seed: int | None = None
    presence_penalty: float | None = None
    frequency_penalty: float | None = None
    logit_bias: dict[str, int] | None = None
    stop_sequences: tuple[str, ...] | None = None
    thinking: bool | Literal["minimal", "low", "medium", "high", "xhigh"] | None = None
    openai_reasoning_summary: Literal["auto", "concise", "detailed"] | None = None
    openai_store: bool | None = None
    service_tier: Literal["auto", "default", "flex", "priority"] | None = None

    @field_validator("stop_sequences", mode="before")
    @classmethod
    def _serialized_stop_sequences(cls, value: object) -> object:
        if isinstance(value, list):
            return tuple(value)
        return value

    @field_validator("temperature", "timeout", "presence_penalty", "frequency_penalty")
    @classmethod
    def _finite_numbers(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("Model settings must contain only finite numbers")
        return value

    @field_validator("logit_bias")
    @classmethod
    def _bounded_logit_bias(cls, value: dict[str, int] | None) -> dict[str, int] | None:
        if value is not None and (len(value) > 256 or any(len(key) > 256 for key in value)):
            raise ValueError("logit_bias must be bounded")
        return value

    @field_validator("stop_sequences")
    @classmethod
    def _bounded_stop_sequences(cls, value: tuple[str, ...] | None) -> tuple[str, ...] | None:
        if value is not None and (
            len(value) > 32 or len(value) != len(set(value)) or any(not item or len(item) > 4096 for item in value)
        ):
            raise ValueError("stop_sequences must be bounded, non-empty, and unique")
        return value

    @model_validator(mode="after")
    def _bounded_penalties(self) -> Self:
        for value in (self.presence_penalty, self.frequency_penalty):
            if value is not None and not -2 <= value <= 2:
                raise ValueError("Model penalties must be between -2 and 2")
        return self


class PydanticAiModelAdapter:
    """Strict validation boundary for the supported Pydantic AI recipe subset."""

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
        if model_cfg:
            raise CompositionError(
                "Model construction configuration is not supported for this adapter release.",
                code="model_configuration_unsupported",
            )
        try:
            validated = _SupportedModelSettings.model_validate(dict(settings), strict=True)
        except ValueError as exc:
            raise CompositionError(
                "Model settings are invalid or unsupported.",
                code="model_settings_invalid",
            ) from exc
        normalized = validated.model_dump(mode="json", exclude_none=True)
        return NormalizedModelConfiguration(
            route=route,
            settings=normalized,
            model_cfg={},
        )


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
]
