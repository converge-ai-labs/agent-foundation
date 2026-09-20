from __future__ import annotations

import pytest
from a13n_service.environments.domain import NewEnvironmentSelection
from a13n_service.environments.models import EnvironmentRecord
from a13n_service.gateway.notifications import NotificationService, NotificationSubscription
from a13n_service.gateway.queries import NativeInteractionQueries
from a13n_service.interactions.command_values import ForkRunCommand
from a13n_service.interactions.control_domain import ThreadRunSubmissionRequest
from a13n_service.interactions.models import RunRecord, ThreadRecord
from a13n_service.interactions.queue import QueuedSubmissionStore
from a13n_service.interactions.submissions import QueuedSubmissionService
from a13n_service.interactions.thread_creation import allocate_thread
from a13n_service.interactions.thread_domain import CreateThreadRequest
from a13n_service.run_stream import RunDisplayStore
from a13n_service.storage import short_session
from a13n_service.storage.object_store import LocalObjectStore
from sqlalchemy import func, select

from tests.gateway.test_commands import _actor, _commands, _complete_run, _Freezing, _frozen, _Preparation, _request
from tests.interactions.conftest import AGENT_ID, NOW, WORKSPACE_ID
from tests.interactions.test_acceptance import _inline_hooks
from tests.interactions.test_environment_runtime import template_config

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("selection", ["default", "none", "template"])
async def test_start_environment_choice_replays_without_duplicate_allocation(
    lifecycle_interaction_sessions, tmp_path, selection
):
    _, template, _ = await template_config(lifecycle_interaction_sessions, tmp_path, "on_use")
    objects = await LocalObjectStore.create(tmp_path / "gateway-objects")
    commands = _commands(lifecycle_interaction_sessions, objects, _Preparation(), _Freezing([_frozen()]))
    request = _request()
    if selection != "default":
        request = request.model_copy(
            update={"environment": None if selection == "none" else NewEnvironmentSelection(template_id=template.id)}
        )
    accepted = await commands.runs.start(
        actor=_actor(), workspace_id=WORKSPACE_ID, idempotency_key="start", request=request
    )
    repeated = await commands.runs.start(
        actor=_actor(), workspace_id=WORKSPACE_ID, idempotency_key="start", request=request
    )
    assert repeated == accepted
    async with short_session(lifecycle_interaction_sessions) as database:
        run = await database.get(RunRecord, accepted.run_id)
        thread = await database.get(ThreadRecord, accepted.thread_id)
        assert (run.environment_id is None) == (selection == "none")
        assert thread.default_environment_id == run.environment_id
        assert await database.scalar(select(func.count()).select_from(EnvironmentRecord)) == (
            0 if selection == "none" else 1
        )
        if run.environment_id:
            environment = await database.get(EnvironmentRecord, run.environment_id)
            assert environment.status == "unprepared"
    altered = request.model_copy(update={"environment": None}) if selection == "default" else _request()
    assert (
        await commands.runs.start(actor=_actor(), workspace_id=WORKSPACE_ID, idempotency_key="start", request=altered)
        == accepted
    )


async def test_empty_thread_is_readable_and_accepts_first_input_with_explicit_null_environment(
    lifecycle_interaction_sessions, tmp_path
):
    service, _, _ = await template_config(lifecycle_interaction_sessions, tmp_path, "on_use")
    thread = await allocate_thread(
        service.sessions,
        actor=_actor(),
        workspace_id=WORKSPACE_ID,
        body=CreateThreadRequest(agent_id=AGENT_ID, environment=None),
        idempotency_key="empty-thread",
    )
    async with short_session(service.sessions) as database:
        persisted = await database.get(ThreadRecord, thread.id)
        assert (persisted.next_delivery_sequence, persisted.pending_count, persisted.pending_bytes) == (1, 0, 0)
    objects = await LocalObjectStore.create(tmp_path / "gateway-objects")
    commands = _commands(lifecycle_interaction_sessions, objects, _Preparation(), _Freezing([_frozen()]))
    queries = NativeInteractionQueries(lifecycle_interaction_sessions, RunDisplayStore(objects))
    notifications = NotificationService(lifecycle_interaction_sessions)
    (subscription,) = await notifications.authorize(
        actor=_actor(),
        subscriptions=(
            NotificationSubscription(
                subscription_id="empty", scope="thread", resource_id=thread.id, topics=("run.updated",)
            ),
        ),
    )
    assert (await queries.get_thread(actor=_actor(), thread_id=thread.id)).current_run_id is None
    assert [
        item.id
        for item in (
            await queries.list_threads(actor=_actor(), session_id=thread.session_id, limit=20, cursor=None)
        ).items
    ] == [thread.id]
    assert (
        await queries.list_runs(actor=_actor(), workspace_id=None, thread_id=thread.id, limit=20, cursor=None)
    ).items == ()
    queue = QueuedSubmissionService(
        lifecycle_interaction_sessions,
        QueuedSubmissionStore(lifecycle_interaction_sessions, _inline_hooks(), clock=lambda: NOW),
        commands,
        clock=lambda: NOW,
    )
    request = ThreadRunSubmissionRequest(expected_thread_version=1, agent_id=AGENT_ID, input=_request().input)
    receipt = await queue.submit(actor=_actor(), thread_id=thread.id, request=request, idempotency_key="first")
    assert receipt.run is not None
    assert (await notifications.read(subscription, limit=10))[0].run_id == receipt.run.run_id
    assert await queue.submit(actor=_actor(), thread_id=thread.id, request=request, idempotency_key="first") == receipt
    async with short_session(lifecycle_interaction_sessions) as database:
        run = await database.get(RunRecord, receipt.run.run_id)
        assert run.environment_id is None
        assert run.thread_id == thread.id


async def test_explicit_null_successor_and_fork_preserve_environment_selection(
    lifecycle_interaction_sessions, tmp_path
):
    await template_config(lifecycle_interaction_sessions, tmp_path, "on_use")
    objects = await LocalObjectStore.create(tmp_path / "gateway-objects")
    commands = _commands(lifecycle_interaction_sessions, objects, _Preparation(), _Freezing([_frozen()]))
    source = await commands.runs.start(
        actor=_actor(), workspace_id=WORKSPACE_ID, idempotency_key="source", request=_request()
    )
    await _complete_run(lifecycle_interaction_sessions, objects, run_id=source.run_id)
    request = ForkRunCommand(input=_request().input, environment=None)
    fork = await commands.runs.fork(actor=_actor(), run_id=source.run_id, idempotency_key="fork", request=request)
    assert (
        await commands.runs.fork(actor=_actor(), run_id=source.run_id, idempotency_key="fork", request=request) == fork
    )
    async with short_session(lifecycle_interaction_sessions) as database:
        assert (await database.get(RunRecord, source.run_id)).environment_id is not None
        assert (await database.get(RunRecord, fork.run_id)).environment_id is None
