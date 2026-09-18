"""Common resource management used by protocol-focused tests."""

from a13n_harness.providers.endpoint_policy import EndpointPolicy
from a13n_service.connectivity.connections.checks import ConnectionChecks
from a13n_service.connectivity.connections.service import ConnectionService
from a13n_service.connectivity.connectors.composition import ConnectorProviders
from a13n_service.connectivity.connectors.connections import ConnectorConnectionService
from a13n_service.connectivity.mcp.service import MCPConnectionService


def management(protocol: ConnectorConnectionService | MCPConnectionService) -> ConnectionService:
    policy = protocol._endpoint_policy if isinstance(protocol, MCPConnectionService) else EndpointPolicy()
    return ConnectionService(protocol._sessions, policy, clock=protocol._clock)


def mcp_checks(protocol: MCPConnectionService) -> ConnectionChecks:
    return ConnectionChecks(
        protocol._sessions, ConnectorProviders(), protocol._protector, protocol, clock=protocol._clock
    )
