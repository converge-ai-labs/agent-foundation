from a13n_environment_provider import EnvironmentState
from a13n_harness import HarnessState
from a13n_service.interactions import (
    CompletedOutcomeCandidate,
    ConsumedThreadInboxEntry,
    DeferredContinuationState,
    HostContinuationState,
    PendingCallKind,
    PendingCallSummary,
    RunInputKind,
    RunLineageKind,
    RunPendingSummary,
    WaitingOutcomeCandidate,
)
from a13n_service.interactions.initialization import (
    RunStateSeed,
    initialize_completed_continuation_state,
    initialize_fork_state,
    initialize_retry_state,
    initialize_start_state,
    initialize_waiting_continuation_state,
)

from .conftest import AGENT_ID, AGENT_REVISION_ID, ATTEMPT_ID, effective_agent_config, initial_state


def _seed(run_id: str = "run_abcdef1234567890") -> RunStateSeed:
    return RunStateSeed(
        run_id=run_id,
        agent_id=AGENT_ID,
        agent_revision_id=AGENT_REVISION_ID,
        effective_agent_config=effective_agent_config(),
    )


def _completed_parent():
    initial = initial_state()
    harness = HarnessState(
        schema_version="1",
        thread_id=initial.thread_id,
        message_history=initial.harness.message_history,
        agent_context_state=initial.harness.agent_context_state,
        environment_states={
            "workspace": EnvironmentState(
                provider_key="test.provider",
                state_version="1",
                state={"target": "durable"},
            )
        },
    )
    payload = initial.model_dump(mode="python", by_alias=True)
    payload.update(
        checkpoint_seq=1,
        checkpoint_kind="completed",
        input_disposition="applied",
        last_checkpoint_run_attempt_id=ATTEMPT_ID,
        last_checkpoint_fence=1,
        harness=harness,
        outcome_candidate=CompletedOutcomeCandidate(output="done"),
    )
    return type(initial).model_validate(payload)


def _waiting_parent():
    initial = initial_state()
    pending = RunPendingSummary(calls=(PendingCallSummary(call_id="approval-1", kind=PendingCallKind.approval),))
    payload = initial.model_dump(mode="python", by_alias=True)
    payload.update(
        checkpoint_seq=1,
        checkpoint_kind="waiting",
        input_disposition="applied",
        last_checkpoint_run_attempt_id=ATTEMPT_ID,
        last_checkpoint_fence=1,
        host=HostContinuationState(
            deferred=DeferredContinuationState(requests={"approvals": {"approval-1": {}}}),
            consumed_inbox_entries=(
                ConsumedThreadInboxEntry(
                    inbox_entry_id="tin_1234567890abcdef",
                    kind="steer",
                ),
            ),
        ),
        outcome_candidate=WaitingOutcomeCandidate(
            wait_reason="approval",
            pending=pending,
        ),
    )
    return type(initial).model_validate(payload)


def test_start_and_completed_continue_allocate_only_the_required_identity() -> None:
    started = initialize_start_state(_seed())
    parent = _completed_parent()
    continued = initialize_completed_continuation_state(_seed(), parent)

    assert started.thread_id.startswith("thread-")
    assert continued.thread_id == parent.thread_id
    assert continued.harness.environment_states == parent.harness.environment_states
    assert continued.host == HostContinuationState()
    assert continued.outcome_candidate is None


def test_waiting_continue_preserves_deferred_values_but_not_parent_receipts() -> None:
    parent = _waiting_parent()

    continued = initialize_waiting_continuation_state(_seed(), parent)

    assert continued.thread_id == parent.thread_id
    assert continued.host.deferred == parent.host.deferred
    assert continued.host.consumed_inbox_entries == ()


def test_fork_and_fork_retry_clear_environment_and_use_the_correct_thread_identity() -> None:
    parent = _completed_parent()
    forked = initialize_fork_state(_seed(), parent)
    retry_thread_id = "thread-abcdefabcdefabcdefabcdefabcdefab"
    retried = initialize_retry_state(
        _seed("run_fedcba0987654321"),
        thread_id=retry_thread_id,
        source_lineage_kind=RunLineageKind.fork,
        source_input_kind=RunInputKind.agent_input,
        parent=parent,
    )

    assert forked.thread_id != parent.thread_id
    assert forked.harness.environment_states == {}
    assert retried.thread_id == retry_thread_id
    assert retried.harness.environment_states == {}
