from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from a13n_service.agents.models import AgentRevisionRecord
from a13n_service.connectivity.connectors.models import ConnectorConnectionRecord, ConnectorToolCatalogRecord
from a13n_service.connectivity.ingress.admission import IngressEventService
from a13n_service.connectivity.ingress.admission_models import IngressAdmissionRecord, IngressBatchRecord
from a13n_service.connectivity.ingress.retention import IngressRetentionReconciler
from a13n_service.connectivity.ingress.service import IngressService
from a13n_service.connectivity.retention import CatalogRetentionReconciler
from a13n_service.storage import ObjectNotFound, transaction
from a13n_service.storage.object_store import LocalObjectStore
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .conftest import NOW
from .selection_helpers import CONNECTOR_CONNECTION_ID, seed_selection_sources
from .test_admission import _create_ingress, _request


@pytest.mark.anyio
async def test_catalog_cleanup_preserves_current_and_revision_referenced_catalogs_in_sqlite(
    connectivity_sessions: async_sessionmaker[AsyncSession],
    connectivity_objects: LocalObjectStore,
) -> None:
    await _assert_catalog_retention(connectivity_sessions, connectivity_objects)


@pytest.mark.anyio
async def test_catalog_cleanup_preserves_current_and_revision_referenced_catalogs_in_postgresql(
    postgres_connectivity_sessions: async_sessionmaker[AsyncSession],
    connectivity_objects: LocalObjectStore,
) -> None:
    await _assert_catalog_retention(postgres_connectivity_sessions, connectivity_objects)


async def _assert_catalog_retention(
    connectivity_sessions: async_sessionmaker[AsyncSession],
    connectivity_objects: LocalObjectStore,
) -> None:
    connector_digest, _mcp_digest = await seed_selection_sources(connectivity_sessions, connectivity_objects)
    async with transaction(connectivity_sessions) as session:
        catalog = await session.scalar(select(ConnectorToolCatalogRecord))
        assert catalog is not None
        catalog.retain_until = NOW - timedelta(seconds=1)
        revision = AgentRevisionRecord(
            id="agtr_retention_test",
            organization_id=catalog.organization_id,
            workspace_id=catalog.workspace_id,
            agent_id="agt_abcdef1234567890",
            version=1,
            plugin_runtime_mode="on_demand",
            config={},
            config_digest="a" * 64,
            resolved_model={},
            resolved_plugin_versions=[],
            runtime_lock_digest="b" * 64,
            resolved_skills=[],
            connector_tools=[{"connector_connection_id": CONNECTOR_CONNECTION_ID}],
            mcp_tools=[],
            resolved_environment=None,
            resolved_subagents=[],
            content_digest="c" * 64,
            source_revision_id=None,
            created_by_type="user",
            created_by_id="usr_abcdef1234567890",
            created_at=NOW,
        )
        session.add(revision)

    reconciler = CatalogRetentionReconciler(
        connectivity_sessions,
        connectivity_objects,
        instance_id="retention-test",
        clock=lambda: NOW,
    )
    assert await reconciler.reconcile_once() == 0

    async with transaction(connectivity_sessions) as session:
        connection = await session.get(ConnectorConnectionRecord, CONNECTOR_CONNECTION_ID)
        assert connection is not None
        connection.current_catalog_digest = None

    assert await reconciler.reconcile_once() == 0
    async with transaction(connectivity_sessions) as session:
        revision = await session.get(AgentRevisionRecord, "agtr_retention_test")
        assert revision is not None
        await session.delete(revision)

    assert await reconciler.reconcile_once() == 1
    async with connectivity_sessions() as session:
        assert await session.scalar(select(ConnectorToolCatalogRecord)) is None
    with pytest.raises(ObjectNotFound):
        await connectivity_objects.stat(f"catalogs/{CONNECTOR_CONNECTION_ID}/{connector_digest}.json")


