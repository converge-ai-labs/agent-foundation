"""Reviewed API-key provider choices and explicit, editable starter settings.

This is a supported HTTP/API-key subset of Pydantic AI, not provider discovery.
Cloud IAM, subscription transports and arbitrary SDK constructor arguments are
not interchangeable with an API key and a base URL.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlsplit

from pydantic import JsonValue

if TYPE_CHECKING:
    from a13n_harness.spec import HarnessModelCharacteristics, ModelCapability


@dataclass(frozen=True, slots=True)
class ApiProvider:
    route: str
    label: str
    base_url: str
    credential_env: str
    transport: Literal["native", "openai-client", "xai"] = "native"
    supports_session_affinity: bool = True


API_PROVIDERS = (
    ApiProvider("openai-responses", "OpenAI · Responses", "https://api.openai.com/v1", "OPENAI_API_KEY"),
    ApiProvider("openai-chat", "OpenAI-compatible · Chat Completions", "https://api.openai.com/v1", "OPENAI_API_KEY"),
    ApiProvider("anthropic", "Anthropic", "https://api.anthropic.com", "ANTHROPIC_API_KEY"),
    ApiProvider("google", "Google · Gemini API", "https://generativelanguage.googleapis.com", "GEMINI_API_KEY"),
    ApiProvider("openrouter", "OpenRouter", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", "openai-client"),
    ApiProvider("deepseek", "DeepSeek", "https://api.deepseek.com", "DEEPSEEK_API_KEY", "openai-client"),
    ApiProvider("zai", "Z.AI / GLM", "https://api.z.ai/api/paas/v4", "ZAI_API_KEY", "openai-client"),
    ApiProvider(
        "moonshotai", "Moonshot AI / Kimi", "https://api.moonshot.ai/v1", "MOONSHOTAI_API_KEY", "openai-client"
    ),
    ApiProvider("groq", "Groq", "https://api.groq.com", "GROQ_API_KEY"),
    ApiProvider("mistral", "Mistral", "https://api.mistral.ai", "MISTRAL_API_KEY", supports_session_affinity=False),
    ApiProvider("together", "Together AI", "https://api.together.xyz/v1", "TOGETHER_API_KEY", "openai-client"),
    ApiProvider(
        "fireworks", "Fireworks AI", "https://api.fireworks.ai/inference/v1", "FIREWORKS_API_KEY", "openai-client"
    ),
    ApiProvider("cerebras", "Cerebras", "https://api.cerebras.ai/v1", "CEREBRAS_API_KEY", "openai-client"),
    ApiProvider("sambanova", "SambaNova", "https://api.sambanova.ai/v1", "SAMBANOVA_API_KEY", "openai-client"),
    ApiProvider(
        "vercel", "Vercel AI Gateway", "https://ai-gateway.vercel.sh/v1", "VERCEL_AI_GATEWAY_API_KEY", "openai-client"
    ),
    ApiProvider("grok", "xAI · Chat Completions", "https://api.x.ai/v1", "XAI_API_KEY", "openai-client"),
    ApiProvider("xai", "xAI · Native SDK (gRPC)", "", "XAI_API_KEY", "xai", supports_session_affinity=False),
)
API_PROVIDER_BY_ROUTE = {provider.route: provider for provider in API_PROVIDERS}

# Starter suggestions, not a live inventory or an account entitlement claim.
# Model IDs are case-sensitive and custom endpoints can use other identifiers.
_OPENAI_MODELS = (
    "gpt-6.1-sol",
    "gpt-6-astra",
    "gpt-6-luna",
    "gpt-5.6-terra",
    "gpt-6-sol",
    "gpt-5.6-sol",
    "gpt-5.5",
    "gpt-5.4",
    "gpt-5.4-mini",
)
API_MODEL_SUGGESTIONS: dict[str, tuple[str, ...]] = {
    "openai-responses": _OPENAI_MODELS,
    "openai-chat": _OPENAI_MODELS,
    "anthropic": (
        "claude-sonnet-5-5",
        "claude-opus-5-5",
        "claude-haiku-5-5",
        "claude-fable-5-1",
        "claude-sonnet-4-6",
        "claude-opus-4-6",
        "claude-haiku-4-5",
    ),
    "google": (
        "gemini-3.8-flash",
        "gemini-3.7-flash",
        "gemini-3.6-flash",
        "gemini-3.5-flash-lite",
        "gemini-3.1-pro-preview",
        "gemini-3.5-flash",
        "gemini-2.5-pro",
        "gemini-2.5-flash",
    ),
    "openrouter": (
        "anthropic/claude-sonnet-5.5",
        "openai/gpt-6.1-sol",
        "moonshotai/kimi-k3",
        "anthropic/claude-sonnet-4.6",
        "openai/gpt-5.4",
        "google/gemini-2.5-pro",
    ),
    "deepseek": ("deepseek-flash", "deepseek-v4-pro", "deepseek-v4-flash"),
    "zai": ("glm-5.3", "glm-5.3-flash", "glm-5.3-flashx", "glm-5.2", "glm-4.7", "glm-4.5"),
    "moonshotai": (
        "kimi-k3",
        "kimi-k2.7-code",
        "kimi-k2.7-code-highspeed",
        "kimi-k2.6",
        "kimi-k2.5",
        "kimi-k2-thinking",
    ),
    "groq": ("openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b"),
    "mistral": ("mistral-large-4", "mistral-small-latest", "codestral-latest"),
    "together": ("moonshotai/Kimi-K3", "deepseek-ai/DeepSeek-V4.1-Flash", "zai-org/GLM-5.3"),
    "fireworks": ("accounts/fireworks/models/ember-1", "accounts/fireworks/models/gpt-oss-120b"),
    "cerebras": ("gpt-oss-120b", "qwen-3.8-27b"),
    "sambanova": ("MiniMax-M2.7", "DeepSeek-V3.1", "Meta-Llama-3.3-70B-Instruct", "gpt-oss-120b"),
    "vercel": ("anthropic/claude-sonnet-5.5", "openai/gpt-6.1-sol", "moonshotai/kimi-k3"),
    "grok": ("grok-4.7", "grok-4.6", "grok-4.5", "grok-4.20-0309-reasoning"),
    "xai": ("grok-4.7", "grok-4.6", "grok-4.5", "grok-4.20-0309-reasoning"),
}


def known_context_window(provider: str, model_id: str, base_url: str) -> int | None:
    """Read current official facts, then bundled pricing metadata; never fetch."""
    from a13n_harness.model_catalog import get_official_model_catalog
    from a13n_harness.pricing import get_default_pricing_catalog

    catalog_provider = (
        "openai"
        if provider in {"openai-responses", "openai-chat"}
        else "x-ai"
        if provider in {"grok", "xai"}
        else provider
    )
    official_provider = {"google": "google-gla", "x-ai": "grok"}.get(catalog_provider, catalog_provider)
    official = get_official_model_catalog().get(f"{official_provider}:{model_id}")
    if official is not None and official.characteristics.context_window_tokens is not None:
        return official.characteristics.context_window_tokens
    entry = get_default_pricing_catalog().resolve(model_id, provider=catalog_provider, provider_url=base_url)
    return entry.context_window if entry is not None else None


def known_model_capabilities(route: str) -> frozenset[ModelCapability] | None:
    characteristics = known_model_characteristics(route)
    return characteristics.capabilities if characteristics is not None else None


def known_model_characteristics(route: str) -> HarnessModelCharacteristics | None:
    """Materialize reviewed input facts for the selected transport, without I/O.

    Exact IDs remain case-sensitive, including behind custom base URLs. A URL
    cannot establish model identity or endpoint compatibility. Unknown IDs and
    undeclared catalog facts return None, not a claim of text-only support.
    """
    from a13n_harness.model_catalog import get_official_model_catalog
    from a13n_harness.spec import HarnessModelCharacteristics, ModelCapability, UrlInputSupport

    provider, _, model_id = route.partition(":")
    catalog_provider = {
        "openai-responses": "openai",
        "openai-chat": "openai",
        "openai-codex": "openai",
        "openai-chatgpt": "openai",
        "google": "google-gla",
        "xai": "grok",
    }.get(provider, provider)
    catalog_key = f"{catalog_provider}:{model_id}"
    if provider in {"openrouter", "vercel"}:
        publisher, _, upstream_id = model_id.partition("/")
        catalog_provider = {
            "openai": "openai",
            "anthropic": "anthropic",
            "google": "google-gla",
            "x-ai": "grok",
            "moonshotai": "moonshotai",
            "z-ai": "zai",
            "deepseek": "deepseek",
        }.get(publisher, "")
        # Reviewed routed spellings; do not strip arbitrary suffixes or rewrite
        # version punctuation on custom model IDs.
        catalog_key = {
            "anthropic/claude-sonnet-5.5": "anthropic:claude-sonnet-5-5",
            "anthropic/claude-opus-5.5": "anthropic:claude-opus-5-5",
            "anthropic/claude-haiku-5.5": "anthropic:claude-haiku-5-5",
            "anthropic/claude-sonnet-4.6": "anthropic:claude-sonnet-4-6",
            "anthropic/claude-opus-4.6": "anthropic:claude-opus-4-6",
            "anthropic/claude-haiku-4.5": "anthropic:claude-haiku-4-5",
            "anthropic/claude-sonnet-4.5": "anthropic:claude-sonnet-4-5",
        }.get(model_id, f"{catalog_provider}:{upstream_id}")
    entry = get_official_model_catalog().get(catalog_key)
    if entry is None or "capabilities" not in entry.characteristics.model_fields_set:
        return None
    # Native URL facts are selected separately from binary input modalities.
    # Compatible transports are reviewed for image input only. PDF remains a
    # manual opt-in even when the source declares native document support.
    supported = (
        frozenset(
            {
                ModelCapability.IMAGE_UNDERSTANDING,
                ModelCapability.AUDIO_UNDERSTANDING,
                ModelCapability.VIDEO_UNDERSTANDING,
            }
        )
        if provider in {"google", "google-gla"}
        else frozenset({ModelCapability.IMAGE_UNDERSTANDING})
    )
    return HarnessModelCharacteristics(
        capabilities=entry.characteristics.capabilities & supported,
        url_input=entry.characteristics.url_input if provider in {"google", "google-gla"} else UrlInputSupport(),
    )


def starter_tool_capabilities(
    route: str,
    *,
    authentication: str | None = None,
    base_url: str | None = None,
) -> list[dict[str, JsonValue]]:
    """Materialize the same editable recommendations shown in interactive setup."""
    from a13n_harness_ui.tool_presets import selected_tool_capabilities, tool_choices

    choices = tool_choices(route, authentication=authentication, base_url=base_url)
    return selected_tool_capabilities(
        tuple(choice.key for choice in choices if choice.recommended), authentication=authentication
    )


def validate_base_url(value: str) -> str:
    """Keep credentials out of a public recipe; local HTTP endpoints are valid."""
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or any(char.isspace() for char in value)
    ):
        raise ValueError("Use an HTTP(S) base URL without credentials, query parameters, or fragments.")
    # Validate malformed ports even though urlsplit otherwise accepts them.
    _ = parsed.port
    return value


@dataclass(frozen=True, slots=True)
class SettingsPreset:
    key: str
    label: str
    description: str
    settings: dict[str, JsonValue]

    @property
    def output_limit_label(self) -> str:
        tokens = self.settings.get("max_tokens")
        return f"Output limit: {tokens:,} tokens" if isinstance(tokens, int) else "Output limit: provider default"


# Creation-time recommendations, not provider limits or a runtime model registry.
# Keep exact reviewed IDs separate from suggestions and permissive upstream profiles:
# adding a model suggestion must not silently certify its output budget.
# Provider references and budget semantics: docs/a13n-harness-ui/models-and-authentication.md.
_OUTPUT_PRESET_MODELS: dict[str, frozenset[str]] = {
    "openai": frozenset({"gpt-5", "gpt-5.4", "gpt-5.4-mini", "gpt-5.5", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-6-astra"}),
    "anthropic": frozenset(
        {
            "claude-sonnet-5-5",
            "claude-opus-5-5",
            "claude-haiku-5-5",
            "claude-fable-5-1",
            "claude-sonnet-4-6",
            "claude-opus-4-6",
            "claude-haiku-4-5",
            "claude-sonnet-4-5",
        }
    ),
    "google": frozenset(
        {
            "gemini-3.8-flash",
            "gemini-3.7-flash",
            "gemini-3.1-pro-preview",
            "gemini-3.5-flash",
            "gemini-2.5-pro",
            "gemini-2.5-flash",
        }
    ),
    "deepseek": frozenset({"deepseek-flash", "deepseek-v4-pro", "deepseek-v4-flash", "deepseek-reasoner"}),
    "zai": frozenset({"glm-5.3", "glm-5.2", "glm-4.7", "glm-4.5"}),
    "moonshotai": frozenset({"kimi-k2.6", "kimi-k2.5", "kimi-k2-thinking"}),
}
_OUTPUT_TOKEN_BUDGETS = {
    "openai": {"low": 16384, "medium": 32768, "high": 65536, "xhigh": 65536},
    "anthropic": {"adaptive": 32768, "interleaved": 16384, "low": 16384, "medium": 32768, "high": 32768},
    # Native Gemini 2.5 high thinking uses 24,576 tokens; leave room for the answer.
    "google": {"low": 16384, "medium": 32768, "high": 32768},
    "deepseek": {"thinking": 32768},
    "zai": {"thinking": 32768},
    "moonshotai": {"thinking": 32768},
}


def settings_presets(provider: str, model_id: str) -> tuple[SettingsPreset, ...]:
    """Materialize paired thinking/output recommendations as editable native settings."""
    if provider == "openai-chatgpt":
        provider = "openai-responses"
    if provider in {"openai", "openai-responses", "openai-chat"} and model_id == "gpt-6.1-sol":
        defaults: dict[str, JsonValue] = (
            {"openai_reasoning_summary": "detailed", "openai_store": False} if provider != "openai-chat" else {}
        )
        return (
            *(
                SettingsPreset(
                    effort,
                    f"{effort.title()} thinking",
                    "Reasoning effort; provider output limit",
                    {**defaults, "openai_reasoning_effort": effort},
                )
                for effort in ("high", "medium", "low", "xhigh", "max")
            ),
            SettingsPreset("default", "Provider defaults", "Do not force a reasoning effort", defaults),
        )
    presets = _thinking_presets(provider, model_id)
    budget_provider = "openai" if provider in {"openai-responses", "openai-chat"} else provider
    if provider == "openrouter":
        # Routed limits can differ from the native endpoint; only reviewed routes
        # receive a budget. Do not normalize arbitrary aliases or route suffixes.
        budget_provider, model_id = {
            "anthropic/claude-sonnet-4.6": ("anthropic", "claude-sonnet-4-6"),
            "openai/gpt-5.4": ("openai", "gpt-5.4"),
            "google/gemini-2.5-pro": ("google", "gemini-2.5-pro"),
        }.get(model_id, ("", ""))
    if model_id not in _OUTPUT_PRESET_MODELS.get(budget_provider, frozenset()):
        return presets
    budgets = _OUTPUT_TOKEN_BUDGETS[budget_provider]
    return tuple(
        replace(preset, settings={**preset.settings, "max_tokens": budgets[preset.key]})
        if preset.key in budgets
        else preset
        for preset in presets
    )


def _thinking_presets(provider: str, model_id: str) -> tuple[SettingsPreset, ...]:
    """Expand thinking choices using the installed upstream model profile, without I/O."""
    if provider == "deepseek" and model_id == "deepseek-flash":
        return (
            SettingsPreset(
                "thinking",
                "Thinking · preserved",
                "Use native thinking; preserve reasoning_content through tools and turns",
                {"extra_body": {"thinking": {"type": "enabled"}}},
            ),
            SettingsPreset("default", "Provider defaults", "Use the model's native thinking mode", {}),
        )
    if provider == "moonshotai" and model_id == "kimi-k3":
        return (
            *(
                SettingsPreset(
                    effort,
                    f"{effort.title()} thinking · preserved",
                    "Always-on reasoning; preserve reasoning_content through tools and turns",
                    {"openai_reasoning_effort": effort},
                )
                for effort in ("max", "high", "low")
            ),
            SettingsPreset("default", "Provider defaults", "Use the model's native reasoning effort", {}),
        )
    if provider == "moonshotai" and model_id in {"kimi-k2.7-code", "kimi-k2.7-code-highspeed"}:
        return (
            SettingsPreset(
                "thinking",
                "Thinking · preserved",
                "Always-on reasoning; preserve reasoning_content through tools and turns",
                {"extra_body": {"thinking": {"type": "enabled", "keep": "all"}}},
            ),
            SettingsPreset("default", "Provider defaults", "Always-on preserved thinking", {}),
        )
    if provider in {"deepseek", "zai", "moonshotai"}:
        from pydantic_ai.profiles.deepseek import deepseek_model_profile
        from pydantic_ai.profiles.moonshotai import moonshotai_model_profile
        from pydantic_ai.profiles.zai import zai_model_profile

        profile_factory = {
            "deepseek": deepseek_model_profile,
            "zai": zai_model_profile,
            "moonshotai": moonshotai_model_profile,
        }[provider]
        thinking_profile = profile_factory(model_id) or {}
        default = SettingsPreset(
            "default", "Provider defaults", "No forced thinking; retain returned reasoning content", {}
        )
        if not thinking_profile.get("supports_thinking"):
            return (default,)
        preserved_settings: dict[str, JsonValue] = {"thinking": True}
        if provider == "zai":
            preserved_settings["zai_clear_thinking"] = False
        return (
            SettingsPreset(
                "thinking",
                "Thinking · preserved",
                "Use the model's native thinking level; preserve reasoning_content through tools and turns",
                preserved_settings,
            ),
            default,
        )
    if provider == "anthropic":
        from pydantic_ai.profiles.anthropic import anthropic_model_profile

        adaptive = SettingsPreset(
            "adaptive",
            "Adaptive thinking",
            "Newer Claude models · interleaving is automatic; return thinking summaries",
            {
                "anthropic_thinking": {"type": "adaptive", "display": "summarized"},
                "anthropic_effort": "high",
                "max_tokens": 16384,
            },
        )
        interleaved = SettingsPreset(
            "interleaved",
            "Interleaved extended thinking",
            "Compatible Claude models · 8,192 thinking tokens; return summaries",
            {
                "anthropic_thinking": {"type": "enabled", "budget_tokens": 8192, "display": "summarized"},
                "anthropic_betas": ["interleaved-thinking-2025-05-14"],
                # Also retain this baseline for custom IDs: the explicit 8,192
                # thinking budget must not fall back to the adapter's 4,096 cap.
                "max_tokens": 16384,
            },
        )
        profile = anthropic_model_profile(model_id) or {}
        anthropic_choices = (
            (adaptive, interleaved) if profile.get("anthropic_supports_adaptive_thinking") else (interleaved, adaptive)
        )
        if profile.get("anthropic_disallows_budget_thinking") or model_id == "claude-haiku-5-5":
            anthropic_choices = (adaptive,)
        return (
            *anthropic_choices,
            SettingsPreset("default", "Provider defaults", "No explicit thinking configuration", {}),
        )
    if provider in {"openai", "openai-responses", "openai-chat", "google", "openrouter"}:
        supports_reasoning = True
        if provider.startswith("openai"):
            from pydantic_ai.profiles.openai import openai_model_profile

            supports_reasoning = bool((openai_model_profile(model_id) or {}).get("openai_supports_reasoning"))
        choices: list[SettingsPreset] = []
        efforts = ("high", "medium", "low", "xhigh") if provider.startswith("openai") else ("high", "medium", "low")
        for effort in efforts:
            settings: dict[str, JsonValue] = {"thinking": effort}
            if provider in {"openai", "openai-responses"}:
                settings["openai_store"] = False
                if supports_reasoning:
                    settings["openai_reasoning_summary"] = "detailed"
            # Google's native unified-thinking translation already requests
            # include_thoughts and maps effort to this model's budget or level.
            elif provider == "openrouter":
                settings = {"openrouter_reasoning": {"effort": effort, "exclude": False}}
            choices.append(
                SettingsPreset(
                    effort,
                    f"{effort.title()} thinking",
                    "Reasoning-capable models only; return available thinking / summaries",
                    settings,
                )
            )
        defaults: dict[str, JsonValue] = {"openai_store": False} if provider in {"openai", "openai-responses"} else {}
        if provider in {"openai", "openai-responses"} and supports_reasoning:
            defaults["openai_reasoning_summary"] = "detailed"
        default = SettingsPreset("default", "Provider defaults", "Do not force a reasoning effort", defaults)
        return (*choices, default) if supports_reasoning else (default, *choices)
    return (
        SettingsPreset(
            "default", "Provider defaults", "Use this model's native behavior; retain returned thinking", {}
        ),
        SettingsPreset(
            "high",
            "High thinking",
            "Only for models supporting the unified thinking setting",
            {"thinking": "high"},
        ),
        SettingsPreset(
            "low",
            "Low thinking",
            "Only for models supporting the unified thinking setting",
            {"thinking": "low"},
        ),
    )
