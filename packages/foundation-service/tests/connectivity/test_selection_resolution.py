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
    prepared = await resolver.prepare_invocation(
        actor=actor(),
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        run_id="run_1234567890abcdef",
        connector_tools=(
            ConnectorConnectionToolSelection(
                connector_connection_id=CONNECTOR_CONNECTION_ID, tools=("not-discovered",), defer_loading=True
            ),
        ),
        mcp_tools=(MCPConnectionToolSelection(mcp_connection_id=MCP_CONNECTION_ID),),
    )
    async with transaction(connectivity_sessions) as session:
        retained = await resolver.freeze_invocation(session, prepared=prepared)
    assert retained.connector_connection_selections[0].tools == ("not-discovered",)
    assert retained.connector_connection_selections[0].defer_loading
    assert retained.mcp_connection_selections[0].tools is None
    assert not hasattr(retained, "mcp_tool_snapshot")


async def test_acceptance_rechecks_resource_revocation(connectivity_sessions):
    await seed_selection_sources(connectivity_sessions)
    resolver = ConnectivitySelectionResolver(connectivity_sessions)
    prepared = await resolver.prepare_revision_creation(
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
            await resolver.freeze_revision_creation(session, prepared=prepared)


async def test_personal_source_cannot_be_selected_by_another_principal(connectivity_sessions):
    await seed_selection_sources(connectivity_sessions, connector_owner_id="usr_1111111111111111")
    with pytest.raises(ConnectivitySelectionError, match="connection_not_eligible"):
        await ConnectivitySelectionResolver(connectivity_sessions).prepare_revision_creation(
            actor=actor(),
            organization_id=ORG_ID,
            workspace_id=WORKSPACE_ID,
            connector_tools=(ConnectorConnectionToolSelection(connector_connection_id=CONNECTOR_CONNECTION_ID),),
            mcp_tools=(),
        )
