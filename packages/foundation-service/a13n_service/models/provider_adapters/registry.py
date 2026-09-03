"""Built-in Provider integration registry."""

from . import (
    alibaba_model_studio,
    anthropic,
    aws_bedrock,
    azure_openai,
    deepseek,
    google_gemini,
    google_vertex,
    moonshot,
    ollama,
    openai,
    openai_compatible,
    openrouter,
    zhipu,
)
from .base import ProviderIntegration

BUILT_IN_PROVIDER_INTEGRATIONS: tuple[ProviderIntegration, ...] = (
    openai.INTEGRATION,
    anthropic.INTEGRATION,
    google_gemini.INTEGRATION,
    google_vertex.INTEGRATION,
    azure_openai.INTEGRATION,
    aws_bedrock.INTEGRATION,
    openrouter.INTEGRATION,
    ollama.INTEGRATION,
    alibaba_model_studio.INTEGRATION,
    deepseek.INTEGRATION,
    moonshot.INTEGRATION,
    zhipu.INTEGRATION,
    openai_compatible.INTEGRATION,
)
