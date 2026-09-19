import pytest
from a13n_harness import HarnessState
from a13n_harness.providers.environment.models import EnvironmentState
from a13n_service.interactions.domain import (
    PendingCallKind,
    PendingCallSummary,
    RunInputKind,
    RunLineageKind,
    RunPendingSummary,
)
from a13n_service.interactions.initialization import (
    RunStateSeed,
    initialize_completed_continuation_state,
    initialize_fork_state,
    initialize_retry_state,
    initialize_start_state,
    initialize_waiting_continuation_state,
)
from a13n_service.interactions.state import (
    CompletedOutcomeCandidate,
    DeferredContinuationState,
    HostContinuationState,
    InboxReceipt,
    WaitingOutcomeCandidate,
)
from pydantic_ai.usage import UsageLimits

from .conftest import AGENT_ID, AGENT_REVISION_ID, ATTEMPT_ID, THREAD_ID, effective_agent_config, initial_state


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
                provider_key="test_provider",
                state_version="1",
                state={"target": "durable"},
            )
        },
    )
    payload = initial.model_dump(mode="python", by_alias=True)
    payload.update(
        checkpoint_seq=1,
        checkpoint_kind="completed",
        last_checkpoint_run_attempt_id=ATTEMPT_ID,
        last_checkpoint_fence=1,
        harness=harness,
        outcome_candidate=CompletedOutcomeCandidate(output="done"),
    )
    return type(initial).model_validate(payload)


