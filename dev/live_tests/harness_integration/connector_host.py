"""Run the real Composio adapter against the lab-owned HTTPS peer."""

from contextlib import asynccontextmanager
from urllib.parse import urlsplit, urlunsplit

import httpx2
from a13n_harness.providers.catalog import ProviderCatalog
from a13n_harness.providers.connector.builtins import BUILT_IN_CONNECTOR_PROVIDERS
from a13n_harness.providers.connector.composio.configuration import COMPOSIO_ENDPOINT
from a13n_harness.providers.connector.http import ConnectorHttpClient
from a13n_harness.providers.endpoint_policy import EndpointPolicy

from ..infrastructure.fixture_peer import certificate_context


class PeerEndpoint:
    def __init__(self, origin):
        self.origin = origin
        self.policy = EndpointPolicy.from_operator_allowlist(private_cidrs=("127.0.0.1/32",), require_https=True)

    async def validate(self, endpoint, *, resolve_dns=True):
        """Accept only Composio destinations and route them to the lab peer, keeping the request path."""
        target = urlsplit(endpoint)
        if f"{target.scheme}://{target.netloc}" != COMPOSIO_ENDPOINT:
            raise ValueError("Live Connector adapter attempted an unexpected upstream")
        peer = urlsplit(self.origin)
        rewritten = urlunsplit((peer.scheme, peer.netloc, target.path, target.query, target.fragment))
        return await self.policy.validate(rewritten, resolve_dns=resolve_dns)


class ConnectorHost:
    def __init__(self, config, settings):
        self.http = httpx2.AsyncClient(verify=certificate_context(config), trust_env=False)
        self.catalog = ProviderCatalog(BUILT_IN_CONNECTOR_PROVIDERS)
        self.connector_http = ConnectorHttpClient(
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
