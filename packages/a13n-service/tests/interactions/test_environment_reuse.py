"""A stopped working Environment can be reused by another Session without losing files."""

from datetime import timedelta

import pytest
from a13n_harness import SafeFailure
from a13n_service.environments.domain import EnvironmentCommandRequest
from a13n_service.environments.runtime import prepare_run_environment
from a13n_service.interactions.models import RunRecord, ThreadRecord
from a13n_service.interactions.objects import RunPayloadStore
from a13n_service.interactions.outcomes import RunOutcomeService
from a13n_service.interactions.scheduling import AttemptScheduler
from a13n_service.storage import short_session

from tests.hooks.support import hook_actor
from tests.lifecycle_support import test_lifecycle_writer

from .conftest import NOW, ORGANIZATION_ID
from .test_attempt_execution import _accept_root, _authority, _worker
from .test_environment_runtime import template_config
from .worker_helpers import prepare_permissions

pytestmark = pytest.mark.anyio


async def test_second_session_reads_original_file_after_stop_and_resume(
    interaction_sessions, interaction_object_store, tmp_path
):
    sessions = interaction_sessions
    service, _, lifecycle = await template_config(sessions, tmp_path, "on_use")
    _, first, _ = await _accept_root(sessions, interaction_object_store)
    scheduler = AttemptScheduler(sessions, clock=lambda: NOW + timedelta(seconds=1), lifecycle=test_lifecycle_writer())
    claim = await scheduler.claim(first.id, _worker())
    first_environment = await prepare_run_environment(
        lifecycle, await prepare_permissions(sessions, first, _authority(claim))
    )
    await first_environment.enter(mount_id="workspace")
    await first_environment.ensure_ready(frozenset({"files"}))
    await first_environment.operations.files.write_text("/original.txt", "Session A's research", mode="create")
    environment_id = first_environment.environment_id
    await first_environment.close()
    before = await service.get_environment(actor=hook_actor(), resource_id=environment_id)
    assert before.status == "running" and before.retention_condition == "active"

    async with short_session(sessions) as session:
        run = await session.get(RunRecord, first.id)
        thread = await session.get(ThreadRecord, first.thread_id)
        run_version, thread_version = run.version, thread.version
    await RunOutcomeService(
        sessions,
        RunPayloadStore(interaction_object_store),
        clock=lambda: NOW + timedelta(seconds=3),
        lifecycle=test_lifecycle_writer(),
    ).cancel(
        organization_id=ORGANIZATION_ID,
        run_id=first.id,
        expected_run_version=run_version,
        expected_thread_version=thread_version,
        failure=SafeFailure(code="cancelled_by_user", message="Session A finished using the Environment."),
    )
    idle = await service.get_environment(actor=hook_actor(), resource_id=environment_id)
    assert idle.retention_condition == "idle"
    command = await service.request_command(
        actor=hook_actor(),
        environment_id=environment_id,
        request=EnvironmentCommandRequest(action="stop"),
        idempotency_key="stop-after-session-a",
    )
    assert command.status == "pending"
    await lifecycle.maintain(environment_id)
    assert (await service.get_command(actor=hook_actor(), command_id=command.id)).status == "completed"
    stopped = await service.get_environment(actor=hook_actor(), resource_id=environment_id)
    assert stopped.status == "stopped" and stopped.generation == before.generation

    _, second, _ = await _accept_root(
        sessions,
        interaction_object_store,
        session_id="session_reuse123456789012",
        thread_id="thread_reuse123456789012",
        run_id="run_reuse123456789012",
        environment_id=environment_id,
    )
    assert second.session_id != first.session_id
    claim = await scheduler.claim(second.id, _worker())
    second_environment = await prepare_run_environment(
        lifecycle, await prepare_permissions(sessions, second, _authority(claim))
    )
    await second_environment.enter(mount_id="workspace")
    await second_environment.ensure_ready(frozenset({"files"}))
    assert (await second_environment.operations.files.read_text("/original.txt")).text == "Session A's research"
    resumed = await service.get_environment(actor=hook_actor(), resource_id=environment_id)
    assert resumed.id == before.id and resumed.status == "running" and resumed.generation == before.generation
    await second_environment.close()
