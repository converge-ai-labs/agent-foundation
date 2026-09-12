"""Capacity admission is atomic with the durable Run's first Environment use."""

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from a13n_environment import EnvironmentError
from a13n_service.environments.capacity import CapacityLimits
from a13n_service.environments.domain import NewEnvironmentSelection
from a13n_service.environments.models import EnvironmentRecord
from a13n_service.environments.runtime import prepare_run_environment
from a13n_service.interactions.models import RunRecord, SessionRecord, ThreadRecord
from a13n_service.interactions.records import run_record, thread_record
from a13n_service.interactions.scheduling import AttemptScheduler
from a13n_service.storage import short_session, transaction

from tests.hooks.support import hook_actor
from tests.lifecycle_support import test_lifecycle_writer

from .conftest import NOW, WORKSPACE_ID
from .test_acceptance import _accepted_run
from .test_attempt_execution import _accept_root, _authority, _worker
from .test_environment_runtime import recipe
from .worker_helpers import prepare_permissions

pytestmark = pytest.mark.anyio


async def sibling_run(sessions, run, environment_id):
    async with transaction(sessions) as session:
        thread = (await session.get(ThreadRecord, run.thread_id)).to_resource()
        sibling_session = SessionRecord(
            id="session_capacity12345678",
            organization_id=run.organization_id,
            workspace_id=WORKSPACE_ID,
            created_at=NOW,
            updated_at=NOW,
        )
        session.add(sibling_session)
        await session.flush()
        sibling_thread = thread.model_copy(
            update={
                "id": "thread_capacity12345678",
                "session_id": sibling_session.id,
                "current_run_id": None,
                "head_run_id": None,
            }
        )
        session.add(thread_record(sibling_thread))
        await session.flush()
        sibling = _accepted_run(
            run_id="run_capacity123456789012",
            thread_id=sibling_thread.id,
            idempotency_key="capacity",
            request_fingerprint="a" * 64,
        ).model_copy(
            update={"session_id": sibling_session.id, "environment_id": environment_id, "environment_access": "full"}
        )
        session.add(run_record(sibling))
        await session.flush()
        (await session.get(ThreadRecord, sibling_thread.id)).current_run_id = sibling.id
        return sibling


@pytest.mark.parametrize("kind", ["targets", "active"])
async def test_postgresql_last_slot_admits_only_one_concurrent_run(
    postgres_interaction_sessions, interaction_object_store, tmp_path, kind
):
    sessions = postgres_interaction_sessions
    service, template, lifecycle = await recipe(sessions, tmp_path, "on_use")
    lifecycle.capacity = CapacityLimits(
        max_targets=1 if kind == "targets" else 10, max_active=1 if kind == "active" else 10
    )
    _, first, _ = await _accept_root(sessions, interaction_object_store)
    other = await service.create_environment(
        actor=hook_actor(),
        workspace_id=WORKSPACE_ID,
        request=NewEnvironmentSelection(template_id=template.id),
        idempotency_key="second-target",
    )
    second = await sibling_run(sessions, first, other.id)
    scheduler = AttemptScheduler(sessions, clock=lambda: NOW + timedelta(seconds=1), lifecycle=test_lifecycle_writer())
    claims = [await scheduler.claim(run.id, _worker()) for run in (first, second)]
    environments = [
        await prepare_run_environment(lifecycle, await prepare_permissions(sessions, run, _authority(claim)))
        for run, claim in zip((first, second), claims, strict=True)
    ]
    results = await asyncio.gather(*(env.prepare() for env in environments), return_exceptions=True)
    assert sum(result is None for result in results) == 1
    error = next(result for result in results if isinstance(result, EnvironmentError))
    assert error.code == "environment_capacity_exceeded" and error.details == {"capacity": kind, "limit": 1}
    async with short_session(sessions) as session:
        rows = [await session.get(EnvironmentRecord, env.environment_id) for env in environments]
        assert sorted(row.status for row in rows) == ["running", "unprepared"]
        runs = [await session.get(RunRecord, run.id) for run in (first, second)]
        assert sum(row.environment_use_started_at is not None for row in runs) == 1
    for env in environments:
        await env.close()


async def test_shared_run_reuses_active_slot_and_stopped_target_counts_until_deleted(
    interaction_sessions, interaction_object_store, tmp_path, monkeypatch
):
    sessions = interaction_sessions
    service, template, lifecycle = await recipe(sessions, tmp_path, "on_use")
    lifecycle.capacity = CapacityLimits(max_targets=1, max_active=1)
    _, first, _ = await _accept_root(sessions, interaction_object_store)
    scheduler = AttemptScheduler(sessions, clock=lambda: NOW + timedelta(seconds=1), lifecycle=test_lifecycle_writer())
    first_claim = await scheduler.claim(first.id, _worker())
    first_env = await prepare_run_environment(
        lifecycle, await prepare_permissions(sessions, first, _authority(first_claim))
    )
    await first_env.prepare()
    sibling = await sibling_run(sessions, first, first_env.environment_id)
    sibling_claim = await scheduler.claim(sibling.id, _worker())
    sibling_env = await prepare_run_environment(
        lifecycle, await prepare_permissions(sessions, sibling, _authority(sibling_claim))
    )
    await sibling_env.prepare()
    await first_env.close()
    await sibling_env.close()
    # Use-ended state releases the active slot; the retained stopped target still occupies a total slot.
    async with transaction(sessions) as session:
        for run in (first, sibling):
            row = await session.get(RunRecord, run.id)
            row.environment_use_started_at = None
        (await session.get(EnvironmentRecord, first_env.environment_id)).status = "stopped"
    other = await service.create_environment(
        actor=hook_actor(),
        workspace_id=WORKSPACE_ID,
        request=NewEnvironmentSelection(template_id=template.id),
        idempotency_key="next-target",
    )
    async with transaction(sessions) as session:
        (await session.get(RunRecord, first.id)).environment_id = other.id
    next_env = await prepare_run_environment(
        lifecycle, await prepare_permissions(sessions, first, _authority(first_claim))
    )
    construct = AsyncMock(wraps=lifecycle.construct)
    monkeypatch.setattr(lifecycle, "construct", construct)
    with pytest.raises(EnvironmentError) as error:
        await next_env.prepare()
    assert error.value.details["capacity"] == "targets"
    construct.assert_not_awaited()
    async with transaction(sessions) as session:
        deleted = await session.get(EnvironmentRecord, first_env.environment_id)
        deleted.status, deleted.state, deleted.target_identity = "deleted", None, None
    await next_env.prepare()
    construct.assert_awaited_once()
    await next_env.close()
