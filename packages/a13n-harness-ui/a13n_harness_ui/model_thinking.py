"""Model-aware thinking controls shared by capture, API and terminal clients.

Settings authored in Model resources remain opaque. Only explicit interactive
choices are validated here; neither discovery nor display constructs a Model,
resolves credentials, or contacts a provider.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, JsonValue
from pydantic_ai.profiles.anthropic import ANTHROPIC_THINKING_BUDGET_MAP, anthropic_model_profile
from pydantic_ai.profiles.google import GOOGLE_THINKING_LEVEL_SCALE, google_model_profile
from pydantic_ai.profiles.openai import openai_model_profile

from a13n_harness_ui.errors import CompositionError

type ThinkingSelection = bool | Literal["minimal", "low", "medium", "high", "xhigh", "max"]


class ThinkingOption(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    value: ThinkingSelection | None
    label: str
    description: str
    disabled_reason: str | None = None

    @property
    def command(self) -> str:
        if self.value is None:
            return "default"
        if isinstance(self.value, bool):
            return "on" if self.value else "off"
        return self.value


class ThinkingControl(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    status: Literal["supported", "unsupported", "unknown"]
    default_summary: str
    options: tuple[ThinkingOption, ...]
    reason: str | None = None


@dataclass(frozen=True)
class _Choice:
    value: ThinkingSelection
    description: str
    patch: dict[str, JsonValue]
    budget: int | None = None


# Only control fields are replaced. Other native settings (summaries, display,
# cache options, etc.) are retained, including siblings in nested objects.
_CONTROL_KEYS = {
    "anthropic_thinking": {"type", "budget_tokens"},
    "google_thinking_config": {"thinking_level", "thinking_budget"},
    "openrouter_reasoning": {"effort", "enabled", "max_tokens"},
}
_OPENAI_ROUTES = {"openai", "openai-chat", "openai-responses", "openai-codex"}
_GOOGLE_ROUTES = {"google", "google-gla", "google-cloud", "google-vertex", "gemini"}


def _label(value: ThinkingSelection) -> str:
    return ("On" if value else "Off") if isinstance(value, bool) else value.capitalize()


def _choices(route: str) -> tuple[_Choice, ...]:
    provider, _, name = route.partition(":")
    if provider in _OPENAI_ROUTES:
        # GPT-6.1 Sol is not recognized by the bundled SDK profile yet.
        # Its documented efforts exclude none/minimal and include max.
        if name == "gpt-6.1-sol":
            return tuple(
                _Choice(effort, "Reasoning effort", {"openai_reasoning_effort": effort})
                for effort in ("low", "medium", "high", "xhigh", "max")
            )
        profile = openai_model_profile(name)
        if not profile.get("supports_thinking"):
            return ()
        # Profiles expose on/off but not a complete effort set. These reviewed
        # families fill that gap; unknown families do not inherit a guessed menu.
        levels: tuple[ThinkingSelection, ...]
        if "-chat" in name:
            levels = ("medium",)
        elif name.startswith(("gpt-5.2", "gpt-5.3", "gpt-5.4", "gpt-5.5", "gpt-5.6", "gpt-6-astra", "gpt-6-sol")):
            levels = ("medium", "high", "xhigh") if "-pro" in name else ("low", "medium", "high", "xhigh")
        elif name.startswith("gpt-5-pro"):
            levels = ("high",)
        elif name.startswith("gpt-5.1"):
            levels = ("low", "medium", "high")
        elif name == "gpt-5" or name.startswith(("gpt-5-mini", "gpt-5-nano", "gpt-5-2025")):
            levels = ("minimal", "low", "medium", "high")
        elif name.startswith(("o1", "o3", "o4")):
            levels = ("low", "medium", "high")
        else:
            return ()
        if profile.get("openai_supports_reasoning_effort_none"):
            levels = (False, *levels)
        return tuple(
            _Choice(v, "Reasoning effort", {"openai_reasoning_effort": "none" if v is False else v}) for v in levels
        )
    if provider == "anthropic":
        # Upstream's Anthropic profile is permissive for unknown IDs. Do not
        # present its fallback as verified support for an arbitrary custom model.
        if not name.startswith(
            (
                "claude-3-7-sonnet",
                "claude-sonnet-4",
                "claude-opus-4",
                "claude-haiku-4-5",
                "claude-sonnet-5",
                "claude-opus-5",
                "claude-fable-5",
                "claude-mythos-5",
            )
        ):
            return ()
        profile = anthropic_model_profile(name) or {}
        off = _Choice(False, "Disable thinking", {"anthropic_thinking": {"type": "disabled"}})
        if profile.get("anthropic_supports_adaptive_thinking"):
            efforts: tuple[ThinkingSelection, ...] = ("low", "medium", "high")
            if profile.get("anthropic_supports_xhigh_effort"):
                efforts = (*efforts, "xhigh")
            if name.startswith("claude-opus"):
                efforts = (*efforts, "max")
            return (
                off,
                *(
                    _Choice(
                        v,
                        "Adaptive thinking effort",
                        {"anthropic_thinking": {"type": "adaptive"}, "anthropic_effort": v},
                    )
                    for v in efforts
                ),
            )
        return (
            off,
            *(
                _Choice(
                    v,
                    f"Thinking budget: {budget:,} tokens",
                    {"anthropic_thinking": {"type": "enabled", "budget_tokens": budget}},
                    budget,
                )
                for v in ("low", "medium", "high")
                if isinstance(budget := ANTHROPIC_THINKING_BUDGET_MAP[v], int)
            ),
        )
    if provider in _GOOGLE_ROUTES:
        profile = google_model_profile(name) or {}
        if not profile.get("supports_thinking"):
            return ()
        if profile.get("google_supports_thinking_level"):
            levels = profile.get("google_thinking_levels", frozenset(GOOGLE_THINKING_LEVEL_SCALE))
            # Gemini level MINIMAL is not an off switch.
            return tuple(
                _Choice(
                    cast(ThinkingSelection, v),
                    "Thinking level" + ("; not disabled" if v == "minimal" else ""),
                    {"google_thinking_config": {"thinking_level": level}},
                )
                for level in GOOGLE_THINKING_LEVEL_SCALE
                if level in levels
                for v in (level.lower(),)
            )
        if not name.startswith("gemini-2.5"):
            return ()
        # Explicit UI budget presets, not claims of native low/medium/high.
        budgets: tuple[tuple[ThinkingSelection, int], ...] = (("low", 2048), ("medium", 8192), ("high", 16384))
        values = tuple(
            _Choice(
                v,
                f"Thinking budget: {budget:,} tokens",
                {"google_thinking_config": {"thinking_budget": budget}},
                budget,
            )
            for v, budget in budgets
        )
        return (
            values
            if profile.get("thinking_always_enabled")
            else (_Choice(False, "Disable thinking", {"google_thinking_config": {"thinking_budget": 0}}), *values)
        )
    return ()


def _conflict(settings: Mapping[str, JsonValue]) -> str | None:
    body = settings.get("extra_body")
    if (
        isinstance(body, dict)
        and {"thinking", "reasoning", "reasoning_effort", "output_config", "generation_config", "generationConfig"}
        & body.keys()
    ):
        return "Thinking is controlled by extra_body. Edit the Model configuration before using an override."
    return None


def _disabled(route: str, settings: Mapping[str, JsonValue], choice: _Choice) -> str | None:
    if conflict := _conflict(settings):
        return conflict
    if choice.budget is not None and route.startswith("anthropic:"):
        limit = settings.get("max_tokens", 4096)
        if not isinstance(limit, int) or isinstance(limit, bool) or choice.budget >= limit:
            return "Thinking budget must be below max_tokens; adjust the Model output limit first."
    return None


def describe_thinking(route: str, settings: Mapping[str, JsonValue]) -> ThinkingControl:
    """Publish choices and configured defaults without exposing raw settings."""
    choices = _choices(route)
    summary = summarize_thinking(route, settings)
    reason = _conflict(settings)
    status: Literal["supported", "unsupported", "unknown"] = "supported" if choices else "unknown"
    if not choices:
        provider, _, name = route.partition(":")
        if (provider in _OPENAI_ROUTES and name.startswith(("gpt-4", "gpt-3.5"))) or (
            provider in _GOOGLE_ROUTES and name.startswith(("gemini-1", "gemini-2.0"))
        ):
            status = "unsupported"
        reason = (
            "Thinking controls are not supported for this model."
            if status == "unsupported"
            else "Thinking controls are not known for this model/adapter. Model settings remain unchanged."
        )
    return ThinkingControl(
        status=status,
        default_summary=summary,
        reason=reason,
        options=(
            ThinkingOption(value=None, label="Model default", description=summary),
            *(
                ThinkingOption(
                    value=c.value,
                    label=_label(c.value),
                    description=c.description,
                    disabled_reason=_disabled(route, settings, c),
                )
                for c in choices
            ),
        ),
    )


def apply_thinking(
    route: str, settings: Mapping[str, JsonValue], selection: ThinkingSelection | None
) -> dict[str, JsonValue]:
    """Apply a validated selection to a detached copy; None inherits verbatim."""
    result = dict(settings)
    if selection is None:
        return result
    choice = next((c for c in _choices(route) if c.value == selection), None)
    if choice is None:
        raise CompositionError(
            "The selected thinking option is not supported by this Model.", code="thinking_selection_invalid"
        )
    if reason := _disabled(route, settings, choice):
        raise CompositionError(reason, code="thinking_selection_invalid")
    result.pop("thinking", None)
    if route.startswith("anthropic:"):
        result.pop("anthropic_effort", None)
    # Retain a supported unified intent for existing consumers and resume. Native
    # fields below are authoritative; 'max' has no unified SDK representation.
    if selection != "max":
        result["thinking"] = selection
    for key, value in choice.patch.items():
        if key in _CONTROL_KEYS and isinstance(value, dict):
            old = settings.get(key)
            siblings = {k: v for k, v in old.items() if k not in _CONTROL_KEYS[key]} if isinstance(old, dict) else {}
            # Disabled Anthropic thinking has no display/budget fields.
            result[key] = value if value.get("type") == "disabled" else {**siblings, **value}
        else:
            result[key] = value
    return result


def summarize_thinking(route: str, settings: Mapping[str, JsonValue]) -> str:
    """Describe requested configuration, never measured provider behavior."""
    if _conflict(settings):
        return "Custom (extra_body)"
    provider = route.partition(":")[0]
    if provider in _OPENAI_ROUTES and (effort := settings.get("openai_reasoning_effort")) is not None:
        return "Off" if effort == "none" else _safe_effort(effort)
    if provider == "anthropic":
        native = settings.get("anthropic_thinking")
        effort = settings.get("anthropic_effort")
        if isinstance(native, dict):
            if native.get("type") == "disabled":
                return "Off"
            if native.get("type") == "enabled":
                return _budget_summary(native.get("budget_tokens"))
            if native.get("type") == "adaptive":
                return f"Adaptive · {_safe_effort(effort or settings.get('thinking'))}"
            return "Custom"
        if effort is not None:
            return f"Effort · {_safe_effort(effort)}"
    if provider in _GOOGLE_ROUTES and isinstance(native := settings.get("google_thinking_config"), dict):
        if "thinking_level" in native:
            return _safe_effort(native["thinking_level"])
        if "thinking_budget" in native:
            return _budget_summary(native["thinking_budget"])
        if native:
            return "Provider default"
    if provider == "openrouter" and isinstance(native := settings.get("openrouter_reasoning"), dict):
        if native.get("enabled") is False:
            return "Off"
        if "effort" in native:
            return _safe_effort(native["effort"])
        if "max_tokens" in native:
            return _budget_summary(native["max_tokens"])
        return "Custom"
    value = settings.get("thinking")
    if value is None:
        return "Provider default"
    if isinstance(value, bool):
        return "On (requested)" if value else "Off (requested)"
    if isinstance(value, str):
        choice = next((c for c in _choices(route) if c.value == value), None)
        if choice is not None:
            # An authored unified effort is translated by the SDK, not by our
            # explicit UI budget presets. Do not claim a preset's token count.
            return _label(choice.value)
    return "Custom"


def _safe_effort(value: JsonValue) -> str:
    # Do not echo arbitrary opaque settings into a status line or public catalog.
    return (
        value.capitalize()
        if isinstance(value, str) and value.lower() in {"minimal", "low", "medium", "high", "xhigh", "max"}
        else "Provider default"
        if value is None
        else "Custom"
    )


def _budget_summary(value: JsonValue) -> str:
    if not isinstance(value, int) or isinstance(value, bool):
        return "Custom budget"
    return "Off" if value == 0 else "Dynamic budget" if value == -1 else f"Budget · {value:,} tokens"
