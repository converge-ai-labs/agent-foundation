"""Creation-time tool choices, not a runtime compatibility or fallback engine.

Upstream owns transport support and native tool validation. These suggestions
also account for the Host function tools in the coding Agent starter template.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from pydantic import JsonValue

from a13n_harness_ui.model_presets import API_MODEL_SUGGESTIONS, API_PROVIDER_BY_ROUTE

NATIVE_TOOLS_DOCS = "https://pydantic.dev/docs/ai/tools-toolsets/native-tools/"


@dataclass(frozen=True, slots=True)
class ToolChoice:
    key: str
    label: str
    description: str
    recommended: bool = False


_NATIVE_CHOICES = (
    ToolChoice(
        "web_search", "Web Search · native", "Provider search and citations; provider usage charges may apply.", True
    ),
    ToolChoice(
        "web_fetch",
        "Web Fetch · native",
        "Provider reads URLs in model context; not a crawler or local download.",
        True,
    ),
    ToolChoice("x_search", "X Search · native", "Search X posts through xAI; separate from general web search."),
    ToolChoice(
        "image_generation",
        "Image Generation · native",
        "Save generated images to Thread tmp; return file paths. Uses image quota.",
        True,
    ),
    ToolChoice(
        "code_execution",
        "Code Execution · native",
        "Provider sandbox, not your workspace shell; files stay with the provider.",
    ),
    ToolChoice(
        "mcp_server",
        "Remote MCP · provider-hosted",
        "Requires a public remote server URL and label; provider connects, not the Host.",
    ),
    ToolChoice(
        "file_search",
        "File Search · native",
        "Requires provider stores; no local uploads. Google: deselect other native tools first.",
    ),
    ToolChoice(
        "advisor", "Advisor · native", "Requires an advisor model ID; additional model usage is billed by the provider."
    ),
)


def tool_choices(
    route: str, *, authentication: str | None = None, base_url: str | None = None
) -> tuple[ToolChoice, ...]:
    """Suggest only tools usable by the selected coding-Agent connection."""
    provider, _, model = route.partition(":")
    kinds: set[str] = set()
    known = authentication in {"codex_subscription", "grok_subscription"} or model in API_MODEL_SUGGESTIONS.get(
        provider, ()
    )
    if authentication == "codex_subscription":
        kinds = {"web_search", "image_generation"}
    elif authentication == "grok_subscription":
        kinds = {"web_search"}
    else:
        preset = API_PROVIDER_BY_ROUTE.get(provider)
        official = preset is not None and (base_url is None or base_url.rstrip("/") == preset.base_url.rstrip("/"))
        if official:
            # Reuse upstream's transport inventory; do not maintain another tool
            # support matrix. Model/account restrictions still apply at execution.
            from pydantic_ai.models.anthropic import AnthropicModel
            from pydantic_ai.models.google import GoogleModel
            from pydantic_ai.models.openai import OpenAIResponsesModel
            from pydantic_ai.models.openrouter import OpenRouterModel
            from pydantic_ai.models.xai import XaiModel

            model_type = {
                "openai-responses": OpenAIResponsesModel,
                "anthropic": AnthropicModel,
                "google": GoogleModel,
                "openrouter": OpenRouterModel,
                "xai": XaiModel,
            }.get(provider)
            if model_type is not None:
                kinds = {tool.kind for tool in model_type.supported_native_tools()}
            if provider == "openai-responses":
                if model not in API_MODEL_SUGGESTIONS[provider]:
                    kinds.discard("image_generation")
            if provider == "anthropic":
                from pydantic_ai.profiles.anthropic import anthropic_model_profile

                profile_tools = (anthropic_model_profile(model) or {}).get("supported_native_tools")
                if profile_tools is not None:
                    kinds &= {tool.kind for tool in profile_tools}
            if provider == "google":
                from pydantic_ai.profiles.google import google_model_profile

                profile = google_model_profile(model) or {}
                if not profile.get("google_supports_tool_combination", False) or not profile.get(
                    "supports_tools", True
                ):
                    kinds.clear()
                # Gemini image-only models cannot use the starter's function tools.
                kinds.discard("image_generation")
    return tuple(
        replace(choice, recommended=choice.recommended and known) for choice in _NATIVE_CHOICES if choice.key in kinds
    )


def selected_tool_capabilities(
    selected: tuple[str, ...],
    *,
    authentication: str | None = None,
    parameters: Mapping[str, Mapping[str, JsonValue]] | None = None,
) -> list[dict[str, JsonValue]]:
    """Materialize ordinary Capability selections; upstream validates native options."""
    from pydantic_ai.capabilities import NativeTool

    parameters = parameters or {}
    # Keep HTTP fetch/download available independently of provider-native tools.
    # Only search and scrape are replaced, each by its native counterpart.
    capabilities: list[dict[str, JsonValue]] = [
        {
            "capability": "web",
            "configuration": {
                "search": {"mode": "off" if "web_search" in selected else "host"},
                "scrape": {"mode": "off" if "web_fetch" in selected else "host"},
            },
        }
    ]
    for key in selected:
        options: dict[str, Any] = {"kind": key, **parameters.get(key, {})}
        if key == "web_search" and authentication == "codex_subscription":
            options.setdefault("external_web_access", True)
        NativeTool.from_spec(**options)
        capabilities.append(
            {
                "capability": "native_image_generation",
                "configuration": {k: v for k, v in options.items() if k != "kind"},
            }
            if key == "image_generation"
            else {"capability": "NativeTool", "configuration": options}
        )
    return capabilities
