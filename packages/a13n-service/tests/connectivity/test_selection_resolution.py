import pytest
from a13n_service.agents.domain import ConnectionToolSelection
from a13n_service.connectivity.connectors.models import ConnectorConnectionRecord
from a13n_service.connectivity.naming import connection_model_aliases
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
        connection_tools=(
            *(
                ConnectionToolSelection(
                    connection_id=CONNECTOR_CONNECTION_ID, tools=("not-discovered",), defer_loading=True
                ),
            ),
            *(ConnectionToolSelection(connection_id=MCP_CONNECTION_ID),),
        ),
    )
    async with transaction(connectivity_sessions) as session:
        retained = await resolver.freeze(session, prepared=prepared)
    assert retained.connection_selections[0].tools == ("not-discovered",)
    assert retained.connection_selections[0].defer_loading
    assert retained.connection_selections[1].tools is None
    assert [selection.model_alias for selection in retained.connection_selections] == [
        "conn_orders_account",
        "conn_docs",
    ]
    assert not hasattr(retained, "mcp_tool_snapshot")


def test_connection_aliases_only_add_hash_for_collisions():
    aliases = connection_model_aliases((("first", "Henry's Notion"), ("second", "Henry s Notion"), ("third", "Linear")))
    assert aliases["third"] == "conn_linear"
    assert aliases["first"] != aliases["second"]
    assert all(
        alias.startswith("conn_henry_s_") and len(alias) <= 29 for alias in (aliases["first"], aliases["second"])
    )


async def test_accepted_alias_survives_connection_rename(connectivity_sessions):
    await seed_selection_sources(connectivity_sessions)
    resolver = ConnectivitySelectionResolver(connectivity_sessions)
    prepared = await resolver.prepare(
        actor=actor(),
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        connection_tools=(ConnectionToolSelection(connection_id=CONNECTOR_CONNECTION_ID),),
    )
    selection = prepared.selections.connection_selections[0]
    async with transaction(connectivity_sessions) as session:
        connection = await session.get(ConnectorConnectionRecord, CONNECTOR_CONNECTION_ID)
        connection.name = "Renamed account"
        connection.normalized_name = "renamed account"
    async with transaction(connectivity_sessions) as session:
        await resolver.require_current_source(
            session, actor=actor(), organization_id=ORG_ID, workspace_id=WORKSPACE_ID, selection=selection
        )
    assert selection.model_alias == "conn_orders_account"


async def test_acceptance_rechecks_resource_revocation(connectivity_sessions):
    await seed_selection_sources(connectivity_sessions)
    resolver = ConnectivitySelectionResolver(connectivity_sessions)
    prepared = await resolver.prepare(
        actor=actor(),
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        connection_tools=(ConnectionToolSelection(connection_id=CONNECTOR_CONNECTION_ID),),
    )
    async with transaction(connectivity_sessions) as session:
        source = await session.get(ConnectorConnectionRecord, CONNECTOR_CONNECTION_ID)
        source.status = "disabled"
    async with transaction(connectivity_sessions) as session:
        with pytest.raises(ConnectivitySelectionError, match="connection_unavailable"):
            await resolver.freeze(session, prepared=prepared)


async def test_workspace_sources_reject_another_workspace(connectivity_sessions):
    await seed_selection_sources(connectivity_sessions)
    with pytest.raises(ConnectivitySelectionError):
        await ConnectivitySelectionResolver(connectivity_sessions).prepare(
            actor=actor(),
            organization_id=ORG_ID,
            workspace_id="ws_other",
            connection_tools=(ConnectionToolSelection(connection_id=CONNECTOR_CONNECTION_ID),),
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
        connection_tools=(
            *(ConnectionToolSelection(connection_id=CONNECTOR_CONNECTION_ID),),
            *(ConnectionToolSelection(connection_id=MCP_CONNECTION_ID),),
        ),
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
                selection=prepared.selections.connection_selections[0],
            )
    finally:
        event.remove(engine, "before_execute", record)
    assert not any("FOR UPDATE" in statement.upper() for statement in statements)
    assert not any("mcp_connections" in statement for statement in statements)
    assert sum("FROM connections" in statement for statement in statements) == 1
    async with transaction(connectivity_sessions) as session:
        with pytest.raises(ConnectivitySelectionError, match="connection_unavailable"):
            await resolver.require_current_source(
                session,
                actor=actor(),
                organization_id=ORG_ID,
                workspace_id=WORKSPACE_ID,
                selection=prepared.selections.connection_selections[1],
            )


@pytest.mark.parametrize("connection_id", [CONNECTOR_CONNECTION_ID, MCP_CONNECTION_ID])
async def test_reauthorization_fences_accepted_runs_but_credential_refresh_does_not(
    connectivity_sessions,
    connection_id,
):
    from a13n_service.connectivity.connections.models import ConnectionRecord

    await seed_selection_sources(connectivity_sessions)
    resolver = ConnectivitySelectionResolver(connectivity_sessions)
    prepared = await resolver.prepare(
        actor=actor(),
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        connection_tools=(ConnectionToolSelection(connection_id=connection_id),),
    )
    async with transaction(connectivity_sessions) as session:
        accepted = await resolver.freeze(session, prepared=prepared)
    selection = accepted.connection_selections[0]
    async with transaction(connectivity_sessions) as session:
        connection = await session.get(ConnectionRecord, connection_id)
        connection.credential_generation += 1
    async with transaction(connectivity_sessions) as session:
        await resolver.require_current_source(
            session, actor=actor(), organization_id=ORG_ID, workspace_id=WORKSPACE_ID, selection=selection
        )
    async with transaction(connectivity_sessions) as session:
        connection = await session.get(ConnectionRecord, connection_id)
        connection.authorization_generation += 1
    async with transaction(connectivity_sessions) as session:
        with pytest.raises(ConnectivitySelectionError, match="connection_changed"):
            await resolver.require_current_source(
                session, actor=actor(), organization_id=ORG_ID, workspace_id=WORKSPACE_ID, selection=selection
            )
        with pytest.raises(ConnectivitySelectionError, match="connection_changed"):
            await resolver.freeze(session, prepared=prepared)
    replacement = await resolver.prepare(
        actor=actor(),
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        connection_tools=(ConnectionToolSelection(connection_id=connection_id),),
    )
    assert replacement.selections.connection_selections[0].connection_id == selection.connection_id
    assert (
        replacement.selections.connection_selections[0].authorization_generation
        == selection.authorization_generation + 1
    )
