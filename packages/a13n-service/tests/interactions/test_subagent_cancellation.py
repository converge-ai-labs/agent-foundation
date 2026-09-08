from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from a13n_harness import SafeFailure
from a13n_service.interactions.domain import Run
from a13n_service.interactions.models import RunRecord
from a13n_service.interactions.objects import RunPayloadStore
from a13n_service.interactions.outcomes import RunOutcomeService
from a13n_service.interactions.scheduling import AttemptScheduler
from a13n_service.storage import ObjectStore, short_session
from a13n_service.subagents import (
    ChildCancellationPolicy,
    ChildCancellationReconciler,
    ChildRunAcceptanceService,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.lifecycle_support import test_lifecycle_writer

from .conftest import NOW, ORGANIZATION_ID, effective_agent_config
from .test_attempt_execution import _authority, _worker
from .test_subagent_acceptance import (
    _accept_parent,
    _grant_and_seed_child,
    _prepared_child,
)

pytestmark = pytest.mark.anyio


async def test_parent_cancellation_propagates_only_to_requested_child_threads(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
) -> None:
    parent, requested_child_id, independent_child_id, outcomes = await _cancel_parent_with_children(
        interaction_sessions,
        interaction_object_store,
    )
    reconciler = ChildCancellationReconciler(interaction_sessions, outcomes)

    first = await reconciler.reconcile_parent(
        organization_id=ORGANIZATION_ID,
        parent_run_id=parent.id,
        limit=1,
    )
    second = await reconciler.reconcile_parent(
        organization_id=ORGANIZATION_ID,
        parent_run_id=parent.id,
        after_child_thread_id=first.next_after_child_thread_id,
        limit=1,
    )
    replay = await reconciler.reconcile_parent(organization_id=ORGANIZATION_ID, parent_run_id=parent.id)

    assert (first.scanned, first.cancelled, first.conflicted) == (1, 1, 0)
    assert first.next_after_child_thread_id is not None
    assert (second.scanned, replay.scanned) == (0, 0)
    assert (await _run(interaction_sessions, requested_child_id)).status.value == "cancelled"
    assert (await _run(interaction_sessions, independent_child_id)).status.value == "running"


async def test_concurrent_parent_cancellation_reconciliation_is_idempotent_on_postgresql(
    postgres_interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
) -> None:
    sessions = postgres_interaction_sessions
    parent, requested_child_id, _, outcomes = await _cancel_parent_with_children(
        sessions,
        interaction_object_store,
    )
    reconciler = ChildCancellationReconciler(sessions, outcomes)

    batches = await asyncio.gather(
        reconciler.reconcile_parent(organization_id=ORGANIZATION_ID, parent_run_id=parent.id),
        reconciler.reconcile_parent(organization_id=ORGANIZATION_ID, parent_run_id=parent.id),
    )

    assert sum(batch.cancelled for batch in batches) == 1
    assert (await _run(sessions, requested_child_id)).status.value == "cancelled"


async def _cancel_parent_with_children(
    sessions: async_sessionmaker[AsyncSession],
    objects: ObjectStore,
) -> tuple[Run, str, str, RunOutcomeService]:
    await _grant_and_seed_child(sessions)
    states, parent, parent_state = await _accept_parent(sessions, objects)
    claim = await AttemptScheduler(
        sessions,
        clock=lambda: NOW + timedelta(seconds=1),
        token_factory=lambda: "parent-cancel-lease",
        attempt_id_factory=lambda: "rat_8989898989898989",
        lifecycle=test_lifecycle_writer(),
    ).claim(parent.id, _worker())
    assert claim is not None
    authority = _authority(claim)
    running_parent = await _run(sessions, parent.id)
    child_config = effective_agent_config()
    acceptance = ChildRunAcceptanceService(
        sessions,
        states,
        RunPayloadStore(objects),
        clock=lambda: NOW + timedelta(seconds=2),
        lifecycle=test_lifecycle_writer(),
    )
    requested = await acceptance.accept(
        _prepared_child(
            running_parent,
            parent_state,
            authority.run_attempt_id,
            authority.attempt_number,
            child_config,
            suffix="a",
            cancellation_policy=ChildCancellationPolicy.request_child_cancel,
        ),
        authority,
    )
    independent = await acceptance.accept(
        _prepared_child(
            running_parent,
            parent_state,
            authority.run_attempt_id,
            authority.attempt_number,
            child_config,
            suffix="b",
        ),
        authority,
    )
    requested_claim = await AttemptScheduler(
        sessions,
        clock=lambda: NOW + timedelta(seconds=3),
        token_factory=lambda: "requested-child-lease",
        attempt_id_factory=lambda: "rat_aaaaaaaaaaaaaaaa",
        lifecycle=test_lifecycle_writer(),
    ).claim(requested.child_run_id, _worker())
    independent_claim = await AttemptScheduler(
        sessions,
        clock=lambda: NOW + timedelta(seconds=3),
        token_factory=lambda: "independent-child-lease",
        attempt_id_factory=lambda: "rat_bbbbbbbbbbbbbbbb",
        lifecycle=test_lifecycle_writer(),
    ).claim(independent.child_run_id, _worker())
    assert requested_claim is not None and independent_claim is not None
    running_parent = await _run(sessions, parent.id)
    outcomes = RunOutcomeService(
        sessions, RunPayloadStore(objects), clock=lambda: NOW + timedelta(seconds=4), lifecycle=test_lifecycle_writer()
    )
    await outcomes.cancel(
        organization_id=ORGANIZATION_ID,
        run_id=parent.id,
        expected_run_version=running_parent.version,
        expected_thread_version=1,
        failure=SafeFailure(code="parent_cancelled", message="Parent cancelled."),
    )
    return running_parent, requested.child_run_id, independent.child_run_id, outcomes


async def _run(sessions: async_sessionmaker[AsyncSession], run_id: str) -> Run:
    async with short_session(sessions) as database:
        record = await database.get(RunRecord, run_id)
        assert record is not None
        return record.to_resource()
