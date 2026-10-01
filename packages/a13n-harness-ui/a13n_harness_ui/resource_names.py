"""Plain-text starter resource names, independent of terminal labels and styling.

These are authoring defaults, not a normalizer for existing or user-authored
names. Only generated formatting is ASCII; user-supplied Unicode stays intact.
"""

from __future__ import annotations

import re

_PROVIDER_NAMES = {
    "codex": "Codex",
    "openai-codex": "Codex",
    "grok-subscription": "Grok Subscription",
    "grok": "xAI",
    "openai-responses": "OpenAI",
    "openai-chat": "OpenAI Chat",
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "google": "Google",
    "google-gla": "Google",
    "openrouter": "OpenRouter",
    "deepseek": "DeepSeek",
    "zai": "Z.AI",
    "moonshotai": "Moonshot AI",
    "groq": "Groq",
    "mistral": "Mistral",
    "cerebras": "Cerebras",
    "sambanova": "SambaNova",
    "vercel": "Vercel AI Gateway",
    "together": "Together AI",
    "fireworks": "Fireworks AI",
}


def model_name(provider: str, model_id: str) -> str:
    """Suggest a Model name without changing the case-sensitive route identity."""
    provider_name = _PROVIDER_NAMES.get(provider, provider)
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
    return (title if title.startswith(f"{provider_name} ") else f"{provider_name} - {title}")[:110]


def coding_agent_name(connection_name: str) -> str:
    """Use the same coding-role suffix in previews, suggestions, and publication."""
    return f"{connection_name[:110]} - Coding"
