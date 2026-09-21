"""Detached command observations reduce reads without replacing authorization or commit checks."""

from dataclasses import replace

import pytest
from a13n_service.interactions.command_values import ContinueRunCommand
from a13n_service.interactions.control_domain import (
    InterruptRequest,
    ThreadRunSubmissionRequest,
    WaitingResolutionDefaults,
)
from a13n_service.interactions.errors import InteractionCommandError
from a13n_service.interactions.models import RunRecord, ThreadRecord
from a13n_service.interactions.sources import load_run_source, load_thread_source
from a13n_service.storage import short_session, transaction
from sqlalchemy import func, select

from tests.gateway.test_commands import _actor, _complete_run, _request, _wait_run
from tests.gateway.test_queue import _submission_setup
from tests.hooks.support import seed_hook_actor_access
from tests.interactions.conftest import WORKSPACE_ID
from tests.sql_capture import capture_sql

pytestmark = pytest.mark.anyio


async def _prepare_submission(sessions, tmp_path, branch):
    await seed_hook_actor_access(sessions)
    service, commands, objects, source = await _submission_setup(sessions, tmp_path)
    waiting = None
    version = 2
    if branch == "waiting":
        digest = await _wait_run(sessions, objects, run_id=source.run_id)
        waiting = WaitingResolutionDefaults(sealed_state_digest_sha256=digest)
    elif branch == "root":
        await commands.active.interrupt(
            actor=_actor(),
            run_id=source.run_id,
            idempotency_key="cancel",
            request=InterruptRequest(expected_run_version=1, expected_thread_version=1),
        )
    else:
        await _complete_run(sessions, objects, run_id=source.run_id)
        if branch == "preserved_head":
            successor = await commands.runs.continue_from(
                actor=_actor(),
                source_run_id=source.run_id,
                idempotency_key="successor",
                request=ContinueRunCommand(expected_thread_version=2, input=_request().input),
            )
            await commands.active.interrupt(
                actor=_actor(),
                run_id=successor.run_id,
                idempotency_key="cancel-successor",
                request=InterruptRequest(expected_run_version=1, expected_thread_version=3),
            )
            version = 4
    request = ThreadRunSubmissionRequest(
        expected_thread_version=version,
        input=_request("next input").input,
        waiting_resolution=waiting,
    )
    return service, commands, source, request


@pytest.mark.parametrize("branch", ["completed", "waiting", "root", "preserved_head"])
async def test_submission_reuses_one_source_observation(lifecycle_interaction_sessions, tmp_path, branch):
    sessions = lifecycle_interaction_sessions
    service, _, source, request = await _prepare_submission(sessions, tmp_path, branch)
    with capture_sql(sessions) as statements:
        receipt = await service.submit(
            actor=_actor(),
            thread_id=source.thread_id,
            request=request,
            idempotency_key="measured",
        )
    observed = [
        sql
        for sql in statements
        if sql.startswith("SELECT")
        and "threads.current_run_id" in sql
        and "sessions.configuration_owner_user_id" in sql
    ]
    assert len(observed) == 1, observed
    print(f"submission_sql branch={branch} cursor_executions={len(statements)} source_observations={len(observed)}")
    assert receipt.run is not None
    async with short_session(sessions) as database:
        run = await database.get(RunRecord, receipt.run.run_id)
        assert run.parent_run_id == (None if branch == "root" else source.run_id)
        assert run.input_kind == ("waiting_continue" if branch == "waiting" else "agent_input")
    assert (
        await service.submit(
            actor=_actor(),
            thread_id=source.thread_id,
            request=request,
            idempotency_key="measured",
        )
        == receipt
    )


@pytest.mark.parametrize("branch", ["completed", "waiting", "root"])
async def test_observed_submission_still_checks_version_at_commit(
    lifecycle_interaction_sessions,
    tmp_path,
    monkeypatch,
    branch,
):
    sessions = lifecycle_interaction_sessions
    service, _, source, request = await _prepare_submission(sessions, tmp_path, branch)
    admission = service._submission_admission

    async def advance_after_observation(**kwargs):
        observed = await admission(**kwargs)
        async with transaction(sessions) as database:
            thread = await database.get(ThreadRecord, source.thread_id)
            thread.version += 1
        return observed

    monkeypatch.setattr(service, "_submission_admission", advance_after_observation)
    with pytest.raises(InteractionCommandError) as error:
        await service.submit(
            actor=_actor(),
            thread_id=source.thread_id,
            request=request,
            idempotency_key="stale",
        )
    assert error.value.category.value == "conflict"
    async with short_session(sessions) as database:
        assert await database.scalar(select(func.count()).select_from(RunRecord)) == 1
        thread = await database.get(ThreadRecord, source.thread_id)
        assert thread.current_run_id == source.run_id
        assert thread.version == request.expected_thread_version + 1


@pytest.mark.parametrize("kind", ["run", "thread"])
async def test_source_queries_conceal_another_workspace(lifecycle_interaction_sessions, tmp_path, kind):
    sessions = lifecycle_interaction_sessions
    await seed_hook_actor_access(sessions)
    _, _, _, source = await _submission_setup(sessions, tmp_path)
    async with short_session(sessions) as database:
        with pytest.raises(InteractionCommandError) as error:
            if kind == "run":
                await load_run_source(database, workspace_id="ws_other", run_id=source.run_id)
            else:
                await load_thread_source(database, workspace_id="ws_other", thread_id=source.thread_id)
    assert error.value.code == "resource_not_found"


@pytest.mark.parametrize("mismatch", ["workspace", "run"])
async def test_forwarded_source_cannot_retarget_continuation(
    lifecycle_interaction_sessions,
    tmp_path,
    mismatch,
):
    sessions = lifecycle_interaction_sessions
    _, commands, source, request = await _prepare_submission(sessions, tmp_path, "completed")
    async with short_session(sessions) as database:
        observed = await load_run_source(database, workspace_id=WORKSPACE_ID, run_id=source.run_id)
    if mismatch == "workspace":
        observed = replace(observed, session_scope=replace(observed.session_scope, workspace_id="ws_other"))
    with pytest.raises(InteractionCommandError) as error:
        await commands.runs.accept_continuation(
            actor=_actor(),
            source_run_id=source.run_id if mismatch == "workspace" else "run_other",
            observed_source=observed,
            request_key="a" * 64,
            request=ContinueRunCommand(expected_thread_version=request.expected_thread_version, input=request.input),
        )
    assert error.value.code == "resource_not_found"