def _waiting_parent():
    initial = initial_state()
    pending = RunPendingSummary(
        calls=(
            PendingCallSummary(
                call_id="approval-1",
                kind=PendingCallKind.approval,
                tool_name="dangerous_tool",
            ),
        )
    )
    payload = initial.model_dump(mode="python", by_alias=True)
    payload.update(
        checkpoint_seq=1,
        checkpoint_kind="waiting",
        last_checkpoint_run_attempt_id=ATTEMPT_ID,
        last_checkpoint_fence=1,
        host=HostContinuationState(
            deferred=DeferredContinuationState(
                requests={
                    "calls": [],
                    "approvals": [
                        {
                            "tool_name": "dangerous_tool",
                            "args": {},
                            "tool_call_id": "approval-1",
                        }
                    ],
                    "metadata": {},
                }
            ),
            inbox_receipts=(
                InboxReceipt(
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


def test_start_and_completed_continue_use_the_selected_thread_identity() -> None:
    started = initialize_start_state(_seed(), thread_id=THREAD_ID)
    parent = _completed_parent()
    continued = initialize_completed_continuation_state(_seed(), parent)

    assert started.thread_id == THREAD_ID
    assert continued.thread_id == parent.thread_id
    assert continued.harness.environment_states == {}
    assert continued.host == HostContinuationState()
    assert continued.outcome_candidate is None


def test_waiting_continue_preserves_deferred_values_but_not_parent_receipts() -> None:
    parent = _waiting_parent()

    continued = initialize_waiting_continuation_state(_seed(), parent)

    assert continued.thread_id == parent.thread_id
    assert continued.host.deferred == parent.host.deferred
    assert continued.host.inbox_receipts == ()


def test_fork_and_fork_retry_clear_environment_and_use_the_correct_thread_identity() -> None:
    parent = _completed_parent()
    fork_thread_id = "thread-fedcbafedcbafedcbafedcbafedcbafe"
    forked = initialize_fork_state(_seed(), parent, thread_id=fork_thread_id)
    retry_thread_id = "thread-abcdefabcdefabcdefabcdefabcdefab"
    retried = initialize_retry_state(
        _seed("run_fedcba0987654321"),
        thread_id=retry_thread_id,
        source_lineage_kind=RunLineageKind.fork,
        source_input_kind=RunInputKind.agent_input,
        parent=parent,
    )

    assert forked.thread_id == fork_thread_id
    assert forked.harness.environment_states == {}
    assert retried.thread_id == retry_thread_id
    assert retried.harness.environment_states == {}


@pytest.mark.parametrize("operation", ["continue", "waiting", "fork", "retry"])
def test_state_initialization_retains_and_narrows_usage_limits(operation: str) -> None:
    parent = _waiting_parent() if operation == "waiting" else _completed_parent()
    parent = parent.model_copy(update={"usage_limits": UsageLimits(request_limit=3, total_tokens_limit=500)})
    seed = _seed().model_copy(update={"usage_limits": UsageLimits(request_limit=10, tool_calls_limit=2)})
    if operation == "continue":
        state = initialize_completed_continuation_state(seed, parent)
    elif operation == "waiting":
        state = initialize_waiting_continuation_state(seed, parent)
    elif operation == "fork":
        state = initialize_fork_state(seed, parent, thread_id="thread-fedcbafedcbafedcbafedcbafedcbafe")
    else:
        state = initialize_retry_state(
            seed,
            thread_id="thread-fedcbafedcbafedcbafedcbafedcbafe",
            source_lineage_kind=RunLineageKind.fork,
            source_input_kind=RunInputKind.agent_input,
            parent=parent,
        )
    restored = type(state).model_validate_json(state.model_dump_json())
    assert restored.usage_limits == UsageLimits(request_limit=3, total_tokens_limit=500, tool_calls_limit=2)
    assert parent.usage_limits.tool_calls_limit is None
    assert seed.usage_limits.request_limit == 10


@pytest.mark.parametrize("operation", ["continue", "waiting", "fork", "retry"])
def test_lineage_retains_prepared_plugins_without_consuming_new_input(operation):
    from a13n_service.agents.domain import PluginSelection, PreparedAgentPlugins

    parent = _waiting_parent() if operation == "waiting" else _completed_parent()
    selection = PluginSelection(instance_name="audit", plugin_key="test.audit", config={})
    config = parent.effective_agent_config.model_copy(update={"plugins": (selection,)})
    prepared = PreparedAgentPlugins(plugins=(selection.model_copy(update={"config": {"limit": 5}}),))
    parent = parent.model_copy(update={"effective_agent_config": config, "prepared_plugins": prepared})
    seed = _seed().model_copy(
        update={"effective_agent_config": config.model_copy(update={"instructions": "New request"})}
    )
    if operation == "continue":
        state = initialize_completed_continuation_state(seed, parent)
    elif operation == "waiting":
        state = initialize_waiting_continuation_state(seed, parent)
    elif operation == "fork":
        state = initialize_fork_state(seed, parent, thread_id="thread-fedcbafedcbafedcbafedcbafedcbafe")
    else:
        # Retry takes configuration from the failed source, not its state parent.
        source_preparation = PreparedAgentPlugins(plugins=(selection.model_copy(update={"config": {"limit": 7}}),))
        seed = seed.model_copy(update={"prepared_plugins": source_preparation})
        state = initialize_retry_state(
            seed,
            thread_id=parent.thread_id,
            source_lineage_kind=RunLineageKind.continue_,
            source_input_kind=RunInputKind.agent_input,
            parent=parent,
        )
        prepared = source_preparation
    assert state.prepared_plugins == prepared
    assert state.checkpoint_seq == 0
    assert not state.initial_input_applied
    assert state.effective_agent_config.instructions == "New request"


def test_explicit_plugin_change_prepares_new_graph_and_preserves_parent():
    from a13n_service.agents.domain import PluginSelection, PreparedAgentPlugins

    parent = _completed_parent()
    selection = PluginSelection(instance_name="audit", plugin_key="test.audit", config={"value": True})
    config = parent.effective_agent_config.model_copy(update={"plugins": (selection,)})
    prepared = PreparedAgentPlugins(plugins=(selection,))
    parent = parent.model_copy(update={"effective_agent_config": config, "prepared_plugins": prepared})
    changed = config.model_copy(update={"plugins": (selection.model_copy(update={"config": {"value": 1}}),)})
    state = initialize_completed_continuation_state(
        _seed().model_copy(update={"effective_agent_config": changed}), parent
    )
    assert state.prepared_plugins is None
    assert parent.prepared_plugins.plugins[0].config["value"] is True
