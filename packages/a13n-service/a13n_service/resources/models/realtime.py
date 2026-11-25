"""Open a realtime adapter for the model selected by an Agent."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from a13n_harness.providers.endpoint_policy import EndpointPolicy, EndpointPolicyError
from a13n_harness.providers.model.credentials import ApiKeyCredential
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.providers.xai import XaiProvider
from pydantic_ai.realtime import RealtimeModel
from pydantic_ai.realtime.xai import XaiRealtimeModel

from a13n_service.infra.crypto import KeyRing
from a13n_service.infra.errors import invalid
from a13n_service.infra.outbound import open_http
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
    credential = model.provider.reveal_credential(keys)
    connection = definition.bind(model.provider.config, credential, extra_headers=model.provider.reveal_headers(keys))
    if (
        connection.endpoint is None
        or connection.endpoint.rstrip("/") != endpoint
        or connection.extra_headers
        or connection.configuration.session_affinity_header is not None
        or getattr(connection.configuration, "auth_mode", "bearer") != "bearer"
    ):
        raise invalid("model", f"{api} requires the official API endpoint with Bearer authentication")
    if not isinstance(connection.credential, ApiKeyCredential):
        raise invalid("model", f"{api} requires a provider API key")
    try:
        await policy.validate(endpoint)
    except EndpointPolicyError:
        raise invalid("model", f"The outbound policy denies the {api} endpoint") from None

    api_key = connection.credential.api_key.get_secret_value()
    model_name = model.config.model_name
    adapter = protocol.adapter
    if issubclass(adapter, XaiRealtimeModel):
        yield adapter(model_name, provider=XaiProvider(api_key=api_key))
        return

    async with open_http(policy, timeout=settings.model_timeout, max_bytes=settings.response_bytes) as client:
        provider = OpenAIProvider(api_key=api_key, base_url=endpoint, http_client=client)
        yield adapter(model_name, provider=provider)
