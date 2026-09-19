from datetime import timedelta

import pytest
from a13n_service.environments.domain import RegisterEnvironmentRequest
from a13n_service.environments.models import EnvironmentRecord
from a13n_service.environments.retention import active_use_exists, has_active_use
from a13n_service.environments.usage import refresh_run_retention
from a13n_service.interactions.models import RunRecord
from a13n_service.interactions.scheduling import AttemptScheduler, ClaimedAttempt
from a13n_service.storage import short_session, transaction
from a13n_service.temporal import assume_utc
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from tests.hooks.support import hook_actor
from tests.lifecycle_support import test_lifecycle_writer

from .conftest import NOW, WORKSPACE_ID
from .mount_helpers import accepted_mount
from .test_attempt_execution import _accept_root, _worker
from .test_environment_retention import cancel
from .test_websocket_use_authorization import client_environment as client_environment

pytestmark = pytest.mark.anyio


async def test_additional_use_retains_only_its_target_after_first_use(
    interaction_sessions, interaction_object_store, client_environment
):
    service, provider, target = client_environment
    other = await service.create_environment(
        actor=hook_actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="other-computer",
        request=RegisterEnvironmentRequest(
            provider_id=provider.id,
            configuration={},
            device_id="other",
        ),
    )
    _, run, _ = await _accept_root(interaction_sessions, interaction_object_store)
    claim = await AttemptScheduler(
        interaction_sessions, clock=lambda: NOW + timedelta(seconds=1), lifecycle=test_lifecycle_writer()
    ).claim(run.id, _worker())
    assert isinstance(claim, ClaimedAttempt)
    async with transaction(interaction_sessions) as database:
        mount = accepted_mount(run.id, target.id, working_directory="/projects/computer")
        database.add(mount)
        await database.flush()
        assert not await has_active_use(database, target.id)
        mount.use_started_at = NOW
        await database.flush()
        assert await has_active_use(database, target.id)
        assert not await has_active_use(database, other.id)
        # Correlation must retain the outer target, including in maintenance scans.
        assert set(
            await database.scalars(select(EnvironmentRecord.id).where(active_use_exists(EnvironmentRecord.id)))
        ) == {target.id}
        record = await database.get(RunRecord, run.id, with_for_update=True)
        assert record is not None and record.environment_id is None
        await refresh_run_retention(database, run=record, now=NOW)
        assert (await database.get(EnvironmentRecord, target.id)).retention_condition == "active"
        assert (await database.get(EnvironmentRecord, other.id)).retention_condition == "idle"

    ended_at = NOW + timedelta(seconds=3)
    await cancel(interaction_sessions, interaction_object_store, run, ended_at)
    async with short_session(interaction_sessions) as database:
        assert not await has_active_use(database, target.id)
        target_record = await database.get(EnvironmentRecord, target.id)
        assert target_record is not None and target_record.retention_condition == "idle"
        assert assume_utc(target_record.condition_since) == ended_at


@pytest.mark.parametrize(
    "changes",
    [
        {"name": "workspace"},
        {"name": "../computer"},
        {"application_status": "ready"},
        {"applied_attempt_fence": 1},
        {"error": {"code": "unsafe-state"}},
    ],
)
async def test_mount_schema_rejects_invalid_identity_and_observation(
    interaction_sessions, interaction_object_store, client_environment, changes
):
    _, _, target = client_environment
    _, run, _ = await _accept_root(interaction_sessions, interaction_object_store)
    with pytest.raises(IntegrityError):
        async with transaction(interaction_sessions) as database:
            database.add(accepted_mount(run.id, target.id, **changes))
