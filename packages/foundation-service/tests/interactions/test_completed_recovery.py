from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest
from a13n_harness import (
    AgentDefinition,
    AgentIdentityRef,
    AgentInstanceContext,
    AgentSpec,
    HarnessBuilder,
    HarnessState,
    SafeFailure,
)
from a13n_service.environments.lifecycle import EnvironmentLifecycle
from a13n_service.interactions.attempt_executor import RunAttemptExecutor
from a13n_service.interactions.attempts import (
    AttemptAuthorityError,
    AttemptExecutionService,
    AttemptMutationError,
    AttemptPreparationAccepted,
)
from a13n_service.interactions.control_models import ThreadInboxRecord
from a13n_service.interactions.harness_results import RunTerminalDisposition, StoredHarnessOutcomeAdapter
from a13n_service.interactions.harness_runtime import (
    HarnessCollaborators,
    HarnessDriver,
    HarnessInvocation,
    ImmediateHarnessInput,
)
from a13n_service.interactions.inbox import DatabaseThreadInboxReconciler, ThreadInboxStore
from a13n_service.interactions.input import AcceptedAgentInput, TextContent
from a13n_service.interactions.models import RunAttemptRecord, RunRecord
from a13n_service.interactions.objects import RunPayloadStore, StaleStateWriter
from a13n_service.interactions.outcomes import RunOutcomeService
from a13n_service.interactions.run_control import RunAttemptControl
from a13n_service.interactions.scheduling import AttemptScheduler, ClaimedAttempt
from a13n_service.interactions.state import ConsumedThreadInboxEntry, HostContinuationState
from a13n_service.interactions.terminal import DatabaseRunTerminalCommitter
from a13n_service.storage import short_session
from anyio import sleep_forever
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel

from .conftest import NOW, TENANT_ID
from .test_attempt_execution import _accept_root, _authority, _completed_state, _worker
from .test_attempt_executor import _Projector

pytestmark = pytest.mark.anyio

CONSUMED_ID = "inb_1111111111111111"
PENDING_ID = "inb_2222222222222222"


async def _append(sessions, run_id, entry_id, text):
    return await ThreadInboxStore(sessions, clock=lambda: NOW).append_steer(
        tenant_id=TENANT_ID,
        run_id=run_id,
        entry_id=entry_id,
        input=AcceptedAgentInput(schema_version="1", content=(TextContent(text=text),)),
    )


async def _published_completion(sessions, objects, *, pending=True, budget=3):
    states, run, initial = await _accept_root(sessions, objects, max_recovery_attempts=budget)
    claim = await AttemptScheduler(sessions, clock=lambda: NOW).claim(run.id, _worker())
    assert isinstance(claim, ClaimedAttempt)
    execution = AttemptExecutionService(sessions, clock=lambda: NOW)
    authority = _authority(claim)
    preparation = await execution.commit_preparation_success(authority)
    assert isinstance(preparation, AttemptPreparationAccepted)
    entered = await execution.enter_harness(authority, preparation=preparation, harness_run_id="harness-first")
    authority = _authority(claim, run_version=entered.run_version, attempt_version=entered.attempt_version)
    await _append(sessions, run.id, CONSUMED_ID, "already included")
    candidate = _completed_state(initial, claim.attempt.id, claim.attempt.fence).model_copy(
        update={
            "harness": HarnessState.new(
                thread_id=run.thread_id,
                message_history=[
                    ModelRequest(parts=[UserPromptPart(content="original request")]),
                    ModelRequest(parts=[UserPromptPart(content="already included")]),
                    ModelResponse(parts=[TextPart(content="previous result")]),
                ],
            ),
            "host": HostContinuationState(
                consumed_inbox_entries=(ConsumedThreadInboxEntry(inbox_entry_id=CONSUMED_ID, kind="steer"),)
            ),
        }
    )
    state = await execution.publish_checkpoint(authority, states, await states.read(TENANT_ID, run.id), candidate)
    if pending:
        await _append(sessions, run.id, PENDING_ID, "follow-up request")
    committer = DatabaseRunTerminalCommitter(
        sessions,
        RunOutcomeService(sessions, RunPayloadStore(objects), clock=lambda: NOW),
        execution,
        clock=lambda: NOW,
    )
    return states, run, authority, state, execution, committer


