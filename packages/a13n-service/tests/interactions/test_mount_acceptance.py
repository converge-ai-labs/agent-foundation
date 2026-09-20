from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest
from a13n_harness import SafeFailure
from a13n_service.environments.errors import EnvironmentManagementError
from a13n_service.environments.models import EnvironmentProviderRecord
from a13n_service.environments.mount_domain import AddEnvironmentMountRequest
from a13n_service.environments.mount_models import RunEnvironmentMountRecord
from a13n_service.environments.mounts import RunEnvironmentMountService
from a13n_service.environments.websocket.admission import OnlineAdmission
from a13n_service.environments.websocket.coordination import ConnectionCoordination
from a13n_service.iam import AuthorizationError, PrincipalRef
from a13n_service.iam.models import RoleBindingRecord, UserRecord
from a13n_service.interactions.attempts import AttemptExecutionService
from a13n_service.interactions.models import RunRecord
from a13n_service.interactions.scheduling import AttemptScheduler, ClaimedAttempt
from a13n_service.storage import short_session, transaction
from anyio import create_task_group, sleep
from sqlalchemy import func, select

from tests.environments.websocket.conftest import relay_redis as relay_redis
from tests.hooks.support import hook_actor
from tests.lifecycle_support import test_lifecycle_writer

from .conftest import AGENT_ID, NOW, ORGANIZATION_ID, USER_ID, WORKSPACE_ID
from .test_attempt_execution import _accept_root, _authority, _worker
from .test_environment_retention import cancel
from .test_websocket_acceptance import _connect
from .test_websocket_use_authorization import client_environment as client_environment

pytestmark = pytest.mark.anyio


@pytest.fixture
async def mount_run(interaction_sessions, interaction_object_store, client_environment, relay_redis):
    _, provider, environment = client_environment
    _, run, _ = await _accept_root(interaction_sessions, interaction_object_store)
    coordination = ConnectionCoordination(relay_redis)
    service = RunEnvironmentMountService(
        interaction_sessions, OnlineAdmission(interaction_sessions, coordination), clock=lambda: NOW
    )
    return service, coordination, run, provider, environment


@pytest.fixture
async def online_mount_run(mount_run):
    service, coordination, run, provider, environment = mount_run
    # These cases verify relational acceptance, not expiration of connection evidence.
    coordination.limits = replace(coordination.limits, lease_ms=5_000, candidate_ms=30_000)
    connection = await _connect(coordination, environment.id)

    async def keep_online(*, task_status):
        observed = await coordination.renew(connection)
        assert observed.value.connection == connection
        task_status.started()
        while True:
            await sleep(coordination.limits.lease_ms / 3000)
            observed = await coordination.renew(connection)
            assert observed.value.connection == connection and observed.value.status == "online"

    async with create_task_group() as tasks:
        await tasks.start(keep_online)
        try:
            yield service, coordination, run, provider, environment
        finally:
            tasks.cancel_scope.cancel()


async def _add(service, run, environment, *, name="computer", key="mount", actor=None):
    return await service.add(
        actor=actor or hook_actor(),
        run_id=run.id,
        idempotency_key=key,
        request=AddEnvironmentMountRequest(
            name=name, environment_id=environment.id, working_directory="/projects/computer"
        ),
    )


async def test_mount_receipt_survives_offline_disabled_provider_and_terminal_run(
    interaction_sessions, interaction_object_store, mount_run, monkeypatch
):
    service, coordination, run, provider, environment = mount_run
    connection = await _connect(coordination, environment.id)
    original = coordination.observe
    observations = 0

    async def observe(*args):
        nonlocal observations
        observations += 1
        assert interaction_sessions.kw["bind"].sync_engine.pool.checkedout() == 0
        return await original(*args)

    monkeypatch.setattr(coordination, "observe", observe)
    accepted = await _add(service, run, environment)
    assert accepted.application_status == "pending"
    await coordination.retire(connection)
    await cancel(interaction_sessions, interaction_object_store, run, NOW + timedelta(seconds=1))
    async with transaction(interaction_sessions) as database:
        (await database.get(EnvironmentProviderRecord, provider.id)).enabled = False
    assert await _add(service, run, environment) == accepted
    assert observations == 1
    changed = await _add(service, run, environment, name="changed")
    assert changed == accepted
    async with short_session(interaction_sessions) as database:
        assert await database.scalar(select(func.count()).select_from(RunEnvironmentMountRecord)) == 1
        assert (await database.get(RunRecord, run.id)).environment_id is None


