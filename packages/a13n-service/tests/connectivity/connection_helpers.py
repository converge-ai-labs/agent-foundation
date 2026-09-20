"""Common resource management used by protocol-focused tests."""

from a13n_harness.providers.catalog import ProviderCatalog
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from a13n_service.connectivity.connections.checks import ConnectionChecks
from a13n_service.connectivity.connections.service import ConnectionService
from a13n_service.connectivity.connectors.connections import ConnectorConnectionService
from a13n_service.connectivity.mcp.service import MCPConnectionService


def management(protocol: ConnectorConnectionService | MCPConnectionService) -> ConnectionService:
    policy = protocol._endpoint_policy if isinstance(protocol, MCPConnectionService) else EndpointPolicy()
    return ConnectionService(protocol._sessions, policy, clock=protocol._clock)


def mcp_checks(protocol: MCPConnectionService) -> ConnectionChecks:
    return ConnectionChecks(
        protocol._sessions,
        ProviderCatalog(),
        None,
        protocol._protector,
        protocol,
        clock=protocol._clock,
    )


def authorizations(protocol, oauth):
    """Exercise credential retries at their public authorization owner."""
    from unittest.mock import Mock

    from a13n_service.connectivity.connections.authorization import AuthorizationService

    return AuthorizationService(
        protocol._sessions,
        protocol._protector,
        Mock(spec=ConnectorConnectionService),
        oauth,
        protocol,
        public_origin=None,
        callback_urls=(),
        clock=protocol._clock,
    )


async def replace_credentials(protocol, oauth, *, actor, connection_id, idempotency_key, request):
    from a13n_service.connectivity.connections.domain import CreateAuthorizationRequest

    credentials = {"bearer": request.bearer} if request.bearer is not None else request.static_headers
    await authorizations(protocol, oauth).create(
        actor=actor,
        connection_id=connection_id,
        idempotency_key=idempotency_key,
        request=CreateAuthorizationRequest(
            expected_version=request.expected_version, method="credentials", credentials=credentials
        ),
    )
    return await management(protocol).get(actor=actor, connection_id=connection_id)


async def authenticate_machine(protocol, oauth, *, actor, connection_id, expected_version, idempotency_key):
    from a13n_service.connectivity.connections.domain import CreateAuthorizationRequest

    await authorizations(protocol, oauth).create(
        actor=actor,
        connection_id=connection_id,
        idempotency_key=idempotency_key,
        request=CreateAuthorizationRequest(expected_version=expected_version, method="client_credentials"),
    )
    return await management(protocol).get(actor=actor, connection_id=connection_id)
