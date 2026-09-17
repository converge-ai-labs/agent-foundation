"""PG observations never substitute for installation in the current Harness facade."""

from contextlib import asynccontextmanager
from datetime import timedelta

import pytest
from a13n_environment import EnvironmentError
from a13n_harness import AgentIdentityRef, AgentInstanceContext
from a13n_harness.environment.advanced import create_empty_environment_runtime
from a13n_service.environments.mount_models import RunEnvironmentMountRecord
from a13n_service.environments.mount_observations import RunMountObservations
from a13n_service.environments.mount_runtime import RunMountRuntime
from a13n_service.environments.runtime import prepare_run_environment
from a13n_service.interactions.attempts import AttemptAuthorityError
from a13n_service.storage import short_session, transaction

from .conftest import NOW
from .mount_helpers import accepted_mount
from .test_mount_preparation import preparations as preparations

pytestmark = pytest.mark.anyio


@asynccontextmanager
async def mounted_runtime(sessions, preparations, *, prepare=None, clock=lambda: 0, observations=None):
    lifecycle, attempt, _, _ = preparations
    calls = []

    async def candidate(mount):
        assert sessions.kw["bind"].sync_engine.pool.checkedout() == 0
        calls.append(mount.name)
        environment = await prepare_run_environment(lifecycle, attempt, mount=mount)
        assert environment is not None
        return environment

    runtime = create_empty_environment_runtime()
    store = observations or RunMountObservations(sessions, clock=lambda: NOW)
    controller = RunMountRuntime(
        runtime=runtime,
        observations=store,
        current_attempt=lambda: attempt,
        prepare=prepare or candidate,
        clock=clock,
    )
    async with runtime.bind(
        thread_id=attempt.thread_id,
        run_id=attempt.run_id,
        instance=AgentInstanceContext(
            identity=AgentIdentityRef(issuer="test", subject="test"), agent_instance_id=attempt.thread_id
        ),
        host_refs={},
    ) as bound:
        await runtime._activate()
        yield controller, bound, calls


async def test_ready_in_pg_is_reconstructed_and_reconcile_never_installs(interaction_sessions, preparations):
    _, attempt, mounts, _ = preparations
    store = RunMountObservations(interaction_sessions, clock=lambda: NOW)
    for mount in mounts:
        await store.publish(attempt, mount, "ready")
    async with mounted_runtime(interaction_sessions, preparations) as (controller, bound, calls):
        assert await controller.reconcile() == mounts
        assert not bound.snapshot.mounts and not calls
        await controller.apply()
        assert [mount.name for mount in bound.snapshot.mounts] == ["first", "second"]
        assert bound.snapshot.default_mount is None
        await controller.apply()
        assert calls == ["first", "second"]
        # Both aliases point at the prepared target and share its durable files.
        await bound.files.write_text("/environment/first/hello.txt", "hello", mode="create")
        assert (await bound.files.read_text("/environment/second/hello.txt")).text == "hello"


async def test_failed_ready_write_retries_without_reentering_adapter(interaction_sessions, preparations, monkeypatch):
    _, attempt, _, _ = preparations
    store = RunMountObservations(interaction_sessions, clock=lambda: NOW)
    publish = store.publish
    available = False

    async def flaky_publish(attempt, mount, status, **kwargs):
        if status == "ready" and not available:
            raise OSError("injected PG observation outage")
        await publish(attempt, mount, status, **kwargs)

    monkeypatch.setattr(store, "publish", flaky_publish)
    async with mounted_runtime(interaction_sessions, preparations, observations=store) as (controller, bound, calls):
        await controller.apply()
        assert len(bound.snapshot.mounts) == 2
        async with short_session(interaction_sessions) as session:
            row = await session.get(RunEnvironmentMountRecord, (attempt.run_id, "first"))
            assert row.application_status == "preparing"
        available = True
        await controller.reconcile()
        await controller.apply()
        assert calls == ["first", "second"]
        async with short_session(interaction_sessions) as session:
            row = await session.get(RunEnvironmentMountRecord, (attempt.run_id, "first"))
            assert row.application_status == "ready"


async def test_unavailable_mount_retries_with_backoff_and_preserves_other_mounts(interaction_sessions, preparations):
    lifecycle, attempt, _, _ = preparations
    calls = []
    now = 0
    online = False

    async def prepare(mount):
        calls.append(mount.name)
        if mount.name == "first" and not online:
            raise EnvironmentError("offline", code="environment_unavailable")
        environment = await prepare_run_environment(lifecycle, attempt, mount=mount)
        assert environment is not None
        return environment

    async with mounted_runtime(interaction_sessions, preparations, prepare=prepare, clock=lambda: now) as (
        controller,
        bound,
        _,
    ):
        await controller.apply()
        second = bound.snapshot.mounts[0]
        assert second.name == "second"
        for tick in [0, 1, 4]:
            now = tick
            await controller.apply()
        assert calls == ["first", "second"]
        now = 5
        await controller.apply()
        assert calls == ["first", "second", "first"]
        online = True
        now = 14
        await controller.apply()
        assert len(bound.snapshot.mounts) == 1
        now = 15
        await controller.apply()
        assert [mount.name for mount in bound.snapshot.mounts] == ["second", "first"]
        assert bound.snapshot.mounts[0] == second


async def test_dependency_failure_is_distinct_and_does_not_hot_loop(interaction_sessions, preparations):
    calls = []

    async def prepare(mount):
        calls.append(mount.name)
        raise EnvironmentError("secret details", code="environment_permission_denied")

    async with mounted_runtime(interaction_sessions, preparations, prepare=prepare) as (controller, bound, _):
        await controller.apply()
        await controller.apply()
        assert not bound.snapshot.mounts
        assert calls == ["first", "second"]
    _, attempt, _, _ = preparations
    async with short_session(interaction_sessions) as session:
        row = await session.get(RunEnvironmentMountRecord, (attempt.run_id, "first"))
        assert row.error["code"] == "environment_permission_denied"
        assert "secret" not in row.error["message"]


async def test_lost_attempt_after_preparation_closes_candidate_without_installing(interaction_sessions, preparations):
    lifecycle, attempt, _, _ = preparations
    prepared = []

    async def prepare(mount):
        environment = await prepare_run_environment(lifecycle, attempt, mount=mount)
        assert environment is not None
        prepared.append(environment)
        attempt.lease.invalidate()
        return environment

    async with mounted_runtime(interaction_sessions, preparations, prepare=prepare) as (controller, bound, _):
        with pytest.raises(AttemptAuthorityError):
            await controller.apply()
        assert not bound.snapshot.mounts
        with pytest.raises(RuntimeError, match="closed"):
            await prepared[0].prepare()


async def test_acceptance_during_preparation_waits_for_next_boundary(interaction_sessions, preparations):
    lifecycle, attempt, _, environment_id = preparations
    added = False

    async def prepare(mount):
        nonlocal added
        if not added:
            async with transaction(interaction_sessions) as session:
                session.add(
                    accepted_mount(
                        attempt.run_id,
                        environment_id,
                        name="later",
                        access="full",
                        created_at=NOW + timedelta(microseconds=2),
                    )
                )
            added = True
        environment = await prepare_run_environment(lifecycle, attempt, mount=mount)
        assert environment is not None
        return environment

    async with mounted_runtime(interaction_sessions, preparations, prepare=prepare) as (controller, bound, _):
        await controller.apply()
        assert [mount.name for mount in bound.snapshot.mounts] == ["first", "second"]
        await controller.apply()
        assert [mount.name for mount in bound.snapshot.mounts] == ["first", "second", "later"]
