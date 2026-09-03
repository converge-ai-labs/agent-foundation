from __future__ import annotations

import pytest
from a13n_service.agents.domain import (
    ConnectorConnectionToolSelection,
    MCPConnectionToolSelection,
)
from a13n_service.connectivity.connectors.models import ConnectorConnectionRecord
from a13n_service.connectivity.selection_resolution import ConnectivitySelectionError, ConnectivitySelectionResolver
from a13n_service.storage import transaction
from a13n_service.storage.object_store import LocalObjectStore
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .conftest import ORG_ID, WORKSPACE_ID, actor
from .selection_helpers import CONNECTOR_CONNECTION_ID, MCP_CONNECTION_ID, seed_selection_sources

pytestmark = pytest.mark.anyio


def connector_selection(*, tools: tuple[str, ...] | None = None) -> ConnectorConnectionToolSelection:
    return ConnectorConnectionToolSelection(
        connector_connection_id=CONNECTOR_CONNECTION_ID,
        tools=tools,
        exposure="direct",
    )


def mcp_selection(*, tools: tuple[str, ...] | None = None) -> MCPConnectionToolSelection:
    return MCPConnectionToolSelection(
        mcp_connection_id=MCP_CONNECTION_ID,
        tools=tools,
        exposure="catalog",
    )


async def resolver_with_sources(
    sessions: async_sessionmaker[AsyncSession],
    objects: LocalObjectStore,
) -> ConnectivitySelectionResolver:
    await seed_selection_sources(sessions, objects)
    return ConnectivitySelectionResolver(sessions, objects)


async def test_invocation_resolves_exact_selections_and_deterministic_snapshot(
    connectivity_sessions: async_sessionmaker[AsyncSession],
    connectivity_objects: LocalObjectStore,
) -> None:
    resolver = await resolver_with_sources(connectivity_sessions, connectivity_objects)
    requested = {
        "connector_tools": {"orders": connector_selection()},
        "mcp_tools": {"docs": mcp_selection(tools=())},
    }

    first = await resolver.prepare_invocation(
        actor=actor(),
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        run_id="run_1234567890abcdef",
        **requested,
    )
    repeated = await resolver.prepare_invocation(
        actor=actor(),
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        run_id="run_1234567890abcdef",
        **requested,
    )
    another_run = await resolver.prepare_invocation(
        actor=actor(),
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        run_id="run_fedcba0987654321",
        **requested,
    )

    assert first.frozen.connector_connection_selections[0].allowed_tool_keys == (
        "create_order",
        "find_order",
    )
    assert first.frozen.mcp_connection_selections[0].allowed_tool_keys == ()
    assert len(first.frozen.mcp_tool_snapshot.snapshot.direct_tools) == 2
    assert first.frozen.mcp_tool_snapshot.snapshot.catalog_tools == ()
    assert first.frozen.mcp_tool_snapshot.reference == repeated.frozen.mcp_tool_snapshot.reference
    assert first.frozen.mcp_tool_snapshot.object_key == repeated.frozen.mcp_tool_snapshot.object_key
    assert first.frozen.mcp_tool_snapshot.reference != another_run.frozen.mcp_tool_snapshot.reference


async def test_empty_snapshot_is_canonical_across_runs(
    connectivity_sessions: async_sessionmaker[AsyncSession],
    connectivity_objects: LocalObjectStore,
) -> None:
    resolver = ConnectivitySelectionResolver(connectivity_sessions, connectivity_objects)

    first = await resolver.prepare_invocation(
        actor=actor(),
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        run_id="run_1234567890abcdef",
        connector_tools={},
        mcp_tools={},
    )
    second = await resolver.prepare_invocation(
        actor=actor(),
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        run_id="run_fedcba0987654321",
        connector_tools={},
        mcp_tools={},
    )

    assert first.frozen.mcp_tool_snapshot.reference == second.frozen.mcp_tool_snapshot.reference
    assert first.frozen.mcp_tool_snapshot.object_key != second.frozen.mcp_tool_snapshot.object_key
    assert first.frozen.mcp_tool_snapshot.snapshot.direct_tools == ()
    assert first.frozen.mcp_tool_snapshot.snapshot.catalog_tools == ()


async def test_prepare_rejects_unknown_tools_and_duplicate_sources(
    connectivity_sessions: async_sessionmaker[AsyncSession],
    connectivity_objects: LocalObjectStore,
) -> None:
    resolver = await resolver_with_sources(connectivity_sessions, connectivity_objects)

    with pytest.raises(ConnectivitySelectionError, match="tool_not_found") as missing:
        await resolver.prepare_revision_creation(
            actor=actor(),
            organization_id=ORG_ID,
            workspace_id=WORKSPACE_ID,
            connector_tools={"orders": connector_selection(tools=("missing",))},
            mcp_tools={},
        )
    assert missing.value.path == "connector_tools.orders"

    with pytest.raises(ConnectivitySelectionError, match="connection_selected_more_than_once"):
        await resolver.prepare_revision_creation(
            actor=actor(),
            organization_id=ORG_ID,
            workspace_id=WORKSPACE_ID,
            connector_tools={"orders": connector_selection(), "again": connector_selection()},
            mcp_tools={},
        )


async def test_personal_source_requires_exact_owner_even_for_admin(
    connectivity_sessions: async_sessionmaker[AsyncSession],
    connectivity_objects: LocalObjectStore,
) -> None:
    await seed_selection_sources(
        connectivity_sessions,
        connectivity_objects,
        connector_owner_id="usr_someoneelse1234",
    )
    resolver = ConnectivitySelectionResolver(connectivity_sessions, connectivity_objects)

    with pytest.raises(ConnectivitySelectionError, match="connection_not_eligible"):
        await resolver.prepare_revision_creation(
            actor=actor(),
            organization_id=ORG_ID,
            workspace_id=WORKSPACE_ID,
            connector_tools={"orders": connector_selection()},
            mcp_tools={},
        )


async def test_freeze_rejects_catalog_pointer_race(
    connectivity_sessions: async_sessionmaker[AsyncSession],
    connectivity_objects: LocalObjectStore,
) -> None:
    resolver = await resolver_with_sources(connectivity_sessions, connectivity_objects)
    prepared = await resolver.prepare_invocation(
        actor=actor(),
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        run_id="run_1234567890abcdef",
        connector_tools={"orders": connector_selection()},
        mcp_tools={},
    )
    async with transaction(connectivity_sessions) as session:
        connection = await session.get(ConnectorConnectionRecord, CONNECTOR_CONNECTION_ID)
        assert connection is not None
        connection.current_catalog_digest = None

    with pytest.raises(ConnectivitySelectionError, match="connector_connection_changed"):
        async with transaction(connectivity_sessions) as session:
            await resolver.freeze_invocation(session, prepared=prepared)
