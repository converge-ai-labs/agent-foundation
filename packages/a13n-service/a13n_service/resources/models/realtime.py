"""Open a realtime adapter for the model selected by an Agent."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast
from urllib.parse import urlsplit

from a13n_harness.providers.endpoint_policy import EndpointPolicy, EndpointPolicyError
from a13n_harness.providers.model.credentials import ApiKeyCredential
from google.genai import Client
from google.genai.types import HttpOptions
from openai import AsyncAzureOpenAI, AsyncOpenAI
from pydantic_ai.providers.azure import AzureProvider
from pydantic_ai.providers.google import GoogleProvider
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.providers.xai import XaiProvider
from pydantic_ai.realtime import RealtimeModel
from pydantic_ai.realtime.azure import AzureRealtimeModel, AzureRealtimeModelProfile
from pydantic_ai.realtime.google import GoogleRealtimeModel
from pydantic_ai.realtime.xai import XaiRealtimeModel

from a13n_service.infra.crypto import KeyRing
from a13n_service.infra.errors import invalid
from a13n_service.infra.outbound import open_http, validate_cdp_endpoint
from a13n_service.providers.realtime import PROTOCOLS
from a13n_service.providers.registry import Registry
from a13n_service.resources.models.service import ResolvedModel
from a13n_service.settings import Providers


@asynccontextmanager
async def open_realtime_model(
    model: ResolvedModel, *, registry: Registry, keys: KeyRing, policy: EndpointPolicy, settings: Providers
) -> AsyncIterator[RealtimeModel]:
    """Resolve the adapter from the selected Model's API and use its Provider credential."""
    api = model.config.model_api
    protocol = PROTOCOLS.get(api)
    if protocol is None:
        raise invalid("model", "The selected model does not use a realtime API")
    provider_type, endpoint = protocol.provider, protocol.endpoint
    if model.provider.type != provider_type:
        raise invalid("model", f"{api} requires a {provider_type} provider")
    definition = registry.get("model", provider_type)
    if model.provider.extra_headers:
        raise invalid("model", f"{api} requires the official API endpoint without custom headers")
    connection = definition.bind(model.provider.config, model.provider.reveal_credential(keys))
    configured = connection.endpoint.rstrip("/") if connection.endpoint else ""
    if api == "azure.realtime" and connection.configuration.base_url is None and configured:
        parsed = urlsplit(configured)
        if (parsed.hostname or "").endswith(".openai.azure.com") and parsed.path == "/openai/v1":
            endpoint = configured
    if (
        endpoint is None
        or configured != endpoint
        or (api == "google.realtime" and connection.configuration.base_url is not None)
        or connection.configuration.session_affinity_header is not None
        or (api.startswith("openai.") and model.provider.config.get("auth_mode", "bearer") != "bearer")
    ):
        raise invalid("model", f"{api} requires the official API endpoint without custom headers")
    if not isinstance(connection.credential, ApiKeyCredential):
        raise invalid("model", f"{api} requires a provider API key")
    try:
        await policy.validate(endpoint)
        destination = urlsplit(endpoint)
        websocket = destination._replace(scheme="wss", path=destination.path + protocol.websocket_path).geturl()
        await validate_cdp_endpoint(policy, websocket)
    except EndpointPolicyError:
        raise invalid("model", f"The outbound policy denies the {api} endpoint") from None

    api_key = connection.credential.api_key.get_secret_value()
    model_name = model.config.model_name
    adapter = protocol.adapter
    if issubclass(adapter, XaiRealtimeModel):
        yield adapter(model_name, provider=XaiProvider(api_key=api_key))
        return

    async with open_http(policy, timeout=settings.model_timeout, max_bytes=settings.response_bytes) as client:
        if issubclass(adapter, AzureRealtimeModel):
            sdk = AsyncOpenAI(api_key=api_key, base_url=endpoint, http_client=client, max_retries=0)
            azure = AzureProvider(openai_client=cast(AsyncAzureOpenAI, sdk))
            # This API selects Azure OpenAI GA, not the independently configured Azure AI Voice Live API.
            yield adapter(
                model_name,
                provider=azure,
                profile=AzureRealtimeModelProfile(azure_realtime_apis=frozenset(("azure_openai",))),
            )
        elif issubclass(adapter, GoogleRealtimeModel):
            sdk = Client(
                vertexai=False,
                api_key=api_key,
                http_options=HttpOptions(base_url=endpoint, api_version="v1beta", httpx_async_client=client),
            )
            async with sdk.aio:
                yield adapter(model_name, provider=GoogleProvider(client=sdk))
        else:
            provider = OpenAIProvider(api_key=api_key, base_url=endpoint, http_client=client)
            yield adapter(model_name, provider=provider)
