"""API-key native routes for embedding hosts, including SDK-only transports."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Any, cast

import httpx2
from anyio import move_on_after

from ...configuration import RunConfiguration
from ...models.configuration import configured_model
from ...models.inference import ROUTE_ALIASES as ROUTE_ALIASES
from ...models.transport import create_model_http_client
from ..endpoint_policy import EndpointPolicy
from .builtins import BUILT_IN_MODEL_PROVIDERS
from .credentials import ApiKeyCredential

if TYPE_CHECKING:
    from pydantic_ai.models import Model
    from pydantic_ai.providers import Provider


def _zai_model(model_name: str, model: Model) -> Model:
    from pydantic_ai.models.zai import ZaiModel

    assert model.provider is not None
    return ZaiModel(model_name, provider=model.provider)


@dataclass(frozen=True, slots=True)
class RouteSpec:
    """One native route: the Provider definition, its calling API and its own endpoint."""

    provider_type: str
    model_api: str
    default_base_url: str | None = None
    post_construct: Callable[[str, Model], Model] | None = None


# Native route names select a calling API, never a hosted account or Model identity.
ROUTES = {
    "openai-responses": RouteSpec("openai", "openai.responses"),
    "openai-chat": RouteSpec("openai", "openai.chat_completions"),
    "anthropic": RouteSpec("anthropic", "anthropic.messages"),
    "typesafe": RouteSpec("typesafe", "typesafe.system_one"),
    "google": RouteSpec("google_gemini", "google.generate_content"),
    "openrouter": RouteSpec("openrouter", "openrouter.chat_completions"),
    "deepseek": RouteSpec("deepseek", "openai.chat_completions"),
    "zai": RouteSpec("zhipu", "openai.chat_completions", "https://api.z.ai/api/paas/v4", _zai_model),
    "moonshotai": RouteSpec("moonshot", "openai.chat_completions", "https://api.moonshot.ai/v1"),
    "grok": RouteSpec("openai", "openai.chat_completions", "https://api.x.ai/v1"),
}
_OPENAI_CLIENT_ROUTES = frozenset({"together", "fireworks"})


async def build_api_key_model(
    route: str,
    credential: ApiKeyCredential,
    *,
    base_url: str | None = None,
    configuration: RunConfiguration | None = None,
) -> Model:
    """Build a native route with explicit credentials and host-selected endpoint access.

    The returned native Model owns its HTTP client, built with the shared model
    transport timeouts, retries and accepted Run hostname authorization.
    Service uses definitions with its own Host client instead.
    """
    configuration = configuration or RunConfiguration()
    provider_name, separator, model_name = route.partition(":")
    if not separator:
        raise ValueError("an API-key Model route must include a provider")
    provider_name = ROUTE_ALIASES.get(provider_name, provider_name)
    selected = ROUTES.get(provider_name)
    if selected is None:
        if configuration.allowed_hosts is not None:
            raise ValueError("This native Model route cannot enforce Run allowed hosts")
        return await build_inferred_route(f"{provider_name}:{model_name}", credential, base_url=base_url)
    return await _build_declared_route(
        selected, model_name, credential, base_url or selected.default_base_url, configuration
    )


async def _build_declared_route(
    route: RouteSpec,
    model_name: str,
    credential: ApiKeyCredential,
    base_url: str | None,
    configuration: RunConfiguration,
) -> Model:
    definition = next(item for item in BUILT_IN_MODEL_PROVIDERS if item.type == route.provider_type)
    policy = EndpointPolicy(configuration=configuration)
    client_factory = partial(create_model_http_client, configuration=configuration)
    client = client_factory()
    try:
        model = await definition.build(
            model_name,
            configuration={"base_url": base_url} if base_url is not None else {},
            credential=credential,
            model_api=route.model_api,
            http_client=client,
            endpoint_policy=policy,
        )
        # Native Provider context management also supports later re-entry.
        assert model.provider is not None
        model.provider._own_http_client = client
        model.provider._http_client_factory = client_factory
        if route.post_construct is not None:
            model = route.post_construct(model_name, model)
        return configured_model(model, configuration)
    except BaseException:
        with move_on_after(5, shield=True):
            await client.aclose()
        raise


async def build_inferred_route(route: str, credential: ApiKeyCredential, *, base_url: str | None = None) -> Model:
    """Build a route Pydantic AI owns, whose provider class constructs its own transport."""

    from pydantic_ai.models import infer_model
    from pydantic_ai.providers import Provider, infer_provider_class

    provider_name = route.partition(":")[0]
    owned_client: httpx2.AsyncClient | None = None

    def provider_factory(requested: str) -> Provider[Any]:
        nonlocal owned_client
        if requested != provider_name:
            raise ValueError("the native Model requested a different provider")
        constructor = cast(Callable[..., Provider[Any]], infer_provider_class(requested))
        if base_url is not None and requested in _OPENAI_CLIENT_ROUTES:
            from openai import AsyncOpenAI

            client = owned_client = create_model_http_client()
            native = constructor(
                openai_client=AsyncOpenAI(
                    api_key=credential.api_key.get_secret_value(), base_url=base_url, http_client=client
                )
            )
            native._own_http_client = client
            native._http_client_factory = create_model_http_client
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
