"""Primary and additional bindings share preparation policy and retain separate evidence."""

import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest
from a13n_harness.providers.environment.models import EnvironmentError
from a13n_service.environments.capacity import CapacityLimits
from a13n_service.environments.domain import CreateManagedEnvironmentRequest
from a13n_service.environments.models import EnvironmentRecord
from a13n_service.environments.mount_models import RunEnvironmentMountRecord
from a13n_service.environments.mount_observations import RunMountObservations
from a13n_service.environments.runtime import RunEnvironment, prepare_run_environment
from a13n_service.interactions.attempts import AttemptAuthorityError
from a13n_service.interactions.models import RunRecord
from a13n_service.interactions.scheduling import AttemptScheduler, ClaimedAttempt
from a13n_service.storage import short_session, transaction

from tests.hooks.support import hook_actor
from tests.lifecycle_support import test_lifecycle_writer

from .conftest import NOW, WORKSPACE_ID
from .mount_helpers import accepted_mount
from .test_attempt_execution import _accept_root, _authority, _worker
from .test_environment_runtime import template_config
from .worker_helpers import prepare_permissions

pytestmark = pytest.mark.anyio


@pytest.fixture
async def preparations(interaction_sessions, interaction_object_store, tmp_path):
    sessions = interaction_sessions
    service, template, lifecycle = await template_config(sessions, tmp_path, "on_use")
    extra = await service.create_environment(
        actor=hook_actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="additional-target",
        request=CreateManagedEnvironmentRequest(template_id=template.id),
    )
    _, run, _ = await _accept_root(sessions, interaction_object_store)
    async with transaction(sessions) as session:
        session.add_all(
            [
                accepted_mount(run.id, extra.id, name="first", access="full"),
                accepted_mount(
                    run.id, extra.id, name="second", access="full", created_at=NOW + timedelta(microseconds=1)
                ),
            ]
        )
    claim = await AttemptScheduler(sessions, clock=lambda: NOW, lifecycle=test_lifecycle_writer()).claim(
        run.id, _worker()
    )
    assert isinstance(claim, ClaimedAttempt)
    attempt = await prepare_permissions(sessions, run, _authority(claim))
    mounts = await RunMountObservations(sessions, clock=lambda: NOW).snapshot(attempt)
    return lifecycle, attempt, mounts, extra.id


async def test_additions_prepare_lazy_templates_and_do_not_mark_primary_used(interaction_sessions, preparations):
    lifecycle, attempt, mounts, extra_id = preparations
    lifecycle.capacity = CapacityLimits(max_active=1)
    first = await prepare_run_environment(lifecycle, attempt, mount=mounts[0])
    assert first is not None and first.availability.status == "available"
    async with short_session(interaction_sessions) as session:
        run = await session.get(RunRecord, attempt.run_id)
        primary_id = run.environment_id
        assert run.environment_use_started_at is None
        assert (await session.get(EnvironmentRecord, primary_id)).status == "unprepared"
        stamp = (await session.get(RunEnvironmentMountRecord, (run.id, "first"))).use_started_at
        assert stamp is not None
        assert (await session.get(RunEnvironmentMountRecord, (run.id, "second"))).use_started_at is None
    # A second alias uses the same active-target slot, even before the first closes.
    second = await prepare_run_environment(lifecycle, attempt, mount=mounts[1])
    assert second is not None and second.environment_id == extra_id
    await first.close()
    await second.close()
    lifecycle.clock = lambda: NOW + timedelta(seconds=3)
    retried = await prepare_run_environment(lifecycle, attempt, mount=mounts[0])
    assert retried is not None
    await retried.close()
    primary = await prepare_run_environment(lifecycle, attempt)
    assert primary is not None
    with pytest.raises(EnvironmentError) as full:
        await primary.prepare()
    assert full.value.code == "environment_capacity_exceeded"
    await primary.close()
    async with short_session(interaction_sessions) as session:
        assert (await session.get(RunEnvironmentMountRecord, (attempt.run_id, "first"))).use_started_at == stamp
        assert (await session.get(RunEnvironmentMountRecord, (attempt.run_id, "second"))).use_started_at is not None
        assert (await session.get(RunRecord, attempt.run_id)).environment_use_started_at is None


@pytest.mark.parametrize(
    "changed", [{"access": "read_only"}, {"created_at": NOW + timedelta(seconds=1)}, {"run_id": "other"}]
)
async def test_changed_acceptance_snapshot_cannot_start_preparation(interaction_sessions, preparations, changed):
    lifecycle, attempt, mounts, extra_id = preparations
    with pytest.raises(ValueError):
        await prepare_run_environment(lifecycle, attempt, mount=replace(mounts[0], **changed))
    async with short_session(interaction_sessions) as session:
        assert (await session.get(RunEnvironmentMountRecord, (attempt.run_id, "first"))).use_started_at is None
        environment = await session.get(EnvironmentRecord, extra_id)
        assert environment.status == "unprepared" and environment.operation_id is None


async def test_local_attempt_loss_prevents_additional_preparation_effects(interaction_sessions, preparations):
    lifecycle, attempt, mounts, extra_id = preparations
    attempt.lease.invalidate()
    with pytest.raises(AttemptAuthorityError):
        await prepare_run_environment(lifecycle, attempt, mount=mounts[0])
    async with short_session(interaction_sessions) as session:
        assert (await session.get(RunEnvironmentMountRecord, (attempt.run_id, "first"))).use_started_at is None
        environment = await session.get(EnvironmentRecord, extra_id)
        assert environment.status == "unprepared" and environment.operation_id is None


@pytest.mark.parametrize("failure", [OSError("injected preparation failure"), asyncio.CancelledError()])
async def test_failed_preparation_closes_the_acquired_candidate(preparations, monkeypatch, failure):
    lifecycle, attempt, mounts, _ = preparations
    original_prepare = RunEnvironment.prepare
    candidates = []

    async def interrupted_prepare(environment):
        candidates.append(environment)
        await original_prepare(environment)
        raise failure

    monkeypatch.setattr(RunEnvironment, "prepare", interrupted_prepare)
    with pytest.raises(type(failure)):
        await prepare_run_environment(lifecycle, attempt, mount=mounts[0])
    with pytest.raises(RuntimeError, match="closed"):
        await original_prepare(candidates[0])
