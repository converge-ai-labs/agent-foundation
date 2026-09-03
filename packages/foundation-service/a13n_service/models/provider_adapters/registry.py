"""Built-in Provider adapter registry."""

from collections.abc import Mapping
from types import MappingProxyType

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
from .base import ProviderAdapter
from .types import ProviderType

BUILT_IN_PROVIDER_TYPES: tuple[ProviderType, ...] = (
    openai.TYPE,
    anthropic.TYPE,
    google_gemini.TYPE,
    google_vertex.TYPE,
    azure_openai.TYPE,
    aws_bedrock.TYPE,
    openrouter.TYPE,
    ollama.TYPE,
    alibaba_model_studio.TYPE,
    deepseek.TYPE,
    moonshot.TYPE,
    zhipu.TYPE,
    openai_compatible.TYPE,
)

BUILT_IN_PROVIDER_ADAPTERS: Mapping[str, ProviderAdapter] = MappingProxyType(
    {
        openai.TYPE.key: openai.ADAPTER,
        anthropic.TYPE.key: anthropic.ADAPTER,
        google_gemini.TYPE.key: google_gemini.ADAPTER,
        google_vertex.TYPE.key: google_vertex.ADAPTER,
        azure_openai.TYPE.key: azure_openai.ADAPTER,
        aws_bedrock.TYPE.key: aws_bedrock.ADAPTER,
        openrouter.TYPE.key: openrouter.ADAPTER,
        ollama.TYPE.key: ollama.ADAPTER,
        alibaba_model_studio.TYPE.key: alibaba_model_studio.ADAPTER,
        deepseek.TYPE.key: deepseek.ADAPTER,
        moonshot.TYPE.key: moonshot.ADAPTER,
        zhipu.TYPE.key: zhipu.ADAPTER,
        openai_compatible.TYPE.key: openai_compatible.ADAPTER,
    }
)
