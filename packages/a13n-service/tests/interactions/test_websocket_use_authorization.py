from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass, replace

import pytest
from a13n_environment import EnvironmentAction, EnvironmentState, build_environment_provider_catalog
from a13n_environment.models import EnvironmentError
from a13n_service.environments.domain import CreateProviderRequest, RegisterEnvironmentRequest
from a13n_service.environments.models import EnvironmentProviderRecord
from a13n_service.environments.service import EnvironmentService
from a13n_service.environments.websocket.authority import ConnectionIdentity, UseIdentity
from a13n_service.environments.websocket.coordination import ConnectionCoordination
from a13n_service.environments.websocket.use_authorization import ClientUseAuthorization
from a13n_service.interactions.domain import Run
from a13n_service.interactions.scheduling import AttemptScheduler, ClaimedAttempt
from a13n_service.secrets.crypto import SecretProtector
from a13n_service.storage import transaction
from a13n_service.temporal import utc_now

from tests.environments.websocket.conftest import relay_redis as relay_redis
from tests.hooks.support import hook_actor, seed_hook_actor_access
from tests.lifecycle_support import test_lifecycle_writer

from .conftest import NOW, ORGANIZATION_ID, WORKSPACE_ID
from .mount_helpers import accepted_mount
from .test_attempt_execution import _accept_root, _worker

pytestmark = pytest.mark.anyio


@dataclass(frozen=True)
class AdmittedUse:
    identity: UseIdentity
    provider_id: str
    service: EnvironmentService
    run: Run
    claim: ClaimedAttempt


@pytest.fixture
async def client_environment(interaction_sessions):
    await seed_hook_actor_access(interaction_sessions)
    protector = SecretProtector.from_base64(encoded_key=base64.b64encode(b"e" * 32).decode(), encryption_key_id="test")
    service = EnvironmentService(
        interaction_sessions, build_environment_provider_catalog(builtin_keys=("a13n.websocket-envd",)), protector
    )
    provider = await service.create_provider(
        actor=hook_actor(),
        workspace_id=WORKSPACE_ID,
        request=CreateProviderRequest(type="a13n.websocket-envd", name="Client"),
    )
    environment = await service.create_environment(
        actor=hook_actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="client",
        request=RegisterEnvironmentRequest(
            provider_id=provider.id,
            configuration={},
            state=EnvironmentState(
                provider_key="a13n.websocket-envd", state_version="1", state={"daemon_environment_id": "native"}
            ),
        ),
    )
    return service, provider, environment


@pytest.fixture
async def admitted_use(request, interaction_sessions, interaction_object_store, client_environment, relay_redis):
    service, provider, environment = client_environment
    coordination = ConnectionCoordination(relay_redis)
    ticket = await coordination.issue(ORGANIZATION_ID, environment.id)
    candidate = await coordination.admit(
        ORGANIZATION_ID, environment.id, ticket=ticket.secret, owner_instance_id="test-control"
    )
    assert candidate.value.connection is not None
    connection = candidate.value.connection
    await asyncio.sleep(coordination.limits.lease_ms / 1000 + 0.02)
    await coordination.promote(connection)
    await coordination.online(connection)
    has_primary = getattr(request, "param", True)
    _, run, _ = await _accept_root(
        interaction_sessions,
        interaction_object_store,
        environment_id=environment.id if has_primary else None,
        coordination=coordination,
    )
    await coordination.retire(connection)
    await coordination.acknowledge(connection)
    claim = await AttemptScheduler(interaction_sessions, clock=utc_now, lifecycle=test_lifecycle_writer()).claim(
        run.id, _worker()
    )
    assert isinstance(claim, ClaimedAttempt)
    identity = UseIdentity(
        ConnectionIdentity(ORGANIZATION_ID, environment.id, "connection", "epoch", "control"),
        "use",
        run.id,
        claim.attempt.id,
        claim.attempt.attempt_number,
        claim.attempt.worker_id,
    )
    return AdmittedUse(identity, provider.id, service, run, claim)


async def test_control_rechecks_persisted_attempt_and_allows_provider_operations(interaction_sessions, admitted_use):
    identity = admitted_use.identity
    permissions = await ClientUseAuthorization(interaction_sessions)(identity)
    assert EnvironmentAction.FILE_READ_BYTES in permissions
    assert EnvironmentAction.FILE_WRITE_BYTES in permissions
    assert EnvironmentAction.SHELL_EXEC in permissions
    assert interaction_sessions.kw["bind"].sync_engine.pool.checkedout() == 0


