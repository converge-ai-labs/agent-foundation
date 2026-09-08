"""Reviewed API-key provider choices and explicit, editable starter settings.

This is a supported HTTP/API-key subset of Pydantic AI, not provider discovery.
Cloud IAM, subscription transports and arbitrary SDK constructor arguments are
not interchangeable with an API key and a base URL.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

from pydantic import JsonValue


@dataclass(frozen=True, slots=True)
class ApiProvider:
    route: str
    label: str
    base_url: str
    credential_env: str
    transport: Literal["native", "openai-client"] = "native"


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
    ApiProvider("mistral", "Mistral", "https://api.mistral.ai", "MISTRAL_API_KEY"),
    ApiProvider("together", "Together AI", "https://api.together.xyz/v1", "TOGETHER_API_KEY", "openai-client"),
    ApiProvider(
        "fireworks", "Fireworks AI", "https://api.fireworks.ai/inference/v1", "FIREWORKS_API_KEY", "openai-client"
    ),
    ApiProvider("grok", "xAI · Grok API", "https://api.x.ai/v1", "XAI_API_KEY", "openai-client"),
)
API_PROVIDER_BY_ROUTE = {provider.route: provider for provider in API_PROVIDERS}

# Starter suggestions, not a live inventory or an account entitlement claim.
# Model IDs are case-sensitive and custom endpoints can use other identifiers.
_OPENAI_MODELS = ("gpt-5.6-sol", "gpt-6-astra", "gpt-5.6-terra", "gpt-5.5", "gpt-5.4", "gpt-5.4-mini")
API_MODEL_SUGGESTIONS: dict[str, tuple[str, ...]] = {
    "openai-responses": _OPENAI_MODELS,
    "openai-chat": _OPENAI_MODELS,
    "anthropic": ("claude-sonnet-4-6", "claude-opus-4-6", "claude-haiku-4-5", "claude-sonnet-4-5"),
    "google": ("gemini-3.1-pro-preview", "gemini-3.5-flash", "gemini-2.5-pro", "gemini-2.5-flash"),
    "openrouter": ("anthropic/claude-sonnet-4.6", "openai/gpt-5.4", "google/gemini-2.5-pro"),
    "deepseek": ("deepseek-v4-pro", "deepseek-v4-flash", "deepseek-reasoner", "deepseek-chat"),
    "zai": ("glm-5.3", "glm-5.2", "glm-4.7", "glm-4.5"),
    "moonshotai": ("kimi-k2.6", "kimi-k2.5", "kimi-k2-thinking"),
    "groq": ("openai/gpt-oss-120b", "llama-3.3-70b-versatile", "llama-3.1-8b-instant"),
    "mistral": ("mistral-large-latest", "mistral-small-latest", "codestral-latest"),
    "together": ("meta-llama/Llama-3.3-70B-Instruct-Turbo", "Qwen/Qwen3-235B-A22B-Instruct-2507-tput"),
    "fireworks": ("accounts/fireworks/models/llama-v3p3-70b-instruct", "accounts/fireworks/models/gpt-oss-120b"),
    "grok": ("grok-4.6", "grok-4.5", "grok-4.20-0309-reasoning"),
}


def connection_display_name(provider: str, model_id: str) -> str:
    """Readable resource title; custom model identifiers retain their exact spelling."""
    labels = {
        "codex": "Codex",
        "grok-subscription": "Grok Subscription",
        "grok": "xAI",
        "openai-responses": "OpenAI",
        "openai-chat": "OpenAI Chat",
        "openai": "OpenAI",
        "anthropic": "Anthropic",
        "google": "Google",
        "moonshotai": "Moonshot AI",
        "zai": "Z.AI",
    }
    provider_name = labels.get(
        provider, API_PROVIDER_BY_ROUTE[provider].label if provider in API_PROVIDER_BY_ROUTE else provider
    )
    title = model_id
    families = {
        "gpt-": "GPT-",
        "claude-": "Claude ",
        "gemini-": "Gemini ",
        "deepseek-": "DeepSeek ",
        "glm-": "GLM ",
        "kimi-": "Kimi ",
        "grok-": "Grok ",
    }
    for prefix, label in families.items():
        if model_id.startswith(prefix):
            suffix = re.sub(r"(?<=\d)-(?=\d)", ".", model_id[len(prefix) :])
            title = label + suffix.replace("-", " ").title()
            break
    return (title if title.startswith(f"{provider_name} ") else f"{provider_name} · {title}")[:110]


def known_context_window(provider: str, model_id: str, base_url: str) -> int | None:
    """Read the Harness-owned bundled catalog; never query the network."""
    from a13n_harness.pricing import get_default_pricing_catalog

    catalog_provider = (
        "openai" if provider in {"openai-responses", "openai-chat"} else "x-ai" if provider == "grok" else provider
    )
    entry = get_default_pricing_catalog().resolve(model_id, provider=catalog_provider, provider_url=base_url)
    return entry.context_window if entry is not None else None


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


def settings_presets(provider: str, model_id: str) -> tuple[SettingsPreset, ...]:
    """Expand recommendations using the installed upstream model profile, without I/O."""
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
                "max_tokens": 16384,
            },
        )
        profile = anthropic_model_profile(model_id) or {}
        anthropic_choices = (
            (adaptive, interleaved) if profile.get("anthropic_supports_adaptive_thinking") else (interleaved, adaptive)
        )
        if profile.get("anthropic_disallows_budget_thinking"):
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
            "Only for models supporting Pydantic AI's unified thinking setting",
            {"thinking": "high"},
        ),
        SettingsPreset(
            "low",
            "Low thinking",
            "Only for models supporting Pydantic AI's unified thinking setting",
            {"thinking": "low"},
        ),
    )
