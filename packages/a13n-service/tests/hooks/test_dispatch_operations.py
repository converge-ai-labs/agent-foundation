"""Operator retry and database invariants use the real migrated schema."""

import anyio
import pytest
from a13n_harness import SafeFailure
from a13n_service.cli import main
from a13n_service.lifecycle.models import LifecycleEventRecord
from a13n_service.settings import Settings
from a13n_service.storage import short_session, transaction
from click.testing import CliRunner
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from tests.hooks.support import seed_run_and_secret
from tests.hooks.test_dispatcher import _emit
from tests.interactions.conftest import NOW, ORGANIZATION_ID

pytestmark = pytest.mark.anyio


async def test_operator_retry_targets_only_failed_dispatch(hook_interaction_sessions, service_database, monkeypatch):
    sessions = hook_interaction_sessions
    await seed_run_and_secret(sessions)
    (event_id,) = await _emit(sessions)
    async with transaction(sessions) as database:
        await database.execute(
            update(LifecycleEventRecord).values(
                hook_dispatch_state="failed",
                hook_dispatch_attempts=10,
                hook_dispatch_next_attempt_at=None,
                hook_dispatch_error_json=SafeFailure(code="test_failure", message="Test").model_dump(mode="json"),
            )
        )
    monkeypatch.setattr(
        "a13n_service.cli._settings",
        lambda: Settings(database={"url": service_database.url.get_secret_value()}),
    )
    monkeypatch.setattr("a13n_service.cli.configure_logging", lambda settings: None)

    async def invoke(organization_id):
        return await anyio.to_thread.run_sync(
            lambda: CliRunner().invoke(
                main, ["hooks", "retry-dispatch", event_id, "--organization-id", organization_id]
            )
        )

    assert (await invoke("org_other")).exit_code == 1
    result = await invoke(ORGANIZATION_ID)
    assert result.exit_code == 0, result.output
    assert event_id in result.output
    assert (await invoke(ORGANIZATION_ID)).exit_code == 1
    async with short_session(sessions) as database:
        event = await database.scalar(select(LifecycleEventRecord))
        assert event.hook_dispatch_state == "pending" and event.hook_dispatch_attempts == 0
        assert event.hook_dispatch_next_attempt_at is not None
        assert event.hook_dispatched_at is None and event.hook_dispatch_error_json is None
        assert event.projection_state == "pending" and event.projection_attempts == 0


@pytest.mark.parametrize(
    "values",
    [
        {"hook_dispatch_attempts": -1},
        {"hook_dispatch_state": "unknown"},
        {"hook_dispatch_state": "done", "hook_dispatch_next_attempt_at": None},
        {"hook_dispatch_state": "failed", "hook_dispatch_next_attempt_at": None},
        {"hook_dispatched_at": NOW},
    ],
)
async def test_migrated_dispatch_shape_rejects_partial_progress(hook_interaction_sessions, values):
    sessions = hook_interaction_sessions
    await seed_run_and_secret(sessions)
    await _emit(sessions)
    with pytest.raises(IntegrityError, match="hook_dispatch"):
        async with transaction(sessions) as database:
            await database.execute(update(LifecycleEventRecord).values(**values))
    async with short_session(sessions) as database:
        event = await database.scalar(select(LifecycleEventRecord))
        assert event.hook_dispatch_state == "pending" and event.hook_dispatch_attempts == 0
