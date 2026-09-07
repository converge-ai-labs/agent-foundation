"""Harness extension of the native Pydantic AI AgentSpec."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from enum import StrEnum
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from pydantic_ai.agent.spec import AgentSpec as PydanticAgentSpec
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.usage import UsageLimits

from a13n_harness.capability_types import first_party_declarative_capability_types


class ModelCapability(StrEnum):
    """Harness-owned capabilities of the active Agent model."""

    IMAGE_UNDERSTANDING = "image_understanding"
    VIDEO_UNDERSTANDING = "video_understanding"
    AUDIO_UNDERSTANDING = "audio_understanding"


class HarnessModelCharacteristics(BaseModel):
    """Resolved Harness characteristics of the active Agent model."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    capabilities: frozenset[ModelCapability] = Field(default_factory=frozenset)
    context_window: int | None = Field(default=None, gt=0)
    proactive_context_management_threshold: float | None = Field(default=0.65, ge=0.0, le=1.0)
    compact_threshold: float = Field(default=0.90, gt=0.0, le=1.0)

    @property
    def summary_reminder_tokens(self) -> int | None:
        """Return the absolute proactive summarize threshold when it is known."""
        if self.context_window is None or self.proactive_context_management_threshold is None:
            return None
        return int(self.context_window * self.proactive_context_management_threshold)


def _default_usage_limits() -> UsageLimits:
    return UsageLimits(request_limit=1000)


class AgentSpec(PydanticAgentSpec):
    """Native AgentSpec plus Harness-owned run and model characteristics."""

    model_config = ConfigDict(populate_by_name=True)

    system_prompt: str | list[str] | None = None
    toolset_instructions: bool = True
    usage_limits: UsageLimits = Field(default_factory=_default_usage_limits)
    model_characteristics: HarnessModelCharacteristics | None = None

    def with_updates(
        self,
        updates: Mapping[str, object] | None = None,
        /,
        **overrides: object,
    ) -> Self:
        """Return a fully validated deep copy with selected top-level fields replaced."""

        requested: dict[str, object] = {}
        if updates is not None:
            if not isinstance(updates, Mapping) or not all(isinstance(key, str) for key in updates):
                raise TypeError("updates must be a mapping with string keys")
            requested.update(updates)
        for key, value in overrides.items():
            if key in requested:
                raise ValueError(f"AgentSpec update field {key!r} was supplied more than once")
            requested[key] = value

        fields = type(self).model_fields
        aliases = {field.alias: name for name, field in fields.items() if field.alias is not None}
        replacements: dict[str, object] = {}
        sources: dict[str, str] = {}
        for key, value in requested.items():
            field_name = key if key in fields else aliases.get(key)
            if field_name is None:
                raise ValueError(f"AgentSpec has no updateable field {key!r}")
            previous = sources.get(field_name)
            if previous is not None:
                raise ValueError(f"AgentSpec field {field_name!r} was supplied through both {previous!r} and {key!r}")
            sources[field_name] = key
            replacements[field_name] = value

        values = {name: deepcopy(getattr(self, name)) for name in fields}
        values.update({name: deepcopy(value) for name, value in replacements.items()})
        return type(self).model_validate(values)

    @classmethod
    def model_json_schema_with_capabilities(
        cls,
        custom_capability_types: Sequence[type[AbstractCapability[Any]]] = (),
    ) -> dict[str, Any]:
        """Include Harness model characteristics in the native strict AgentSpec schema."""
        schema = super().model_json_schema_with_capabilities(
            (*first_party_declarative_capability_types(), *custom_capability_types)
        )
        definitions = schema.setdefault("$defs", {})
        model_characteristics_schema = HarnessModelCharacteristics.model_json_schema()
        definitions.update(model_characteristics_schema.pop("$defs", {}))
        definitions["HarnessModelCharacteristics"] = model_characteristics_schema
        schema["properties"]["system_prompt"] = {
            "anyOf": [
                {"type": "string"},
                {"items": {"type": "string"}, "type": "array"},
                {"type": "null"},
            ],
            "default": None,
        }
        schema["properties"]["toolset_instructions"] = {
            "type": "boolean",
            "default": True,
        }
        usage_limits_schema = TypeAdapter(UsageLimits).json_schema()
        usage_limits_schema["default"] = TypeAdapter(UsageLimits).dump_python(
            _default_usage_limits(),
            mode="json",
        )
        schema["properties"]["usage_limits"] = usage_limits_schema
        schema["properties"]["model_characteristics"] = {
            "anyOf": [
                {"$ref": "#/$defs/HarnessModelCharacteristics"},
                {"type": "null"},
            ],
            "default": None,
        }
        return schema


__all__ = ["AgentSpec", "HarnessModelCharacteristics", "ModelCapability"]
