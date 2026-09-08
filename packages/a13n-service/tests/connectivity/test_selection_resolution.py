import pytest
from a13n_service.agents.domain import ConnectorConnectionToolSelection, MCPConnectionToolSelection
from a13n_service.connectivity.connectors.models import ConnectorConnectionRecord
from a13n_service.connectivity.selection_resolution import ConnectivitySelectionError, ConnectivitySelectionResolver
from a13n_service.storage import transaction

from .conftest import ORG_ID, WORKSPACE_ID, actor
from .selection_helpers import CONNECTOR_CONNECTION_ID, MCP_CONNECTION_ID, seed_selection_sources

pytestmark = pytest.mark.anyio


async def test_acceptance_retains_requested_scope_without_discovery(connectivity_sessions, connectivity_objects):
    await seed_selection_sources(connectivity_sessions)
    resolver = ConnectivitySelectionResolver(connectivity_sessions)
    prepared = await resolver.prepare(
        actor=actor(),
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        connector_tools=(
            ConnectorConnectionToolSelection(
                connector_connection_id=CONNECTOR_CONNECTION_ID, tools=("not-discovered",), defer_loading=True
            ),
        ),
        mcp_tools=(MCPConnectionToolSelection(mcp_connection_id=MCP_CONNECTION_ID),),
    )
    async with transaction(connectivity_sessions) as session:
        retained = await resolver.freeze(session, prepared=prepared)
    assert retained.connector_connection_selections[0].tools == ("not-discovered",)
    assert retained.connector_connection_selections[0].defer_loading
    assert retained.mcp_connection_selections[0].tools is None
    assert not hasattr(retained, "mcp_tool_snapshot")


async def test_acceptance_rechecks_resource_revocation(connectivity_sessions):
    await seed_selection_sources(connectivity_sessions)
    resolver = ConnectivitySelectionResolver(connectivity_sessions)
    prepared = await resolver.prepare(
        actor=actor(),
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        connector_tools=(ConnectorConnectionToolSelection(connector_connection_id=CONNECTOR_CONNECTION_ID),),
        mcp_tools=(),
    )
    async with transaction(connectivity_sessions) as session:
        source = await session.get(ConnectorConnectionRecord, CONNECTOR_CONNECTION_ID)
        source.status = "disabled"
    async with transaction(connectivity_sessions) as session:
        with pytest.raises(ConnectivitySelectionError, match="connector_connection_unavailable"):
            await resolver.freeze(session, prepared=prepared)


async def test_workspace_sources_reject_another_workspace(connectivity_sessions):
    await seed_selection_sources(connectivity_sessions)
    with pytest.raises(ConnectivitySelectionError):
        await ConnectivitySelectionResolver(connectivity_sessions).prepare(
            actor=actor(),
            organization_id=ORG_ID,
            workspace_id="ws_other",
            connector_tools=(ConnectorConnectionToolSelection(connector_connection_id=CONNECTOR_CONNECTION_ID),),
            mcp_tools=(),
        )


async def test_dispatch_checks_only_current_source_without_freezing_other_connections(connectivity_sessions):
    from a13n_service.connectivity.mcp.models import MCPConnectionRecord
    from sqlalchemy import event

    await seed_selection_sources(connectivity_sessions)
    resolver = ConnectivitySelectionResolver(connectivity_sessions)
    prepared = await resolver.prepare(
        actor=actor(),
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        connector_tools=(ConnectorConnectionToolSelection(connector_connection_id=CONNECTOR_CONNECTION_ID),),
        mcp_tools=(MCPConnectionToolSelection(mcp_connection_id=MCP_CONNECTION_ID),),
    )
    async with transaction(connectivity_sessions) as session:
        other = await session.get(MCPConnectionRecord, MCP_CONNECTION_ID)
        other.status = "disabled"
    statements = []
    engine = connectivity_sessions.kw["bind"].sync_engine

    def record(_connection, clause, *_args):
        statements.append(str(clause))

    event.listen(engine, "before_execute", record)
    try:
        async with transaction(connectivity_sessions) as session:
            await resolver.require_current_source(
                session,
                actor=actor(),
                organization_id=ORG_ID,
                workspace_id=WORKSPACE_ID,
                selection=prepared.selections.connector_connection_selections[0],
            )
    finally:
        event.remove(engine, "before_execute", record)
    assert not any("FOR UPDATE" in statement.upper() for statement in statements)
    assert not any("mcp_connections" in statement for statement in statements)
    assert sum("connector_connections" in statement for statement in statements) == 1
    async with transaction(connectivity_sessions) as session:
        with pytest.raises(ConnectivitySelectionError, match="mcp_connection_unavailable"):
            await resolver.require_current_source(
                session,
                actor=actor(),
                organization_id=ORG_ID,
                workspace_id=WORKSPACE_ID,
                selection=prepared.selections.mcp_connection_selections[0],
            )
