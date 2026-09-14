"""Run the real Composio adapter against the lab-owned HTTPS peer."""

from contextlib import asynccontextmanager

import httpx2
from a13n_service.connectivity.connectors.providers import built_in_connector_provider_registry
from a13n_service.connectivity.connectors.providers.composio.configuration import COMPOSIO_ENDPOINT
from a13n_service.endpoint_policy import EndpointPolicy

from ..infrastructure.fixture_peer import certificate_context


class PeerEndpoint:
    def __init__(self, origin):
        self.origin = origin
        self.policy = EndpointPolicy.from_operator_allowlist(private_cidrs=("127.0.0.1/32",), require_https=True)

    async def validate(self, endpoint, *, resolve_dns=True):
        if endpoint != COMPOSIO_ENDPOINT:
            raise ValueError("Live Connector adapter attempted an unexpected upstream")
        return await self.policy.validate(self.origin, resolve_dns=resolve_dns)


class ConnectorHost:
    def __init__(self, config, settings):
        self.http = httpx2.AsyncClient(verify=certificate_context(config), trust_env=False)
        self.registry = built_in_connector_provider_registry(
            self.http,
            PeerEndpoint(config["peer_url"]),
            response_max_bytes=settings.connectivity.response_max_bytes,
            timeout_seconds=settings.connectivity.total_timeout_seconds,
        )

    def install(self, app):
        service_lifespan = app.router.lifespan_context

        @asynccontextmanager
        async def lifespan(app):
            async with self.http, service_lifespan(app) as state:
                yield state

        app.router.lifespan_context = lifespan
