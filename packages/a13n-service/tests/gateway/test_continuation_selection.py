"""Continuation reuses transaction-local Run facts without changing lineage."""

import pytest
from a13n_service.interactions.command_values import ContinueRunCommand
from a13n_service.interactions.errors import InteractionCommandError
from a13n_service.interactions.models import RunRecord, ThreadRecord
from a13n_service.storage import short_session
from a13n_service.storage.object_store import LocalObjectStore

from tests.gateway.test_commands import (
    _actor,
    _commands,
    _complete_run,
    _Freezing,
    _frozen,
    _Preparation,
    _request,
)
from tests.hooks.support import seed_hook_actor_access
from tests.interactions.conftest import WORKSPACE_ID
from tests.sql_capture import capture_sql

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("historical", [False, True], ids=["current-source", "historical-source"])
async def test_continue_reads_each_advancement_run_once(lifecycle_interaction_sessions, tmp_path, historical):
    sessions = lifecycle_interaction_sessions
    await seed_hook_actor_access(sessions)
    objects = await LocalObjectStore.create(tmp_path / "objects")
    commands = _commands(sessions, objects, _Preparation(), _Freezing([_frozen()]))
    source = await commands.runs.start(
        actor=_actor(), workspace_id=WORKSPACE_ID, idempotency_key="source", request=_request()
    )
    await _complete_run(sessions, objects, run_id=source.run_id)
    version = 2
    if historical:
        latest = await commands.runs.continue_from(
            actor=_actor(),
            source_run_id=source.run_id,
            idempotency_key="latest",
            request=ContinueRunCommand(expected_thread_version=version, input=_request().input),
        )
        await _complete_run(sessions, objects, run_id=latest.run_id)
        version = 4

    request = ContinueRunCommand(expected_thread_version=version, input=_request("continue source").input)
    with capture_sql(sessions) as statements:
        accepted = await commands.runs.continue_from(
            actor=_actor(), source_run_id=source.run_id, idempotency_key="selected-source", request=request
        )

    async with short_session(sessions) as database:
        run = await database.get(RunRecord, accepted.run_id)
        thread = await database.get(ThreadRecord, accepted.thread_id)
        assert run is not None and thread is not None
        assert run.parent_run_id == thread.head_run_id == source.run_id
        assert thread.current_run_id == run.id
        assert thread.version == accepted.thread_version == version + 1
        assert run.status == "accepted"
    replay = await commands.runs.continue_from(
        actor=_actor(), source_run_id=source.run_id, idempotency_key="selected-source", request=request
    )
    assert replay == accepted

    # The preparation JOIN is separate; these are the commit-time point reads.
    run_reads = [
        sql
        for sql in statements
        if "FROM runs WHERE runs.organization_id =" in " ".join(sql.split())
        and "JOIN" not in sql
        and sql.startswith("SELECT runs.")
    ]
    assert len(run_reads) == (2 if historical else 1)


@pytest.mark.parametrize("conflict", ["active-current", "stale-version"])
async def test_historical_continue_preserves_thread_preconditions(lifecycle_interaction_sessions, tmp_path, conflict):
    sessions = lifecycle_interaction_sessions
    await seed_hook_actor_access(sessions)
    objects = await LocalObjectStore.create(tmp_path / "objects")
    commands = _commands(sessions, objects, _Preparation(), _Freezing([_frozen()]))
    source = await commands.runs.start(
        actor=_actor(), workspace_id=WORKSPACE_ID, idempotency_key="source", request=_request()
    )
    await _complete_run(sessions, objects, run_id=source.run_id)
    latest = await commands.runs.continue_from(
        actor=_actor(),
        source_run_id=source.run_id,
        idempotency_key="latest",
        request=ContinueRunCommand(expected_thread_version=2, input=_request().input),
    )
    if conflict == "stale-version":
        await _complete_run(sessions, objects, run_id=latest.run_id)

    with pytest.raises(InteractionCommandError) as rejected:
        await commands.runs.continue_from(
            actor=_actor(),
            source_run_id=source.run_id,
            idempotency_key="conflicting-continuation",
            request=ContinueRunCommand(expected_thread_version=3, input=_request().input),
        )
    assert rejected.value.code == ("thread_busy" if conflict == "active-current" else "thread_version_conflict")
    async with short_session(sessions) as database:
        thread = await database.get(ThreadRecord, source.thread_id)
        assert thread is not None
        assert thread.current_run_id == latest.run_id
        assert thread.head_run_id == (source.run_id if conflict == "active-current" else latest.run_id)
        assert thread.version == (3 if conflict == "active-current" else 4)