@pytest.mark.parametrize("presence", ["offline", "connecting", "takeover"])
async def test_mount_offline_acceptance_has_no_association(interaction_sessions, mount_run, presence):
    service, coordination, run, _, environment = mount_run
    if presence != "offline":
        await _connect(coordination, environment.id, online=presence == "takeover")
    if presence == "takeover":
        ticket = await coordination.issue(run.organization_id, environment.id)
        await coordination.admit(
            run.organization_id, environment.id, ticket=ticket.secret, owner_instance_id="new-control"
        )
    with pytest.raises(EnvironmentManagementError) as caught:
        await _add(service, run, environment)
    assert caught.value.code == "environment_unavailable"
    async with short_session(interaction_sessions) as database:
        assert await database.scalar(select(func.count()).select_from(RunEnvironmentMountRecord)) == 0


async def test_concurrent_names_preserve_acceptance_order_and_clock_rollback(online_mount_run):
    service, _, run, _, environment = online_mount_run
    first, second = await asyncio.gather(
        _add(service, run, environment, name="first", key="first"),
        _add(service, run, environment, name="second", key="second"),
    )
    service._clock = lambda: NOW - timedelta(days=1)
    third = await _add(service, run, environment, name="third", key="third")
    expected = [*sorted([first, second], key=lambda row: row.created_at), third]
    assert expected[0].created_at < expected[1].created_at < third.created_at
    page = await service.list(actor=hook_actor(), run_id=run.id, limit=2)
    assert list(page.items) == expected[:2]
    assert page.next_cursor is not None
    rest = await service.list(actor=hook_actor(), run_id=run.id, limit=2, cursor=page.next_cursor)
    assert rest.items == (third,) and rest.next_cursor is None
    with pytest.raises(EnvironmentManagementError) as caught:
        await _add(service, run, environment, name="first", key="different-key")
    assert caught.value.code == "environment_mount_conflict"


async def test_same_key_concurrent_mount_requests_share_one_receipt(online_mount_run):
    service, _, run, _, environment = online_mount_run
    first, second = await asyncio.gather(_add(service, run, environment), _add(service, run, environment))
    assert first == second
    assert (await service.list(actor=hook_actor(), run_id=run.id)).items == (first,)


async def test_peer_receipt_wins_over_disconnect_during_observation(
    interaction_sessions, mount_run, relay_redis, monkeypatch
):
    service, coordination, run, _, environment = mount_run
    connection = await _connect(coordination, environment.id)
    peer_coordination = ConnectionCoordination(relay_redis)
    peer = RunEnvironmentMountService(
        interaction_sessions, OnlineAdmission(interaction_sessions, peer_coordination), clock=lambda: NOW
    )
    observe = coordination.observe

    async def after_peer_acceptance(*args):
        await _add(peer, run, environment)
        await peer_coordination.retire(connection)
        return await observe(*args)

    monkeypatch.setattr(coordination, "observe", after_peer_acceptance)
    receipt = await _add(service, run, environment)
    assert (await service.list(actor=hook_actor(), run_id=run.id)).items == (receipt,)


