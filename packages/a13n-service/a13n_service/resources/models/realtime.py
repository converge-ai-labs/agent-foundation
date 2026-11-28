"""Open a realtime adapter for the model selected by an Agent."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from a13n_harness.providers.endpoint_policy import EndpointPolicy, EndpointPolicyError
from a13n_harness.providers.model.credentials import ApiKeyCredential
from anyio import to_thread
from pydantic_ai.providers.google import GoogleProvider
from pydantic_ai.providers.xai import XaiProvider
from pydantic_ai.realtime import RealtimeModel
from pydantic_ai.realtime.azure import AzureRealtimeModel, AzureRealtimeModelProfile
from pydantic_ai.realtime.google import GoogleRealtimeModel
from pydantic_ai.realtime.xai import XaiRealtimeModel

from a13n_service.infra.crypto import KeyRing
from a13n_service.infra.errors import invalid
from a13n_service.infra.outbound import open_http, validate_ws_endpoint
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
        await validate_ws_endpoint(policy, websocket)
    except EndpointPolicyError:
        raise invalid("model", f"The outbound policy denies the {api} endpoint") from None

    model_name = model.config.model_name
    adapter = protocol.adapter
    if issubclass(adapter, XaiRealtimeModel):
        # The Harness xAI builder targets Chat Completions, not the native Grok Voice adapter.
        yield adapter(model_name, provider=XaiProvider(api_key=connection.credential.api_key.get_secret_value()))
        return

    build_provider = definition.build_provider
    assert build_provider is not None
    async with open_http(policy, timeout=settings.model_timeout, max_bytes=settings.response_bytes) as client:
        provider = await to_thread.run_sync(build_provider, connection, client, api)
        if issubclass(adapter, AzureRealtimeModel):
            # This API selects Azure OpenAI GA, not the independently configured Azure AI Voice Live API.
            yield adapter(
                model_name,
                provider=provider,
                profile=AzureRealtimeModelProfile(azure_realtime_apis=frozenset(("azure_openai",))),
            )
        elif issubclass(adapter, GoogleRealtimeModel):
            assert isinstance(provider, GoogleProvider)
            async with provider.client.aio:
                yield adapter(model_name, provider=provider)
        else:
            yield adapter(model_name, provider=provider)
