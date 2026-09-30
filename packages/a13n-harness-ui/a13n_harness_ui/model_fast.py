"""Fast request controls shared by CLI, WebUI and immutable capture.

These describe request settings, never account entitlement or observed speed.
Discovery is local and does not construct models or contact providers.
"""

from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, JsonValue
from pydantic_ai.profiles.anthropic import anthropic_model_profile

from a13n_harness_ui.errors import CompositionError

type FastSelection = bool | Literal["ultrafast"]
type FastState = Literal["on", "off", "ultrafast", "default"]


class FastControl(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    supported: bool
    state: FastState
    reason: str | None = None
    ultrafast_supported: bool = False
    ultrafast_reason: str | None = None


def _setting(route: str) -> str | None:
    provider = route.partition(":")[0]
    if provider in {"openai", "openai-chat", "openai-responses", "openai-codex"}:
        return "openai_service_tier"
    if provider == "anthropic":
        return "anthropic_speed"
    if provider in {"google-gla", "gemini"}:
        return "service_tier"
    return None


def _supported(route: str) -> bool:
    provider, _, name = route.partition(":")
    if provider in {"openai", "openai-chat", "openai-responses", "openai-codex"}:
        return name.startswith(("gpt-5", "gpt-6", "o3", "o4"))
    if provider == "anthropic":
        # Older SDK profiles still advertise retired 4.6/4.7 Fast support.
        return name.startswith(("claude-opus-4-8", "claude-opus-5")) and bool(
            (anthropic_model_profile(name) or {}).get("anthropic_supports_fast_speed")
        )
    if provider in {"google-gla", "gemini"}:
        return name.startswith(("gemini-2.5", "gemini-3"))
    return False


def _conflict(settings: Mapping[str, JsonValue]) -> bool:
    body = settings.get("extra_body")
    headers = settings.get("extra_headers")
    return (isinstance(body, dict) and bool({"service_tier", "speed"} & body.keys())) or (
        isinstance(headers, dict)
        and any(key.lower() in {"x-gemini-service-tier", "x-vertex-ai-llm-request-type"} for key in headers)
    )


def fast_state(route: str, settings: Mapping[str, JsonValue]) -> FastState:
    """Read authored/captured settings without mistaking provider defaults for Off."""
    key = _setting(route)
    if key is None or _conflict(settings):
        return "default"
    value = settings.get(key)
    if key != "anthropic_speed" and value is None:
        value = settings.get("service_tier")
    if value == "ultrafast":
        return "ultrafast"
    if value in ("priority", "fast"):
        return "on"
    if value in ("default", "standard", "flex"):
        return "off"
    return "default"


def describe_fast(route: str, settings: Mapping[str, JsonValue]) -> FastControl:
    reason = None
    if not _supported(route):
        reason = "Fast controls are not available for this model connection."
    elif _conflict(settings):
        reason = "Fast is controlled by extra_body or extra_headers. Edit the Model configuration first."
    ultrafast_reason = reason
    if ultrafast_reason is None and route != "openai-codex:gpt-6-astra":
        ultrafast_reason = "Ultrafast requires GPT-6 Astra through a Codex subscription connection."
    return FastControl(
        supported=reason is None,
        state=fast_state(route, settings),
        reason=reason,
        ultrafast_supported=ultrafast_reason is None,
        ultrafast_reason=ultrafast_reason,
    )


def apply_fast(route: str, settings: Mapping[str, JsonValue], selected: FastSelection | None) -> dict[str, JsonValue]:
    effective = dict(settings)
    if selected is None:
        return effective
    control = describe_fast(route, settings)
    if not control.supported:
        raise CompositionError(control.reason or "Fast is unavailable.", code="model_fast_unavailable")
    if selected == "ultrafast" and not control.ultrafast_supported:
        raise CompositionError(control.ultrafast_reason or "Ultrafast is unavailable.", code="model_fast_unavailable")
    if route.startswith("anthropic:"):
        effective["anthropic_speed"] = "fast" if selected else "standard"
    else:
        # Let the native SDK translate the generic tier, including Gemini's
        # default -> standard mapping. Remove a competing OpenAI native value.
        effective.pop("openai_service_tier", None)
        effective["service_tier"] = "ultrafast" if selected == "ultrafast" else "priority" if selected else "default"
    return effective
