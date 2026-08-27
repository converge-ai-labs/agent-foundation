"""Harness extension of the native Pydantic AI AgentSpec."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai.agent.spec import AgentSpec as PydanticAgentSpec
from pydantic_ai.capabilities import AbstractCapability


class ModelConfiguration(BaseModel):
    """Resolved model characteristics that complement the provider ModelProfile."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    context_window: int | None = Field(default=None, gt=0)
    proactive_context_management_threshold: float | None = Field(default=0.65, ge=0.0, le=1.0)
    compact_threshold: float = Field(default=0.90, gt=0.0, le=1.0)

    @property
    def summary_reminder_tokens(self) -> int | None:
        """Return the absolute proactive summarize threshold when it is known."""
        if self.context_window is None or self.proactive_context_management_threshold is None:
            return None
        return int(self.context_window * self.proactive_context_management_threshold)

    @property
    def compaction_trigger_tokens(self) -> int | None:
        """Return the absolute compaction threshold when it is known."""
        if self.context_window is None:
            return None
        return max(1, int(self.context_window * self.compact_threshold))


class AgentSpec(PydanticAgentSpec):
    """Native AgentSpec plus resolved Harness model characteristics."""

    model_config = ConfigDict(populate_by_name=True)

    model_configuration: ModelConfiguration | None = Field(default=None, alias="model_config")

    @classmethod
    def model_json_schema_with_capabilities(
        cls,
        custom_capability_types: Sequence[type[AbstractCapability[Any]]] = (),
    ) -> dict[str, Any]:
        """Include Harness model configuration in the native strict AgentSpec schema."""
        schema = super().model_json_schema_with_capabilities(custom_capability_types)
        schema.setdefault("$defs", {})["ModelConfiguration"] = ModelConfiguration.model_json_schema()
        schema["properties"]["model_config"] = {
            "anyOf": [
                {"$ref": "#/$defs/ModelConfiguration"},
                {"type": "null"},
            ],
            "default": None,
        }
        return schema


__all__ = ["AgentSpec", "ModelConfiguration"]
