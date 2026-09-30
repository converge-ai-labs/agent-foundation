"""Typed operation controls and their application; provider semantics stay separate.

The flat fields are shared by API and terminal selections for wire compatibility.
This is deliberately a fixed set of controls, not a provider/plugin registry.
"""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, JsonValue

from a13n_harness_ui.model_fast import FastControl, FastSelection, apply_fast, describe_fast
from a13n_harness_ui.model_reasoning_mode import (
    ReasoningMode,
    ReasoningModeControl,
    apply_reasoning_mode,
    describe_reasoning_mode,
)
from a13n_harness_ui.model_thinking import ThinkingControl, ThinkingSelection, apply_thinking, describe_thinking


class ModelControlSelection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    thinking: ThinkingSelection | None = None
    fast: FastSelection | None = None
    reasoning_mode: ReasoningMode | None = None

    def controls(self) -> ModelControlSelection:
        """Detach only the control fields from a richer operation request."""
        return ModelControlSelection(thinking=self.thinking, fast=self.fast, reasoning_mode=self.reasoning_mode)


def apply_model_controls(
    route: str, settings: Mapping[str, JsonValue], selection: ModelControlSelection
) -> dict[str, JsonValue]:
    effective = apply_thinking(route, settings, selection.thinking)
    effective = apply_fast(route, effective, selection.fast)
    return apply_reasoning_mode(route, effective, selection.reasoning_mode)


class ModelControls(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    thinking: ThinkingControl
    fast: FastControl
    reasoning_mode: ReasoningModeControl


def describe_model_controls(route: str, settings: Mapping[str, JsonValue]) -> ModelControls:
    return ModelControls(
        thinking=describe_thinking(route, settings),
        fast=describe_fast(route, settings),
        reasoning_mode=describe_reasoning_mode(route, settings),
    )
