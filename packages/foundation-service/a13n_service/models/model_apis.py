"""Finite calling-API bindings and their Foundation-owned request fields."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from pydantic_ai.models import Model as PydanticModel
from pydantic_ai.models.anthropic import AnthropicModel, AnthropicModelSettings
from pydantic_ai.models.bedrock import BedrockConverseModel, BedrockModelSettings
from pydantic_ai.models.bedrock_mantle import BedrockMantleChatModel, BedrockMantleResponsesModel
from pydantic_ai.models.google import GoogleModel, GoogleModelSettings
from pydantic_ai.models.ollama import OllamaModel
from pydantic_ai.models.openai import (
    OpenAIChatModel,
    OpenAIChatModelSettings,
    OpenAIResponsesModel,
    OpenAIResponsesModelSettings,
)
from pydantic_ai.models.openrouter import OpenRouterModel, OpenRouterModelSettings
from pydantic_ai.providers import Provider

BuiltModel = PydanticModel[Any]
NativeProvider = Provider[Any]

# Connection fields are Provider-owned, even on custom compatible endpoints.
_CONNECTION_FIELDS = ("api_key", "authorization", "credentials", "base_url", "endpoint", "headers", "extra_headers")
_CHAT_FIELDS = (
    "model",
    "messages",
    "tools",
    "tool_choice",
    "functions",
    "function_call",
    "stream",
    "stream_options",
    "response_format",
)
_RESPONSES_FIELDS = (
    "model",
    "input",
    "instructions",
    "tools",
    "tool_choice",
    "stream",
    "stream_options",
    "previous_response_id",
    "conversation",
)
_ANTHROPIC_FIELDS = ("model", "messages", "system", "tools", "tool_choice", "stream", "container", "output_format")


def _paths(*fields: str) -> tuple[tuple[str, ...], ...]:
    return tuple((name,) for name in (*_CONNECTION_FIELDS, *fields))


@dataclass(frozen=True, slots=True)
class ModelApiBinding:
    key: str
    model_type: Callable[..., BuiltModel]
    settings_type: Any
    protected_body_paths: tuple[tuple[str, ...], ...]
    supports_extra_body: bool = True

    def build(self, upstream_model: str, provider: NativeProvider) -> BuiltModel:
        return self.model_type(upstream_model, provider=provider)


def _index(bindings: Iterable[ModelApiBinding]) -> Mapping[str, ModelApiBinding]:
    indexed: dict[str, ModelApiBinding] = {}
    for binding in bindings:
        if binding.key in indexed:
            raise ValueError(f"duplicate model API {binding.key!r}")
        indexed[binding.key] = binding
    return MappingProxyType(indexed)


BUILT_IN_MODEL_APIS = _index(
    (
        ModelApiBinding(
            "openai.responses",
            OpenAIResponsesModel,
            OpenAIResponsesModelSettings,
            (*_paths(*_RESPONSES_FIELDS), ("text", "format")),
        ),
        ModelApiBinding("openai.chat_completions", OpenAIChatModel, OpenAIChatModelSettings, _paths(*_CHAT_FIELDS)),
        ModelApiBinding(
            "anthropic.messages",
            AnthropicModel,
            AnthropicModelSettings,
            (*_paths(*_ANTHROPIC_FIELDS), ("output_config", "format")),
        ),
        ModelApiBinding("google.generate_content", GoogleModel, GoogleModelSettings, (), supports_extra_body=False),
        ModelApiBinding(
            "bedrock.converse",
            BedrockConverseModel,
            BedrockModelSettings,
            _paths(*_ANTHROPIC_FIELDS, "modelId", "toolConfig", "outputConfig", "inferenceConfig"),
            supports_extra_body=False,
        ),
        ModelApiBinding(
            "bedrock_mantle.responses",
            BedrockMantleResponsesModel,
            OpenAIResponsesModelSettings,
            (*_paths(*_RESPONSES_FIELDS), ("text", "format")),
        ),
        ModelApiBinding(
            "bedrock_mantle.chat_completions", BedrockMantleChatModel, OpenAIChatModelSettings, _paths(*_CHAT_FIELDS)
        ),
        ModelApiBinding(
            "openrouter.chat_completions",
            OpenRouterModel,
            OpenRouterModelSettings,
            _paths(*_CHAT_FIELDS, "models", "preset", "transforms"),
        ),
        ModelApiBinding(
            "ollama.chat_completions", OllamaModel, OpenAIChatModelSettings, _paths(*_CHAT_FIELDS, "format")
        ),
    )
)
