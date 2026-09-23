"""Responses reasoning mode, independent of thinking effort and summaries."""

from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, JsonValue
from pydantic_ai.profiles.openai import openai_model_profile

from a13n_harness_ui.errors import CompositionError

type ReasoningMode = Literal["standard", "pro"]
type ReasoningModeState = Literal["standard", "pro", "default", "custom"]


class ReasoningModeControl(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    supported: bool
    state: ReasoningModeState
    reason: str | None = None


def _conflict(settings: Mapping[str, JsonValue]) -> bool:
    body = settings.get("extra_body")
    # The native SDK replaces the whole reasoning object, including when the
    # custom body contains only a sibling such as summary.
    return isinstance(body, dict) and "reasoning" in body


def _supported(route: str) -> bool:
    provider, _, name = route.partition(":")
    return provider in {"openai", "openai-responses", "openai-codex"} and bool(
        openai_model_profile(name).get("openai_responses_supports_reasoning_mode")
    )


def reasoning_mode_state(route: str, settings: Mapping[str, JsonValue]) -> ReasoningModeState:
    """Describe authored/captured intent, not provider entitlement or observed execution."""
    if _conflict(settings):
        return "custom"
    value = settings.get("openai_reasoning_mode")
    if value is None:
        return "default"
    if value in ("standard", "pro"):
        # Settings on other transports do not imply a Responses request mode.
        if _supported(route):
            return "pro" if value == "pro" else "standard"
    return "custom"


def describe_reasoning_mode(route: str, settings: Mapping[str, JsonValue]) -> ReasoningModeControl:
    reason = None
    if not _supported(route):
        reason = "Reasoning mode is not available for this model connection."
    elif _conflict(settings):
        reason = "Reasoning mode is controlled by extra_body. Edit the Model configuration first."
    return ReasoningModeControl(supported=reason is None, state=reasoning_mode_state(route, settings), reason=reason)


def apply_reasoning_mode(
    route: str, settings: Mapping[str, JsonValue], selected: ReasoningMode | None
) -> dict[str, JsonValue]:
    effective = dict(settings)
    if selected is None:
        return effective
    control = describe_reasoning_mode(route, settings)
    if not control.supported:
        raise CompositionError(
            control.reason or "Reasoning mode is unavailable.", code="model_reasoning_mode_unavailable"
        )
    effective["openai_reasoning_mode"] = selected
    return effective
