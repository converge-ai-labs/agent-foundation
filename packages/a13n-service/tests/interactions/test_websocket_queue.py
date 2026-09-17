from __future__ import annotations

import pytest
from a13n_service.environments.domain import ExistingEnvironmentSelection
from a13n_service.environments.errors import EnvironmentManagementError
from a13n_service.environments.websocket.coordination import ConnectionCoordination
from a13n_service.interactions.acceptance import RunAcceptanceService
from a13n_service.interactions.control_domain import QueuedSubmissionState, ThreadRunSubmissionIntent
from a13n_service.interactions.initialization import RunStateSeed, initialize_empty_thread_state
from a13n_service.interactions.input import AcceptedAgentInput, AgentInput, TextContent
from a13n_service.interactions.models import RunRecord, ThreadRecord
from a13n_service.interactions.objects import RunPayloadStore, RunStateStore
from a13n_service.interactions.queue import QueuedSubmissionStore
from a13n_service.storage import short_session

from tests.environments.websocket.conftest import relay_redis as relay_redis
from tests.lifecycle_support import test_lifecycle_writer
from tests.memory.selection_support import ordinary_memory

from .conftest import AGENT_ID, AGENT_REVISION_ID, NOW, ORGANIZATION_ID, effective_agent_config
from .test_acceptance import _accepted_run
from .test_attempt_execution import _accept_root
from .test_queue import _fail_current_run, _inline_hooks, _principal
from .test_websocket_acceptance import _connect
from .test_websocket_use_authorization import client_environment as client_environment

pytestmark = pytest.mark.anyio


async def test_queue_admission_waits_for_online_at_consumption_and_retains_original_replay(
    interaction_sessions, interaction_object_store, client_environment, relay_redis
):
    _, _, environment = client_environment
    _, source, _ = await _accept_root(interaction_sessions, interaction_object_store)
    hooks = _inline_hooks()
    queue = QueuedSubmissionStore(interaction_sessions, hooks, clock=lambda: NOW)
    queued = await queue.enqueue(
        organization_id=ORGANIZATION_ID,
        thread_id=source.thread_id,
        expected_thread_version=1,
        authority_principal=_principal(),
        submission=ThreadRunSubmissionIntent(
            input=AgentInput(schema_version="1", content=(TextContent(text="queued"),)),
            environment=ExistingEnvironmentSelection(environment_id=environment.id),
        ),
        queued_submission_id="qsub_5555555555555555",
    )
    await _fail_current_run(interaction_sessions, run_id=source.id, thread_id=source.thread_id)
    accepted_input = AcceptedAgentInput(schema_version="1", content=(TextContent(text="queued"),))
    config = effective_agent_config()
    state = initialize_empty_thread_state(
        RunStateSeed(
            run_id="run_6666666666666666",
            agent_id=AGENT_ID,
            agent_revision_id=AGENT_REVISION_ID,
            effective_agent_config=config,
        ),
        thread_id=source.thread_id,
    )
    run = _accepted_run(
        run_id=state.run_id,
        thread_id=source.thread_id,
        idempotency_key="consume-client",
        request_fingerprint="6" * 64,
        config=config,
    ).model_copy(update={"input": accepted_input.model_dump(mode="json", by_alias=True, exclude_none=True)})
    coordination = ConnectionCoordination(relay_redis)
    service = RunAcceptanceService(
        interaction_sessions,
        RunStateStore(interaction_object_store),
        RunPayloadStore(interaction_object_store),
        hooks,
        bindings=ordinary_memory(interaction_sessions),
        lifecycle=test_lifecycle_writer(),
        coordination=coordination,
        clock=lambda: NOW,
    )

    async def consume():
        return await service.consume_queued(
            run=run,
            state=state,
            queued_submission_id=queued.queued_submission.queued_submission_id,
            submission_digest_sha256=queued.queued_submission.submission_digest_sha256,
            accepted_input=accepted_input,
            expected_thread_version=2,
            expected_queue_version=1,
            expected_current_run_id=source.id,
            expected_head_run_id=None,
            next_head_run_id=None,
        )

    with pytest.raises(EnvironmentManagementError) as caught:
        await consume()
    assert caught.value.code == "environment_unavailable"
    async with short_session(interaction_sessions) as database:
        assert await database.get(RunRecord, run.id) is None
        thread = await database.get(ThreadRecord, source.thread_id)
        assert (thread.version, thread.queue_version, thread.current_run_id) == (2, 1, source.id)
    pending = await queue.get(
        organization_id=ORGANIZATION_ID, queued_submission_id=queued.queued_submission.queued_submission_id
    )
    assert pending.state == QueuedSubmissionState.queued
    connection = await _connect(coordination, environment.id)
    receipt = await consume()
    assert receipt.outcome == "run_accepted"
    assert receipt.queued_submission.state == QueuedSubmissionState.consumed
    async with short_session(interaction_sessions) as database:
        assert (await database.get(RunRecord, run.id)).environment_id == environment.id
    await coordination.retire(connection)
    assert await consume() == receipt