@pytest.mark.anyio
async def test_postgresql_cleanup_replicas_claim_an_expired_catalog_once(
    postgres_connectivity_sessions: async_sessionmaker[AsyncSession],
    connectivity_objects: LocalObjectStore,
) -> None:
    await seed_selection_sources(postgres_connectivity_sessions, connectivity_objects)
    async with transaction(postgres_connectivity_sessions) as session:
        connection = await session.get(ConnectorConnectionRecord, CONNECTOR_CONNECTION_ID)
        catalog = await session.scalar(select(ConnectorToolCatalogRecord))
        assert connection is not None and catalog is not None
        connection.current_catalog_digest = None
        catalog.retain_until = NOW - timedelta(seconds=1)
    reconcilers = tuple(
        CatalogRetentionReconciler(
            postgres_connectivity_sessions,
            connectivity_objects,
            instance_id=f"retention-{index}",
            object_grace_seconds=24 * 3600,
            clock=lambda: NOW,
        )
        for index in range(2)
    )

    results = await asyncio.gather(*(reconciler.reconcile_once() for reconciler in reconcilers))

    assert sum(results) == 1
    async with postgres_connectivity_sessions() as session:
        assert await session.scalar(select(ConnectorToolCatalogRecord)) is None


@pytest.mark.anyio
async def test_orphan_snapshot_cleanup_observes_grace_and_uses_conditional_delete(
    connectivity_sessions: async_sessionmaker[AsyncSession],
    connectivity_objects: LocalObjectStore,
) -> None:
    key = (
        "tenants/org_abcdef1234567890/workspaces/ws_abcdef1234567890/"
        "runs/run_orphan1234567890/mcp-tool-snapshots/"
        f"{'d' * 64}.json"
    )
    await connectivity_objects.put(key, b"{}", content_type="application/json")
    reconciler = CatalogRetentionReconciler(
        connectivity_sessions,
        connectivity_objects,
        instance_id="retention-test",
        object_grace_seconds=60,
        clock=lambda: NOW + timedelta(days=1),
    )

    assert await reconciler.reconcile_once() == 1
    with pytest.raises(ObjectNotFound):
        await connectivity_objects.stat(key)


@pytest.mark.anyio
async def test_raw_evidence_and_terminal_identity_expire_independently(
    connectivity_sessions: async_sessionmaker[AsyncSession],
    connectivity_objects: LocalObjectStore,
    ingress_service: IngressService,
    ingress_event_service: IngressEventService,
) -> None:
    ingress_id = await _create_ingress(ingress_service)
    await ingress_event_service.receive(
        ingress_id=ingress_id,
        request=_request("retention-event", retain_raw=True),
    )
    async with transaction(connectivity_sessions) as session:
        admission = await session.scalar(select(IngressAdmissionRecord))
        batch = await session.scalar(select(IngressBatchRecord))
        assert admission is not None and batch is not None and admission.raw_ref_json is not None
        object_key = admission.raw_ref_json["object_key"]
        admission.raw_ref_json = {
            **admission.raw_ref_json,
            "expires_at": (NOW - timedelta(seconds=1)).isoformat(),
        }
        admission.status = "accepted"
        admission.result_kind = "run"
        admission.result_id = "run_retention123456"
        admission.dedup_expires_at = NOW - timedelta(seconds=1)
        admission.terminal_at = NOW - timedelta(seconds=1)
        batch.status = "accepted"
        batch.result_kind = "run"
        batch.result_id = "run_retention123456"
        batch.terminal_at = NOW - timedelta(seconds=1)

    reconciler = IngressRetentionReconciler(
        connectivity_sessions,
        connectivity_objects,
        clock=lambda: NOW,
    )
    assert await reconciler.reconcile_once() == 3
    async with connectivity_sessions() as session:
        assert await session.scalar(select(IngressAdmissionRecord)) is None
        assert await session.scalar(select(IngressBatchRecord)) is None
    with pytest.raises(ObjectNotFound):
        await connectivity_objects.stat(object_key)
