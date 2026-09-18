"""One transport and OAuth refresh policy for Control and Worker connectivity."""

from dataclasses import dataclass

import httpx2
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.connectivity.mcp.oauth_client import MCPOAuthClient
from a13n_service.connectivity.mcp.refresh import OAuthCredentialRefresh
from a13n_service.connectivity.mcp.transport import RemoteTransport
from a13n_service.ids import new_object_id
from a13n_service.secrets import SecretProtector
from a13n_service.settings import Settings


def connectivity_http_timeout(settings: Settings) -> httpx2.Timeout:
    return httpx2.Timeout(
        settings.connectivity.total_timeout_seconds,
        connect=settings.connectivity.connect_timeout_seconds,
        read=settings.connectivity.read_timeout_seconds,
    )


@dataclass(frozen=True, slots=True)
class MCPClients:
    oauth: MCPOAuthClient
    transport: RemoteTransport
    credentials: OAuthCredentialRefresh


def build_mcp_clients(
    settings: Settings,
    sessions: async_sessionmaker[AsyncSession],
    protector: SecretProtector,
    http: httpx2.AsyncClient,
    endpoints: EndpointPolicy,
) -> MCPClients:
    oauth = MCPOAuthClient(
        http,
        endpoints,
        response_max_bytes=settings.connectivity.response_max_bytes,
        max_redirects=settings.connectivity.max_redirects,
    )
    return MCPClients(
        oauth,
        RemoteTransport(
            endpoints,
            timeout_seconds=settings.connectivity.total_timeout_seconds,
            http_timeout=connectivity_http_timeout(settings),
        ),
        OAuthCredentialRefresh(
            sessions,
            oauth,
            protector,
            instance_id=settings.service.instance_id or new_object_id("svc"),
            lease_seconds=settings.connectivity.connector_reconcile_lease_seconds,
            skew_seconds=settings.connectivity.provider_token_expiry_skew_seconds,
        ),
    )