@pytest.mark.parametrize("budget", [1, 3])
async def test_completion_race_recovers_same_run_and_preserves_history(
    interaction_sessions, interaction_object_store, monkeypatch, budget
):
    states, run, first, candidate, execution, committer = await _published_completion(
        interaction_sessions, interaction_object_store, budget=budget
    )
    receipt = await committer.commit_state_outcome(first, candidate)
    async with short_session(interaction_sessions) as database:
        predecessor = await database.get(RunAttemptRecord, first.run_attempt_id)
        assert predecessor.status == "failed"
        assert predecessor.failure_json["code"] == "completion_blocked_by_pending_delivery"
        current_run = await database.get(RunRecord, run.id)
        assert current_run.sealed_state_digest_sha256 is None
        assert current_run.current_run_attempt_id is None
        if budget == 1:
            assert receipt.disposition is RunTerminalDisposition.failed
            assert current_run.status == "failed"
            assert (await database.get(ThreadInboxRecord, PENDING_ID)).status == "superseded"
            return
        assert receipt.disposition is RunTerminalDisposition.retrying
        assert current_run.status == "running"

    claim = await AttemptScheduler(interaction_sessions, clock=lambda: NOW).claim(run.id, _worker(worker_id="worker-2"))
    assert isinstance(claim, ClaimedAttempt)
    second = _authority(claim)
    preparation = await execution.commit_preparation_success(second)
    assert isinstance(preparation, AttemptPreparationAccepted)
    claimed = await execution.claim_state_writer(second, states, candidate)
    with pytest.raises(AttemptAuthorityError):
        await execution.claim_state_writer(first, states, claimed)
    with pytest.raises(StaleStateWriter):
        await states.claim_writer(candidate, fence=first.fence)

    materialized = []

    async def materialize(entry):
        materialized.append(entry.id)
        assert entry.id == PENDING_ID
        return "follow-up request"

    model_calls = []

    async def model(messages, info):
        del info
        model_calls.append(messages)
        yield "new result"

    control = RunAttemptControl(
        context=second,
        execution=execution,
        states=states,
        state=claimed,
        inbox=DatabaseThreadInboxReconciler(interaction_sessions, materialize, clock=lambda: NOW),
    )
    driver = HarnessDriver(HarnessBuilder(instrumentation=None), control=control, projector=_Projector())
    preparer = Mock()
    preparer.prepare = AsyncMock(
        return_value=HarnessInvocation(
            definition=AgentDefinition(agent=AgentSpec(), output_type=str, model=FunctionModel(stream_function=model)),
            input=ImmediateHarnessInput("MUST NOT REPLAY"),
            collaborators=HarnessCollaborators(
                instance=AgentInstanceContext(
                    identity=AgentIdentityRef(issuer="foundation", subject="test-user"),
                    agent_instance_id="instance-1",
                    actor="user:test-user",
                    host_refs={"session_id": run.session_id},
                )
            ),
        )
    )
    wakeups = Mock()
    wakeups.receive = sleep_forever
    cleanup = Mock()
    cleanup.close = AsyncMock()
    capacity = Mock()

    async def prepare_environment(lifecycle, context):
        del lifecycle
        assert context.run_attempt_id == second.run_attempt_id
        progress = await states.read(TENANT_ID, run.id)
        assert progress.envelope.harness == candidate.envelope.harness
        assert progress.envelope.host == candidate.envelope.host
        assert progress.envelope.effective_agent_config == candidate.envelope.effective_agent_config
        assert progress.envelope.input_disposition == "applied"
        assert progress.envelope.outcome_candidate is None
        assert progress.envelope.checkpoint_kind == "progress"
        assert progress.envelope.checkpoint_seq == candidate.envelope.checkpoint_seq + 1
        assert progress.envelope.last_checkpoint_run_attempt_id == second.run_attempt_id
        assert progress.envelope.last_checkpoint_fence == progress.writer_fence == second.fence
        async with short_session(interaction_sessions) as database:
            assert (await database.get(ThreadInboxRecord, CONSUMED_ID)).status == "consumed"
            assert (await database.get(ThreadInboxRecord, PENDING_ID)).status == "pending"

    environment_preparer = AsyncMock(side_effect=prepare_environment)
    monkeypatch.setattr("a13n_service.interactions.attempt_executor.prepare_run_environment", environment_preparer)
    result = await RunAttemptExecutor(
        context=second,
        control=control,
        driver=driver,
        preparer=preparer,
        wakeups=wakeups,
        adapter=StoredHarnessOutcomeAdapter(
            tenant_id=TENANT_ID,
            run_id=run.id,
            payloads=RunPayloadStore(interaction_object_store),
            max_output_bytes=4096,
            inline_output_bytes=4096,
        ),
        committer=committer,
        cleanup=cleanup,
        capacity_slot=capacity,
        environments=Mock(spec=EnvironmentLifecycle),
    ).run()
    assert result.disposition is RunTerminalDisposition.completed
    environment_preparer.assert_awaited_once()
    assert materialized == [PENDING_ID]
    assert len(model_calls) == 1
    prompts = [part.content for message in model_calls[0] for part in message.parts if isinstance(part, UserPromptPart)]
    expected_prompts = ["original request", "already included", "follow-up request"]
    assert [prompt for prompt in prompts if prompt in expected_prompts] == expected_prompts
    assert "MUST NOT REPLAY" not in prompts
    assert any(
        isinstance(part, TextPart) and part.content == "previous result"
        for message in model_calls[0]
        for part in message.parts
    )
    final_state = await states.read(TENANT_ID, run.id)
    assert [receipt.inbox_entry_id for receipt in final_state.envelope.host.consumed_inbox_entries] == [
        CONSUMED_ID,
        PENDING_ID,
    ]
    async with short_session(interaction_sessions) as database:
        final_run = await database.get(RunRecord, run.id)
        assert final_run.status == "completed"
        assert final_run.attempts_started == 2
        assert final_run.sealed_state_digest_sha256 == final_state.digest_sha256
        assert (await database.get(ThreadInboxRecord, PENDING_ID)).status == "consumed"
    cleanup.close.assert_awaited_once()
    capacity.release.assert_called_once()


