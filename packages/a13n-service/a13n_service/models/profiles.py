"""Resolve explicit catalog identities without a bundled model-name directory."""

from collections.abc import Callable
from typing import Any

from a13n_harness.providers.model.builtins import BUILT_IN_MODEL_PROVIDERS
from pydantic_ai.profiles import ModelProfile
from pydantic_ai.profiles.openai import openai_model_profile
from pydantic_ai.providers import Provider
from pydantic_ai.providers.alibaba import AlibabaProvider
from pydantic_ai.providers.deepseek import DeepSeekProvider
from pydantic_ai.providers.moonshotai import MoonshotAIProvider
from pydantic_ai.providers.zai import ZaiProvider

from .domain import CatalogRef

# Gateways that resolve another vendor's published model names in addition to their own.
_COMPATIBLE_CATALOGS: dict[str, tuple[str, ...]] = {"google_vertex": ("google",), "azure_openai": ("openai",)}

# Only equivalent directory channels are interchangeable for native name lookup.
PROVIDER_CATALOGS: dict[str, tuple[str, ...]] = {
    definition.type: (*definition.catalog_providers, *_COMPATIBLE_CATALOGS.get(definition.type, ()))
    for definition in BUILT_IN_MODEL_PROVIDERS
}

_COMPATIBLE_CHAT_PROFILES: dict[str, Callable[[str], ModelProfile | None]] = {
    "openai": openai_model_profile,
    "deepseek": DeepSeekProvider.model_profile,
    "moonshotai": MoonshotAIProvider.model_profile,
    "moonshotai-cn": MoonshotAIProvider.model_profile,
    "alibaba": AlibabaProvider.model_profile,
    "alibaba-cn": AlibabaProvider.model_profile,
    "zhipuai": ZaiProvider.model_profile,
    "zai": ZaiProvider.model_profile,
}


def catalog_profile(
    reference: CatalogRef | None,
    *,
    provider_type: str,
    model_api: str,
    native_provider: Provider[Any],
) -> ModelProfile | None:
    if reference is None:
        return None
    if reference.provider in PROVIDER_CATALOGS.get(provider_type, ()):
        return native_provider.model_profile(reference.model)
    # A generic OpenAI connection may reference a known compatible vendor. Do not
    # transplant these rules into native gateways such as OpenRouter or Bedrock.
    if provider_type == "openai" and model_api == "openai.chat_completions":
        resolver = _COMPATIBLE_CHAT_PROFILES.get(reference.provider)
        return resolver(reference.model) if resolver is not None else None
    return None
