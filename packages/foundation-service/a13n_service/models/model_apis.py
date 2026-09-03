"""Finite calling-API bindings to native Pydantic AI Models."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, cast

from a13n_harness.errors import ModelResolutionError
from pydantic_ai.models import Model as PydanticModel
from pydantic_ai.models import infer_model
from pydantic_ai.models.bedrock_mantle import BedrockMantleChatModel, BedrockMantleResponsesModel
from pydantic_ai.providers import Provider
from pydantic_ai.providers.bedrock_mantle import BedrockMantleProvider

BuiltModel = PydanticModel[Any]
NativeProvider = Provider[Any]
ExplicitModelBuilder = Callable[[str, NativeProvider], BuiltModel]


@dataclass(frozen=True, slots=True)
class ModelApiBinding:
    key: str
    pydantic_provider_name: str
    explicit_builder: ExplicitModelBuilder | None = None

    def build(self, upstream_model: str, provider: NativeProvider) -> BuiltModel:
        if self.explicit_builder is not None:
            return self.explicit_builder(upstream_model, provider)
        return infer_model(
            f"{self.pydantic_provider_name}:{upstream_model}",
            provider_factory=lambda _: provider,
        )


def _bedrock_mantle_responses(upstream_model: str, provider: NativeProvider) -> BuiltModel:
    return BedrockMantleResponsesModel(cast(Any, upstream_model), provider=_require_bedrock_mantle(provider))


def _bedrock_mantle_chat(upstream_model: str, provider: NativeProvider) -> BuiltModel:
    return BedrockMantleChatModel(cast(Any, upstream_model), provider=_require_bedrock_mantle(provider))


def _require_bedrock_mantle(provider: NativeProvider) -> BedrockMantleProvider:
    if not isinstance(provider, BedrockMantleProvider):
        raise ModelResolutionError(
            "The accepted Model API is unavailable.",
            code="model_api_unavailable",
        )
    return provider


def _index(bindings: Iterable[ModelApiBinding]) -> Mapping[str, ModelApiBinding]:
    indexed: dict[str, ModelApiBinding] = {}
    for binding in bindings:
        if binding.key in indexed:
            raise ValueError(f"duplicate model API {binding.key!r}")
        indexed[binding.key] = binding
    return MappingProxyType(indexed)


BUILT_IN_MODEL_APIS = _index(
    (
        ModelApiBinding("openai.responses", "openai-responses"),
        ModelApiBinding("openai.chat_completions", "openai-chat"),
        ModelApiBinding("anthropic.messages", "anthropic"),
        ModelApiBinding("google.generate_content", "google"),
        ModelApiBinding("bedrock.converse", "bedrock"),
        ModelApiBinding(
            "bedrock_mantle.responses",
            "bedrock-mantle",
            explicit_builder=_bedrock_mantle_responses,
        ),
        ModelApiBinding(
            "bedrock_mantle.chat_completions",
            "bedrock-mantle",
            explicit_builder=_bedrock_mantle_chat,
        ),
        ModelApiBinding("openrouter.chat_completions", "openrouter"),
        ModelApiBinding("ollama.chat_completions", "ollama"),
    )
)
