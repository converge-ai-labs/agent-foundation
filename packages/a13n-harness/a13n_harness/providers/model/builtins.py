"""Built-in Provider integration registry."""

from __future__ import annotations

from . import (
    alibaba_model_studio,
    anthropic,
    aws_bedrock,
    azure_openai,
    deepseek,
    fireworks,
    google_gemini,
    google_vertex,
    minimax,
    moonshot,
    ollama,
    openai,
    openai_chatgpt,
    openrouter,
    together,
    typesafe,
    zhipu,
)
from .definition import ModelProviderDefinition

BUILT_IN_MODEL_PROVIDERS: tuple[ModelProviderDefinition, ...] = (
    openai.DEFINITION,
    openai_chatgpt.DEFINITION,
    anthropic.DEFINITION,
    google_gemini.DEFINITION,
    google_vertex.DEFINITION,
    azure_openai.DEFINITION,
    aws_bedrock.DEFINITION,
    openrouter.DEFINITION,
    fireworks.DEFINITION,
    together.DEFINITION,
    ollama.DEFINITION,
    alibaba_model_studio.DEFINITION,
    deepseek.DEFINITION,
    moonshot.DEFINITION,
    minimax.DEFINITION,
    zhipu.DEFINITION,
    typesafe.DEFINITION,
)
