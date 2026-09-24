"""Native calling APIs; SDK modules load only when constructing or inspecting a Model."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pydantic_ai.models import Model
    from pydantic_ai.providers import Provider


@dataclass(frozen=True, slots=True)
class ModelApi:
    key: str
    display_name: str
    model_module: str
    model_name: str
    settings_module: str
    settings_name: str

    @property
    def settings_type(self) -> Any:
        return getattr(import_module(self.settings_module), self.settings_name)

    def build(self, model_name: str, provider: Provider[Any]) -> Model[Any]:
        constructor = getattr(import_module(self.model_module), self.model_name)
        return constructor(model_name, provider=provider)


MODEL_APIS = MappingProxyType(
    {
        "openai.responses": ModelApi(
            "openai.responses",
            "OpenAI Responses",
            "pydantic_ai.models.openai",
            "OpenAIResponsesModel",
            "pydantic_ai.models.openai",
            "OpenAIResponsesModelSettings",
        ),
        "openai.chat_completions": ModelApi(
            "openai.chat_completions",
            "OpenAI Chat Completions",
            "pydantic_ai.models.openai",
            "OpenAIChatModel",
            "pydantic_ai.models.openai",
            "OpenAIChatModelSettings",
        ),
        "anthropic.messages": ModelApi(
            "anthropic.messages",
            "Anthropic Messages",
            "pydantic_ai.models.anthropic",
            "AnthropicModel",
            "pydantic_ai.models.anthropic",
            "AnthropicModelSettings",
        ),
        "google.generate_content": ModelApi(
            "google.generate_content",
            "Google Generate Content",
            "pydantic_ai.models.google",
            "GoogleModel",
            "pydantic_ai.models.google",
            "GoogleModelSettings",
        ),
        "bedrock.converse": ModelApi(
            "bedrock.converse",
            "Amazon Bedrock Converse",
            "pydantic_ai.models.bedrock",
            "BedrockConverseModel",
            "pydantic_ai.models.bedrock",
            "BedrockModelSettings",
        ),
        "bedrock_mantle.responses": ModelApi(
            "bedrock_mantle.responses",
            "Amazon Bedrock Mantle Responses",
            "pydantic_ai.models.bedrock_mantle",
            "BedrockMantleResponsesModel",
            "pydantic_ai.models.openai",
            "OpenAIResponsesModelSettings",
        ),
        "bedrock_mantle.chat_completions": ModelApi(
            "bedrock_mantle.chat_completions",
            "Amazon Bedrock Mantle Chat Completions",
            "pydantic_ai.models.bedrock_mantle",
            "BedrockMantleChatModel",
            "pydantic_ai.models.openai",
            "OpenAIChatModelSettings",
        ),
        "openrouter.chat_completions": ModelApi(
            "openrouter.chat_completions",
            "OpenRouter Chat Completions",
            "pydantic_ai.models.openrouter",
            "OpenRouterModel",
            "pydantic_ai.models.openrouter",
            "OpenRouterModelSettings",
        ),
        "typesafe.system_one": ModelApi(
            "typesafe.system_one",
            "TypeSafe System One",
            "pydantic_ai.models.typesafe",
            "TypeSafeModel",
            "pydantic_ai.models.typesafe",
            "TypeSafeModelSettings",
        ),
        "ollama.chat_completions": ModelApi(
            "ollama.chat_completions",
            "Ollama Chat Completions",
            "pydantic_ai.models.ollama",
            "OllamaModel",
            "pydantic_ai.models.openai",
            "OpenAIChatModelSettings",
        ),
    }
)
