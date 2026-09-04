"""Accepted connection scope, independent of live tool definitions."""

from a13n_service.agents.domain import ConnectorConnectionToolSelection, MCPConnectionToolSelection, ObjectId


class ConnectorConnectionRunSelection(ConnectorConnectionToolSelection):
    connector_provider_id: ObjectId


MCPConnectionRunSelection = MCPConnectionToolSelection