async def test_completed_recovery_rejects_missing_preparation_claim_and_sealed_run(
    interaction_sessions, interaction_object_store
):
    states, run, first, candidate, execution, committer = await _published_completion(
        interaction_sessions, interaction_object_store, pending=False
    )
    preparation = await execution.commit_preparation_success(first)
    assert isinstance(preparation, AttemptPreparationAccepted)
    with pytest.raises(AttemptMutationError):
        await execution.resume_completed_candidate(first, states, candidate, preparation=preparation)
    with pytest.raises(ValueError, match="replacement Attempt"):
        await states.resume_completed(candidate, run_attempt_id=first.run_attempt_id, fence=first.fence)

    await execution.fail(first, SafeFailure(code="retry", message="Retry."), retryable=True)
    claim = await AttemptScheduler(interaction_sessions, clock=lambda: NOW).claim(run.id, _worker(worker_id="worker-2"))
    assert isinstance(claim, ClaimedAttempt)
    second = _authority(claim)
    second_preparation = await execution.commit_preparation_success(second)
    assert isinstance(second_preparation, AttemptPreparationAccepted)
    with pytest.raises(AttemptMutationError):
        await execution.resume_completed_candidate(second, states, candidate, preparation=second_preparation)
    claimed = await execution.claim_state_writer(second, states, candidate)
    with pytest.raises(AttemptMutationError):
        await execution.resume_completed_candidate(second, states, claimed, preparation=preparation)
    assert (
        await execution.resume_completed_candidate(second, states, claimed, preparation=second_preparation) == claimed
    )
    sealed = await committer.commit_state_outcome(second, claimed, preparation=second_preparation)
    assert sealed.disposition is RunTerminalDisposition.completed
    with pytest.raises(AttemptAuthorityError):
        await execution.resume_completed_candidate(second, states, claimed, preparation=second_preparation)
    assert await states.read(TENANT_ID, run.id) == claimed


@pytest.mark.parametrize("race", ["new_writer", "lost_response"])
async def test_completed_recovery_uses_exact_cas_and_never_retries_unknown_write(
    interaction_sessions, interaction_object_store, monkeypatch, race
):
    states, run, first, candidate, execution, committer = await _published_completion(
        interaction_sessions, interaction_object_store
    )
    await committer.commit_state_outcome(first, candidate)
    claim = await AttemptScheduler(interaction_sessions, clock=lambda: NOW).claim(run.id, _worker(worker_id="worker-2"))
    assert isinstance(claim, ClaimedAttempt)
    second = _authority(claim)
    preparation = await execution.commit_preparation_success(second)
    assert isinstance(preparation, AttemptPreparationAccepted)
    claimed = await execution.claim_state_writer(second, states, candidate)
    original_resume = states.resume_completed
    calls = []

    async def racing_resume(state, **kwargs):
        calls.append(state.info.version)
        if race == "new_writer":
            await states.claim_writer(state, fence=second.fence + 1)
        result = await original_resume(state, **kwargs)
        if race == "lost_response":
            raise TimeoutError("Object response was lost after commit")
        return result

    monkeypatch.setattr(states, "resume_completed", racing_resume)
    with pytest.raises(StaleStateWriter if race == "new_writer" else TimeoutError):
        await execution.resume_completed_candidate(second, states, claimed, preparation=preparation)
    assert calls == [claimed.info.version]
    await execution.validate(second)
    observed = await states.read(TENANT_ID, run.id)
    assert observed.envelope.harness == candidate.envelope.harness
    assert observed.envelope.host == candidate.envelope.host
    if race == "new_writer":
        assert observed.writer_fence == second.fence + 1
        assert observed.envelope.checkpoint_kind == "completed"
        assert observed.envelope.checkpoint_seq == candidate.envelope.checkpoint_seq
    else:
        assert observed.writer_fence == second.fence
        assert observed.envelope.checkpoint_kind == "progress"
        assert observed.envelope.checkpoint_seq == candidate.envelope.checkpoint_seq + 1
    async with short_session(interaction_sessions) as database:
        assert (await database.get(ThreadInboxRecord, PENDING_ID)).status == "pending"