@pytest.mark.parametrize("restricted", ["caller", "principal"])
async def test_caller_and_original_principal_each_need_environment_use(interaction_sessions, mount_run, restricted):
    service, _, run, _, environment = mount_run
    caller_id = "usr_9999999999999999"
    caller = replace(hook_actor(), principal=PrincipalRef(principal_type="user", principal_id=caller_id))
    async with transaction(interaction_sessions) as database:
        database.add(
            UserRecord(
                id=caller_id,
                email="mount-caller@example.com",
                normalized_email="mount-caller@example.com",
                name="Mount caller",
                status="active",
                email_verified_at=NOW,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await database.flush()
        for kind, resource_id, role in (
            ("organization", ORGANIZATION_ID, "member"),
            ("workspace", WORKSPACE_ID, "builder"),
        ):
            database.add(
                RoleBindingRecord(
                    id=f"rb_mountcaller_{kind}",
                    organization_id=ORGANIZATION_ID,
                    workspace_id=WORKSPACE_ID if kind == "workspace" else None,
                    principal_type="user",
                    principal_id=caller_id,
                    resource_type=kind,
                    resource_id=resource_id,
                    role_key=role,
                    created_by_user_id=USER_ID,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
        await database.flush()
        binding_id = "rb_mountcaller_workspace" if restricted == "caller" else "rb_hookws717171717"
        binding = await database.get(RoleBindingRecord, binding_id)
        binding.role_key = "viewer"
        database.add(
            RoleBindingRecord(
                id="rb_mount_agent_runner",
                organization_id=ORGANIZATION_ID,
                workspace_id=WORKSPACE_ID,
                principal_type="user",
                principal_id=caller_id if restricted == "caller" else USER_ID,
                resource_type="agent",
                resource_id=AGENT_ID,
                role_key="runner",
                created_by_user_id=USER_ID,
                created_at=NOW,
                updated_at=NOW,
            )
        )
    with pytest.raises((AuthorizationError, EnvironmentManagementError)) as caught:
        await _add(service, run, environment, actor=caller)
    assert caught.value.code in {"permission_denied", "environment_not_found"}
    async with short_session(interaction_sessions) as database:
        assert await database.scalar(select(func.count()).select_from(RunEnvironmentMountRecord)) == 0


@pytest.mark.parametrize("retryable", [False, True])
async def test_observations_survive_sealing_but_not_retry(interaction_sessions, mount_run, retryable):
    service, coordination, run, _, environment = mount_run
    await _connect(coordination, environment.id)
    await _add(service, run, environment)
    claim = await AttemptScheduler(
        interaction_sessions, clock=lambda: NOW + timedelta(seconds=1), lifecycle=test_lifecycle_writer()
    ).claim(run.id, _worker())
    assert isinstance(claim, ClaimedAttempt)
    async with transaction(interaction_sessions) as database:
        mount = await database.get(RunEnvironmentMountRecord, (run.id, "computer"))
        mount.applied_attempt_id = claim.attempt.id
        mount.applied_attempt_fence = claim.attempt.attempt_number
        mount.application_status = "ready"
        mount.observed_at = NOW + timedelta(seconds=1)
    ready = (await service.list(actor=hook_actor(), run_id=run.id)).items[0]
    assert ready.application_status == "ready" and ready.applied_attempt_id == claim.attempt.id
    await AttemptExecutionService(
        interaction_sessions, clock=lambda: NOW + timedelta(seconds=2), lifecycle=test_lifecycle_writer()
    ).fail(_authority(claim), SafeFailure(code="transient", message="Try again"), retryable=retryable)
    observed = (await service.list(actor=hook_actor(), run_id=run.id)).items[0]
    if retryable:
        assert observed.application_status == "pending" and observed.applied_attempt_id is None
        assert observed.observed_at is None and observed.error is None
        successor = await AttemptScheduler(
            interaction_sessions, clock=lambda: NOW + timedelta(seconds=3), lifecycle=test_lifecycle_writer()
        ).claim(run.id, _worker())
        assert isinstance(successor, ClaimedAttempt)
        await AttemptExecutionService(
            interaction_sessions, clock=lambda: NOW + timedelta(seconds=4), lifecycle=test_lifecycle_writer()
        ).fail(_authority(successor), SafeFailure(code="terminal", message="Stopped"), retryable=False)
        # Sealing a later Attempt must not revive installation evidence from its predecessor.
        assert (await service.list(actor=hook_actor(), run_id=run.id)).items[0].application_status == "pending"
    else:
        assert observed == ready


async def test_failed_signal_cannot_undo_committed_mount(interaction_sessions, online_mount_run):
    service, _, run, _, environment = online_mount_run
    calls = []

    class BrokenSignals:
        async def publish(self, *, organization_id, thread_id):
            async with short_session(interaction_sessions) as database:
                assert await database.get(RunEnvironmentMountRecord, (run.id, "computer")) is not None
            calls.append((organization_id, thread_id))
            raise RuntimeError("Signal channel is unavailable")

    service._signals = BrokenSignals()
    receipt = await _add(service, run, environment)
    assert calls == [(run.organization_id, run.thread_id)]
    assert (await service.list(actor=hook_actor(), run_id=run.id)).items == (receipt,)