@pytest.mark.parametrize(
    "change",
    [
        {"attempt_fence": 2},
        {"worker_instance_id": "different"},
        {"run_id": "run-other"},
        {"attempt_id": "attempt-other"},
    ],
)
async def test_foreign_use_scope_is_rejected_before_eip_binding(interaction_sessions, admitted_use, change):
    identity = admitted_use.identity
    with pytest.raises(EnvironmentError) as error:
        await ClientUseAuthorization(interaction_sessions)(replace(identity, **change))
    assert error.value.code == "environment_forbidden"


async def test_disabled_provider_cannot_gain_use_from_old_online_presence(interaction_sessions, admitted_use):
    identity, provider_id = admitted_use.identity, admitted_use.provider_id
    async with transaction(interaction_sessions) as session:
        provider = await session.get(EnvironmentProviderRecord, provider_id)
        provider.enabled = False
    with pytest.raises(EnvironmentError):
        await ClientUseAuthorization(interaction_sessions)(identity)


async def test_each_alias_requires_its_own_accepted_binding(interaction_sessions, admitted_use):
    identity = admitted_use.identity
    authorize = ClientUseAuthorization(interaction_sessions)
    primary = await authorize(identity, "workspace")
    with pytest.raises(EnvironmentError):
        await authorize(identity, "writer")
    async with transaction(interaction_sessions) as session:
        mount = accepted_mount(identity.run_id, identity.connection.environment_id, name="writer")
        session.add(mount)
    writable = await authorize(identity, "writer")
    assert EnvironmentAction.SHELL_EXEC in writable
    assert writable == primary
    assert await authorize(identity, "workspace") == primary
    with pytest.raises(EnvironmentError):
        await authorize(identity, "unknown")


async def test_additional_only_run_can_acquire_use_and_cannot_invent_a_primary(
    interaction_sessions, interaction_object_store, client_environment
):
    _, _, environment = client_environment
    _, run, _ = await _accept_root(interaction_sessions, interaction_object_store)
    async with transaction(interaction_sessions) as session:
        session.add(accepted_mount(run.id, environment.id))
    claim = await AttemptScheduler(interaction_sessions, clock=utc_now, lifecycle=test_lifecycle_writer()).claim(
        run.id, _worker()
    )
    assert isinstance(claim, ClaimedAttempt)
    identity = UseIdentity(
        ConnectionIdentity(ORGANIZATION_ID, environment.id, "connection", "epoch", "control"),
        "use",
        run.id,
        claim.attempt.id,
        claim.attempt.attempt_number,
        claim.attempt.worker_id,
    )
    authorize = ClientUseAuthorization(interaction_sessions)
    assert await authorize(identity) == await authorize(identity, "computer")
    with pytest.raises(EnvironmentError):
        await authorize(identity, "workspace")
    # A lease which was valid at original acceptance cannot authorize a later binding.
    from a13n_service.interactions.models import RunAttemptRecord

    async with transaction(interaction_sessions) as session:
        (await session.get(RunAttemptRecord, claim.attempt.id)).lease_expires_at = NOW
    with pytest.raises(EnvironmentError):
        await authorize(identity, "computer")


@pytest.mark.parametrize("failure", ["principal", "dependency"])
async def test_mount_admission_errors_remain_bounded_environment_failures(
    interaction_sessions, admitted_use, monkeypatch, failure
):
    from unittest.mock import AsyncMock

    from a13n_service.environments.websocket import use_authorization
    from a13n_service.iam import AuthorizationError
    from sqlalchemy.exc import OperationalError

    error = (
        AuthorizationError("permission_denied", concealed=True)
        if failure == "principal"
        else OperationalError("SELECT", {}, OSError("disconnected"), connection_invalidated=True)
    )
    monkeypatch.setattr(use_authorization, "authorize_persisted_agent_principal_actions", AsyncMock(side_effect=error))
    with pytest.raises(EnvironmentError) as caught:
        await ClientUseAuthorization(interaction_sessions)(admitted_use.identity, "workspace")
    assert caught.value.code == ("environment_forbidden" if failure == "principal" else "environment_unavailable")
    assert interaction_sessions.kw["bind"].sync_engine.pool.checkedout() == 0
