"""API-key native routes for embedding hosts, including SDK-only transports."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urlsplit

import httpx2
from anyio import move_on_after

from ..endpoint_policy import EndpointPolicy
from .builtins import BUILT_IN_MODEL_PROVIDERS
from .credentials import ApiKeyCredential

if TYPE_CHECKING:
    from pydantic_ai.models import Model
    from pydantic_ai.providers import Provider

# Native route names select a calling API, never a hosted account or Model identity.
_ROUTES = {
    "openai": ("openai", "openai.responses"),
    "openai-responses": ("openai", "openai.responses"),
    "openai-chat": ("openai", "openai.chat_completions"),
    "anthropic": ("anthropic", "anthropic.messages"),
    "google": ("google_gemini", "google.generate_content"),
    "openrouter": ("openrouter", "openrouter.chat_completions"),
    "deepseek": ("deepseek", "openai.chat_completions"),
    "zai": ("zhipu", "openai.chat_completions"),
    "moonshotai": ("moonshot", "openai.chat_completions"),
    "grok": ("openai", "openai.chat_completions"),
}
_OPENAI_CLIENT_ROUTES = frozenset({"together", "fireworks"})


async def build_api_key_model(route: str, credential: ApiKeyCredential, *, base_url: str | None = None) -> Model:
    """Build a native route with explicit credentials and host-selected endpoint access.

    The returned native Model owns its HTTP client. A custom URL is deliberately
    allowed to resolve to private addresses for local embedding applications.
    Service uses definitions with its deployment policy instead.
    """
    from pydantic_ai.models import infer_model
    from pydantic_ai.providers import Provider, infer_provider_class

    provider_name, separator, model_name = route.partition(":")
    if not separator:
        raise ValueError("an API-key Model route must include a provider")
    if provider_name in {"gemini", "google-gla", "google-vertex"}:
        provider_name = "google-cloud"
        route = f"{provider_name}:{model_name}"
    selected = _ROUTES.get(provider_name)
    if selected is not None:
        provider_type, api = selected
        definition = next(item for item in BUILT_IN_MODEL_PROVIDERS if item.type == provider_type)
        if provider_name == "grok" and base_url is None:
            base_url = "https://api.x.ai/v1"
        if base_url is None:
            base_url = {"zai": "https://api.z.ai/api/paas/v4", "moonshotai": "https://api.moonshot.ai/v1"}.get(
                provider_name
            )
        configuration = {"base_url": base_url} if base_url is not None else {}
        hostname = urlsplit(base_url).hostname if base_url else None
        policy = EndpointPolicy.from_operator_allowlist(private_domains=[hostname] if hostname else [])
        client = httpx2.AsyncClient()
        try:
            model = await definition.build(
                model_name,
                configuration=configuration,
                credential=credential,
                model_api=api,
                http_client=client,
                endpoint_policy=policy,
            )
            # Native Provider context management also supports later re-entry.
            assert model.provider is not None
            model.provider._own_http_client = client
            model.provider._http_client_factory = httpx2.AsyncClient
            if provider_name == "zai":
                from pydantic_ai.models.zai import ZaiModel

                return ZaiModel(model_name, provider=model.provider)
            return model
        except BaseException:
            with move_on_after(5, shield=True):
                await client.aclose()
            raise

    owned_client: httpx2.AsyncClient | None = None

    def provider_factory(requested: str) -> Provider[Any]:
        nonlocal owned_client
        if requested != provider_name:
            raise ValueError("the native Model requested a different provider")
        constructor = cast(Callable[..., Provider[Any]], infer_provider_class(requested))
        if base_url is not None and requested in _OPENAI_CLIENT_ROUTES:
            from openai import AsyncOpenAI

            client = owned_client = httpx2.AsyncClient()
            native = constructor(
                openai_client=AsyncOpenAI(
                    api_key=credential.api_key.get_secret_value(), base_url=base_url, http_client=client
                )
            )
            native._own_http_client = client
            native._http_client_factory = httpx2.AsyncClient
            return native
        options = {"api_key": credential.api_key.get_secret_value()}
        if base_url is not None:
            options["base_url"] = base_url
        return constructor(**options)

    try:
        return infer_model(route, provider_factory=provider_factory)
    except BaseException:
        if owned_client is not None:
            with move_on_after(5, shield=True):
                await owned_client.aclose()
        raise
