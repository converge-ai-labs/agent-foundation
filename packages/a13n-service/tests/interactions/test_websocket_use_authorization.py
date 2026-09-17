from __future__ import annotations

import base64
from dataclasses import replace

import pytest
from a13n_environment import EnvironmentAction, EnvironmentState, build_environment_provider_catalog
from a13n_environment.models import EnvironmentError
from a13n_service.environments.domain import CreateProviderRequest, RegisterEnvironmentRequest
from a13n_service.environments.models import EnvironmentProviderRecord
from a13n_service.environments.service import EnvironmentService
from a13n_service.environments.websocket.authority import ConnectionIdentity, UseIdentity
from a13n_service.environments.websocket.use_authorization import ClientUseAuthorization
from a13n_service.interactions.scheduling import AttemptScheduler, ClaimedAttempt
from a13n_service.secrets.crypto import SecretProtector
from a13n_service.storage import transaction
from a13n_service.temporal import utc_now

from tests.hooks.support import hook_actor, seed_hook_actor_access
from tests.lifecycle_support import test_lifecycle_writer

from .conftest import ORGANIZATION_ID, WORKSPACE_ID
from .test_attempt_execution import _accept_root, _worker

pytestmark = pytest.mark.anyio


@pytest.fixture
async def admitted_use(interaction_sessions, interaction_object_store):
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
    _, run, _ = await _accept_root(
        interaction_sessions, interaction_object_store, environment_id=environment.id, environment_access="read_only"
    )
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
    return identity, provider.id


async def test_control_rechecks_persisted_attempt_and_accepted_access(interaction_sessions, admitted_use):
    identity, _ = admitted_use
    permissions = await ClientUseAuthorization(interaction_sessions)(identity)
    assert EnvironmentAction.FILE_READ_BYTES in permissions
    assert EnvironmentAction.FILE_WRITE_BYTES not in permissions
    assert EnvironmentAction.SHELL_EXEC not in permissions
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
    identity, _ = admitted_use
    with pytest.raises(EnvironmentError) as error:
        await ClientUseAuthorization(interaction_sessions)(replace(identity, **change))
    assert error.value.code == "environment_forbidden"


async def test_disabled_provider_cannot_gain_use_from_old_online_presence(interaction_sessions, admitted_use):
    identity, provider_id = admitted_use
    async with transaction(interaction_sessions) as session:
        provider = await session.get(EnvironmentProviderRecord, provider_id)
        provider.enabled = False
    with pytest.raises(EnvironmentError):
        await ClientUseAuthorization(interaction_sessions)(identity)
