from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from datetime import timedelta

import pytest
from a13n_environment_provider import EnvironmentState
from a13n_harness import (
    AgentContext,
    AgentDefinition,
    AgentSpec,
    DeferredToolResume,
    HarnessBuilder,
    HarnessEvent,
    HarnessRunResult,
    HarnessRunResultEvent,
    HarnessState,
    ModelRecoveryPolicy,
    RunBindings,
    SafeFailure,
)
from a13n_harness.capabilities import CompactionCapability, CompactionPolicy
from a13n_service.interactions.attempts import (
    AttemptContext,
    AttemptExecutionService,
    AttemptMutationReceipt,
    AttemptPreparationAccepted,
)
from a13n_service.interactions.domain import RunAttemptYieldReason
from a13n_service.interactions.environment_observation import EnvironmentHookObservation
from a13n_service.interactions.harness_results import (
    RunTerminalDisposition,
    RunTerminalReceipt,
    StoredHarnessOutcomeAdapter,
)
from a13n_service.interactions.harness_runtime import (
    HarnessCollaborators,
    HarnessDriver,
    HarnessInvocation,
    ImmediateHarnessInput,
)
from a13n_service.interactions.inbox_delivery import AdaptedThreadInboxEntry
from a13n_service.interactions.objects import RunPayloadStore, RunStateStore, StoredRunState
from a13n_service.interactions.run_control import RunAttemptControl
from a13n_service.interactions.state import (
    CompletedOutcomeCandidate,
    ConsumedThreadInboxEntry,
    DeferredContinuationState,
    HostContinuationState,
    RunStateEnvelope,
)
from a13n_service.storage import ObjectStore
from anyio import Event, create_task_group, fail_after
from pydantic import TypeAdapter
from pydantic_ai import Tool
from pydantic_ai.capabilities import AbstractCapability, Capability
from pydantic_ai.messages import (
    EnqueuedMessagesEvent,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextContent,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel
from pydantic_ai.tools import DeferredToolRequests, DeferredToolResults

from .conftest import ATTEMPT_ID, NOW, ORGANIZATION_ID, RUN_ID, initial_state, progress_state

pytestmark = pytest.mark.anyio


@dataclass
class _RecordingAttemptExecution(AttemptExecutionService):
    trace: list[str]
    yielded: RunAttemptYieldReason | None = None
    handoff_permitted: bool = True

    async def commit_preparation_success(self, authority: AttemptContext):
        return _preparation(authority)

    async def can_handoff(self, authority: AttemptContext) -> bool:
        return self.handoff_permitted

    async def heartbeat(self, authority, *, lease_duration):
        self.trace.append("attempt:heartbeat")
        return _receipt(authority, attempt_delta=1)

    async def validate(self, authority: AttemptContext) -> AttemptMutationReceipt:
        self.trace.append("attempt:validate")
        return _receipt(authority)

    async def enter_harness(
        self,
        authority: AttemptContext,
        *,
        preparation: AttemptPreparationAccepted,
        harness_run_id: str,
    ) -> AttemptMutationReceipt:
        assert preparation.run_attempt_id == authority.run_attempt_id
        assert preparation.fence == authority.fence
        assert harness_run_id
        self.trace.append("attempt:enter")
        return _receipt(authority, run_delta=1, attempt_delta=1)

    async def increment_model_request(self, authority: AttemptContext) -> AttemptMutationReceipt:
        self.trace.append("attempt:model")
        return _receipt(authority, attempt_delta=1)

    async def yield_attempt(
        self,
        authority: AttemptContext,
        reason: RunAttemptYieldReason,
    ) -> AttemptMutationReceipt:
        self.trace.append("attempt:yield")
        self.yielded = reason
        return _receipt(authority, run_delta=1, attempt_delta=1)


@dataclass
class _RecordingThreadInbox:
    trace: list[str]
    entries: tuple[AdaptedThreadInboxEntry, ...] = ()

    async def confirm_checkpoint(
        self,
        authority: AttemptContext,
        state: StoredRunState,
    ) -> AttemptMutationReceipt:
        assert state.envelope.run_id == authority.run_id
        self.trace.append("inbox:confirm")
        consumed = set(state.envelope.host.consumed_inbox_entries)
        self.entries = tuple(entry for entry in self.entries if entry.receipt not in consumed)
        return _receipt(authority)

    async def read_eligible(
        self,
        authority: AttemptContext,
    ) -> Sequence[AdaptedThreadInboxEntry]:
        assert authority.run_id == RUN_ID
        self.trace.append("inbox:read")
        return self.entries


@dataclass
class _RecordingEventProjector:
    events: list[HarnessEvent | HarnessRunResultEvent[object]] = field(default_factory=list)

    def project(self, event: HarnessEvent | HarnessRunResultEvent[object]) -> None:
        self.events.append(event)

    def project_environment(self, observation: EnvironmentHookObservation) -> None:
        del observation

    async def close(self) -> None:
        pass


@dataclass
class _RecordingTerminalCommitter:
    states: list[StoredRunState] = field(default_factory=list)
    failures: list[SafeFailure] = field(default_factory=list)
    cancelled: int = 0

    async def prepare_state_outcome(self, authority, state):
        async def commit(current):
            return await self.commit_state_outcome(current, state)

        return commit

    async def commit_state_outcome(
        self,
        authority: AttemptContext,
        state: StoredRunState,
    ) -> RunTerminalReceipt:
        self.states.append(state)
        disposition = RunTerminalDisposition(state.envelope.checkpoint_kind)
        return _terminal_receipt(authority, disposition)

    async def commit_failure(
        self,
        authority: AttemptContext,
        failure: SafeFailure,
    ) -> RunTerminalReceipt:
        self.failures.append(failure)
        return _terminal_receipt(authority, RunTerminalDisposition.failed)

    async def reconcile_cancelled(
        self,
        authority: AttemptContext,
    ) -> RunTerminalReceipt:
        self.cancelled += 1
        return _terminal_receipt(authority, RunTerminalDisposition.cancelled)


def _context(thread_id: str) -> AttemptContext:
    return AttemptContext(
        organization_id=ORGANIZATION_ID,
        thread_id=thread_id,
        run_id=RUN_ID,
        run_attempt_id=ATTEMPT_ID,
        fence=1,
        lease_token="lease-token",
        worker_id="worker-1",
        worker_generation="generation-1",
        worker_build_id="build-1",
        runtime_lock_digest="a" * 64,
        expected_run_version=1,
        expected_attempt_version=1,
        lease_expires_at=NOW + timedelta(minutes=5),
        lease_duration=timedelta(seconds=30),
        renewal_interval=timedelta(seconds=10),
        renewal_timeout=timedelta(seconds=5),
        reconciliation_timeout=timedelta(seconds=5),
        cleanup_timeout=timedelta(seconds=5),
    )


def _receipt(
    authority: AttemptContext,
    *,
    run_delta: int = 0,
    attempt_delta: int = 0,
) -> AttemptMutationReceipt:
    return AttemptMutationReceipt(
        run_version=authority.expected_run_version + run_delta,
        attempt_version=authority.expected_attempt_version + attempt_delta,
        lease_expires_at=NOW + timedelta(minutes=5),
    )


def _preparation(authority: AttemptContext) -> AttemptPreparationAccepted:
    return AttemptPreparationAccepted(
        run_attempt_id=authority.run_attempt_id,
        fence=authority.fence,
        mutation=_receipt(authority),
    )


def _terminal_receipt(
    authority: AttemptContext,
    disposition: RunTerminalDisposition,
) -> RunTerminalReceipt:
    return RunTerminalReceipt(
        disposition=disposition,
        run_version=authority.expected_run_version + 1,
        attempt_version=authority.expected_attempt_version + 1,
        thread_version=None if disposition is RunTerminalDisposition.retrying else 2,
    )


def _inbox_entry() -> AdaptedThreadInboxEntry:
    return AdaptedThreadInboxEntry(
        delivery_sequence=1,
        receipt=ConsumedThreadInboxEntry(
            inbox_entry_id="inb_1234567890abcdef",
            kind="message",
        ),
        input="new direction",
    )


def _waiting_gate_state() -> RunStateEnvelope:
    state = initial_state()
    pending_call = ToolCallPart(
        tool_name="external_step",
        args={},
        tool_call_id="deferred-1",
    )
    requests = DeferredToolRequests(approvals=[pending_call])
    harness = HarnessState.new(
        thread_id=state.thread_id,
        message_history=(
            ModelRequest(parts=[UserPromptPart(content="prior input")]),
            ModelResponse(parts=[pending_call]),
        ),
    )
    return state.model_copy(
        update={
            "harness": harness,
            "host": HostContinuationState(
                deferred=DeferredContinuationState(
                    requests=TypeAdapter(DeferredToolRequests).dump_python(
                        requests,
                        mode="json",
                    ),
                )
            ),
        }
    )


async def _stored_state(objects: ObjectStore, envelope: RunStateEnvelope) -> tuple[RunStateStore, StoredRunState]:
    states = RunStateStore(objects)
    return states, await states.create(ORGANIZATION_ID, envelope)


def _outcome_adapter(objects: ObjectStore) -> StoredHarnessOutcomeAdapter:
    return StoredHarnessOutcomeAdapter(
        organization_id=ORGANIZATION_ID,
        run_id=RUN_ID,
        payloads=RunPayloadStore(objects),
        max_output_bytes=1024,
        inline_output_bytes=128,
    )


def _model(
    trace: list[str],
    calls: list[tuple[ModelMessage, ...]],
) -> FunctionModel:
    async def stream(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        calls.append(tuple(messages))
        trace.append(f"model:{len(calls)}")
        yield f"turn-{len(calls)}"

    return FunctionModel(stream_function=stream)


async def _run(
    *,
    control: RunAttemptControl,
    bindings: RunBindings,
    state: StoredRunState,
    model: FunctionModel,
    projector: _RecordingEventProjector | None = None,
    capabilities: tuple[AbstractCapability[AgentContext], ...] = (),
    model_recovery: ModelRecoveryPolicy | None = None,
) -> HarnessRunResult[str]:
    deferred_resume = None
    if state.envelope.host.deferred is not None:
        requests = TypeAdapter(DeferredToolRequests).validate_python(
            state.envelope.host.deferred.requests,
        )
        deferred_resume = DeferredToolResume(
            requests,
            DeferredToolResults(
                calls={item.tool_call_id: "resolved" for item in requests.calls},
                approvals={item.tool_call_id: True for item in requests.approvals},
            ),
        )
    driver = HarnessDriver(
        HarnessBuilder(instrumentation=None),
        control=control,
        projector=projector or _RecordingEventProjector(),
    )
    control.bind_executor(driver, lambda: None)
    return await driver.run(
        HarnessInvocation(
            definition=AgentDefinition(
                agent=AgentSpec(),
                output_type=str,
                model=model,
                capabilities=capabilities,
                model_recovery=model_recovery or ModelRecoveryPolicy(),
            ),
            input=ImmediateHarnessInput("accepted input"),
            collaborators=HarnessCollaborators(instance=bindings.instance),
            deferred_resume=deferred_resume,
        ),
        preparation=_preparation(control.current_context),
    )


async def _consume(
    *,
    control: RunAttemptControl,
    bindings: RunBindings,
    state: StoredRunState,
    model: FunctionModel,
    projector: _RecordingEventProjector,
    outcome_adapter: StoredHarnessOutcomeAdapter,
    terminal_committer: _RecordingTerminalCommitter,
    capabilities: tuple[AbstractCapability[AgentContext], ...] = (),
    model_recovery: ModelRecoveryPolicy | None = None,
) -> tuple[HarnessRunResult[str], RunTerminalReceipt | AttemptMutationReceipt]:
    result = await _run(
        control=control,
        bindings=bindings,
        state=state,
        model=model,
        projector=projector,
        capabilities=capabilities,
        model_recovery=model_recovery,
    )
    finalization = await control.finalize(
        result,
        adapter=outcome_adapter,
        committer=terminal_committer,
    )
    return result, finalization


async def test_checkpoint_commits_inbox_receipt_only_after_native_delivery(
    interaction_object_store: ObjectStore,
) -> None:
    trace: list[str] = []
    calls: list[tuple[ModelMessage, ...]] = []
    states, stored = await _stored_state(interaction_object_store, initial_state())
    context = _context(stored.envelope.thread_id)
    bindings = RunBindings.embedded()
    execution = _RecordingAttemptExecution(trace)
    inbox = _RecordingThreadInbox(trace, entries=(_inbox_entry(),))
    coordinator = RunAttemptControl(
        context=context,
        execution=execution,
        states=states,
        state=stored,
        inbox=inbox,
    )

    result = await _run(
        control=coordinator,
        bindings=bindings,
        state=stored,
        model=_tool_model(trace, calls),
        capabilities=(Capability(id="test.step", tools=[Tool(_step)]),),
    )

    assert result.output_or_raise() == "done"
    assert len(calls) == 2
    assert _business_prompts(calls[0]) == ["accepted input", "new direction"]
    assert _business_prompts(calls[1]) == ["accepted input", "new direction"]
    assert trace.index("inbox:read") < trace.index("model:1")
    assert coordinator.current_state.envelope.host.consumed_inbox_entries == (_inbox_entry().receipt,)
    assert coordinator.current_state.envelope.input_disposition == "applied"


@pytest.mark.parametrize("scenario", ["compacted", "compaction_failed", "model_recovery"])
async def test_compaction_precedes_receipt_publication_without_losing_incorporation(interaction_object_store, scenario):
    compaction_fails = scenario == "compaction_failed"
    trace: list[str] = []
    states, stored = await _stored_state(interaction_object_store, initial_state())
    inbox = _RecordingThreadInbox(trace)
    control = RunAttemptControl(
        context=_context(stored.envelope.thread_id),
        execution=_RecordingAttemptExecution(trace),
        states=states,
        state=stored,
        inbox=inbox,
    )
    compact_calls = 0
    ordinary_calls = 0

    async def step() -> str:
        inbox.entries = (_inbox_entry(),)
        return "stepped"

    async def stream(messages, info):
        nonlocal compact_calls, ordinary_calls
        if info.model_settings and info.model_settings.get("tool_choice") == "none":
            compact_calls += 1
            if compact_calls == 1:
                assert "new direction" in _business_prompts(tuple(messages))
                assert control.current_state.envelope.host.consumed_inbox_entries == ()
            if compaction_fails:
                raise RuntimeError("compaction failed")
            yield "The earlier work was summarized."
            return
        ordinary_calls += 1
        if ordinary_calls == 1:
            yield {0: DeltaToolCall(name="step", json_args="{}", tool_call_id="step-1")}
        else:
            assert ("new direction" in _business_prompts(tuple(messages))) is compaction_fails
            if scenario == "model_recovery" and ordinary_calls == 2:
                raise RuntimeError("recover after compaction")
            yield "done"

    result, _ = await _consume(
        control=control,
        bindings=RunBindings.embedded(),
        state=stored,
        model=FunctionModel(stream_function=stream),
        projector=_RecordingEventProjector(),
        outcome_adapter=_outcome_adapter(interaction_object_store),
        terminal_committer=_RecordingTerminalCommitter(),
        capabilities=(
            Capability(id="test.step", tools=[Tool(step)]),
            CompactionCapability(CompactionPolicy(trigger_tokens=1)),
        ),
        model_recovery=ModelRecoveryPolicy(
            enabled=True, max_attempts=2, backoff_initial_seconds=0, backoff_max_seconds=0
        ),
    )
    assert result.output_or_raise() == "done"
    assert compact_calls >= 1
    assert ordinary_calls == (3 if scenario == "model_recovery" else 2)
    assert trace.count("attempt:model") == ordinary_calls + compact_calls
    assert (
        "new direction" in _business_prompts(control.current_state.envelope.harness.message_history)
    ) is compaction_fails
    assert control.current_state.envelope.host.consumed_inbox_entries == (_inbox_entry().receipt,)


async def test_checkpoint_does_not_wait_for_driver_event_consumption(interaction_object_store, monkeypatch):
    trace: list[str] = []
    states, stored = await _stored_state(interaction_object_store, initial_state())
    inbox = _RecordingThreadInbox(trace, entries=(_inbox_entry(),))
    control = RunAttemptControl(
        context=_context(stored.envelope.thread_id),
        execution=_RecordingAttemptExecution(trace),
        states=states,
        state=stored,
        inbox=inbox,
    )
    checkpointed = Event()

    class DelayedProjector(_RecordingEventProjector):
        async def project(self, event):
            await checkpointed.wait()
            super().project(event)

    projector = DelayedProjector()
    replace_state = states.replace

    async def publish(prior, successor, **kwargs):
        published = await replace_state(prior, successor, **kwargs)
        if successor.host.consumed_inbox_entries:
            assert not projector.events
            checkpointed.set()
        return published

    monkeypatch.setattr(states, "replace", publish)
    with fail_after(3):
        result = await _run(
            control=control, bindings=RunBindings.embedded(), state=stored, model=_model(trace, []), projector=projector
        )
    assert result.output_or_raise() == "turn-1"
    assert control.current_state.envelope.host.consumed_inbox_entries == (_inbox_entry().receipt,)
    assert any(
        isinstance(event, HarnessEvent) and isinstance(event.event, EnqueuedMessagesEvent) for event in projector.events
    )
    assert inbox.entries == ()


@pytest.mark.parametrize("saved", ["raw", "compacted", "unmarked"])
async def test_recovery_reconciles_known_inbox_identity_before_reoffering(interaction_object_store, saved):
    entry = _inbox_entry()
    states, initial = await _stored_state(interaction_object_store, initial_state())
    content = entry.tagged_input(RUN_ID) if saved == "raw" else "new direction" if saved == "unmarked" else "summary"
    envelope = progress_state(initial.envelope).model_copy(
        update={
            "harness": HarnessState.new(
                thread_id=initial.envelope.thread_id, message_history=[ModelRequest(parts=[UserPromptPart(content)])]
            ),
            "host": HostContinuationState(consumed_inbox_entries=(entry.receipt,) if saved == "compacted" else ()),
        }
    )
    stored = await states.replace(initial, envelope, run_attempt_id=ATTEMPT_ID, fence=1)
    inbox = _RecordingThreadInbox([], entries=(entry,))
    control = RunAttemptControl(
        context=_context(stored.envelope.thread_id),
        execution=_RecordingAttemptExecution([]),
        states=states,
        state=stored,
        inbox=inbox,
    )
    calls = []
    result = await _run(control=control, bindings=RunBindings.embedded(), state=stored, model=_model([], calls))
    assert result.output_or_raise() == "turn-1"
    assert _business_prompts(calls[0]).count("new direction") == {"raw": 1, "compacted": 0, "unmarked": 2}[saved]
    assert control.current_state.envelope.host.consumed_inbox_entries == (entry.receipt,)
    assert inbox.entries == ()


@pytest.mark.parametrize("write_committed", [False, True])
async def test_uncertain_terminal_checkpoint_keeps_input_pending_until_recovery(
    interaction_object_store, monkeypatch, write_committed
):
    from a13n_service.storage import ObjectStoreUnavailable

    states, stored = await _stored_state(interaction_object_store, initial_state())
    inbox = _RecordingThreadInbox([])
    control = RunAttemptControl(
        context=_context(stored.envelope.thread_id),
        execution=_RecordingAttemptExecution([]),
        states=states,
        state=stored,
        inbox=inbox,
    )

    async def step():
        inbox.entries = (_inbox_entry(),)
        return "stepped"

    result = await _run(
        control=control,
        bindings=RunBindings.embedded(),
        state=stored,
        model=_tool_model([], []),
        capabilities=(Capability(id="test.step", tools=[Tool(step, name="_step")]),),
    )
    assert result.output_or_raise() == "done"
    assert control.current_state.envelope.host.consumed_inbox_entries == ()
    original_replace = states.replace

    async def lose_response(prior, successor, **kwargs):
        if write_committed:
            await original_replace(prior, successor, **kwargs)
        raise ObjectStoreUnavailable("checkpoint response lost")

    monkeypatch.setattr(states, "replace", lose_response)
    with pytest.raises(ObjectStoreUnavailable):
        await control.finalize(
            result, adapter=_outcome_adapter(interaction_object_store), committer=_RecordingTerminalCommitter()
        )
    assert inbox.entries == (_inbox_entry(),)
    assert control.current_state.envelope.host.consumed_inbox_entries == ()
    monkeypatch.setattr(states, "replace", original_replace)
    restored = await states.read(ORGANIZATION_ID, RUN_ID)
    replacement = RunAttemptControl(
        context=_context(stored.envelope.thread_id),
        execution=_RecordingAttemptExecution([]),
        states=states,
        state=restored,
        inbox=inbox,
    )
    if write_committed:
        await replacement.commit_preparation()
        await replacement.reconcile_recovery_state()
        await replacement.recover_outcome(_RecordingTerminalCommitter())
    else:
        calls = []
        resumed = await _run(
            control=replacement, bindings=RunBindings.embedded(), state=restored, model=_model([], calls)
        )
        assert resumed.output_or_raise() == "turn-1"
        assert _business_prompts(calls[0]).count("new direction") == 1
        if "new direction" in _business_prompts(replacement.current_state.envelope.harness.message_history):
            assert replacement.current_state.envelope.host.consumed_inbox_entries == (_inbox_entry().receipt,)
        await replacement.finalize(
            resumed,
            adapter=_outcome_adapter(interaction_object_store),
            committer=_RecordingTerminalCommitter(),
        )
    assert replacement.current_state.envelope.host.consumed_inbox_entries == (_inbox_entry().receipt,)
    assert inbox.entries == ()


async def test_waiting_successor_withholds_inbox_until_first_response(
    interaction_object_store: ObjectStore,
) -> None:
    trace: list[str] = []
    calls: list[tuple[ModelMessage, ...]] = []
    waiting = _waiting_gate_state()
    states, stored = await _stored_state(interaction_object_store, waiting)
    bindings = RunBindings.embedded()
    coordinator = RunAttemptControl(
        context=_context(stored.envelope.thread_id),
        execution=_RecordingAttemptExecution(trace),
        states=states,
        state=stored,
        inbox=_RecordingThreadInbox(trace, entries=(_inbox_entry(),)),
    )

    result = await _run(
        control=coordinator,
        bindings=bindings,
        state=stored,
        model=_model(trace, calls),
        capabilities=(
            Capability(
                id="test.external-step",
                tools=[Tool(_step, name="external_step", requires_approval=True)],
            ),
        ),
    )

    assert _business_prompts(calls[0]) == ["accepted input"]
    assert _business_prompts(calls[1]) == ["accepted input", "new direction"]
    assert result.output_or_raise() == "turn-2"
    assert trace.index("model:1") < trace.index("inbox:read") < trace.index("model:2")
    assert coordinator.current_state.envelope.host.deferred is None


async def test_waiting_gate_survives_internal_model_recovery(
    interaction_object_store: ObjectStore,
) -> None:
    trace: list[str] = []
    calls: list[tuple[ModelMessage, ...]] = []
    waiting = _waiting_gate_state()
    states, stored = await _stored_state(interaction_object_store, waiting)
    bindings = RunBindings.embedded()
    coordinator = RunAttemptControl(
        context=_context(stored.envelope.thread_id),
        execution=_RecordingAttemptExecution(trace),
        states=states,
        state=stored,
        inbox=_RecordingThreadInbox(trace, entries=(_inbox_entry(),)),
    )

    result = await _run(
        control=coordinator,
        bindings=bindings,
        state=stored,
        model=_recovering_model(trace, calls),
        model_recovery=ModelRecoveryPolicy(
            enabled=True,
            max_attempts=2,
            backoff_initial_seconds=0,
            backoff_max_seconds=0,
        ),
        capabilities=(
            Capability(
                id="test.external-step",
                tools=[Tool(_step, name="external_step", requires_approval=True)],
            ),
        ),
    )

    assert result.output_or_raise() == "turn-3"
    assert len(calls) == 3
    assert _business_prompts(calls[0]) == ["accepted input"]
    assert _business_prompts(calls[1]) == ["accepted input"]
    assert _business_prompts(calls[2]) == ["accepted input", "new direction"]
    assert trace.index("model:2") < trace.index("inbox:read") < trace.index("model:3")


async def test_planned_handoff_checkpoints_and_cancels_before_model_io(
    interaction_object_store: ObjectStore,
    monkeypatch,
) -> None:
    trace: list[str] = []
    calls: list[tuple[ModelMessage, ...]] = []
    states, stored = await _stored_state(interaction_object_store, initial_state())
    replace_state = states.replace

    async def record_checkpoint(*args, **kwargs):
        published = await replace_state(*args, **kwargs)
        trace.append("state:checkpoint")
        return published

    monkeypatch.setattr(states, "replace", record_checkpoint)
    bindings = RunBindings.embedded()
    execution = _RecordingAttemptExecution(trace)
    projector = _RecordingEventProjector()
    terminal = _RecordingTerminalCommitter()
    coordinator = RunAttemptControl(
        context=_context(stored.envelope.thread_id),
        execution=execution,
        states=states,
        state=stored,
        inbox=_RecordingThreadInbox(trace),
    )
    await coordinator.request_handoff(RunAttemptYieldReason.service_drain)

    result = await _run(
        control=coordinator,
        bindings=bindings,
        state=stored,
        model=_model(trace, calls),
        projector=projector,
    )
    await coordinator.renew_lease()
    await coordinator.reconcile()
    mutation = await coordinator.finalize(
        result, adapter=_outcome_adapter(interaction_object_store), committer=terminal
    )
    assert result.status == "cancelled"
    assert isinstance(mutation, AttemptMutationReceipt)
    assert calls == []
    assert coordinator.terminal_observation_allowed
    assert coordinator.current_state.envelope.input_disposition == "applied"
    assert terminal.states == []
    assert _business_prompts(coordinator.current_state.envelope.harness.message_history) == ["accepted input"]
    assert terminal.failures == []
    assert terminal.cancelled == 0

    assert execution.yielded is RunAttemptYieldReason.service_drain
    assert mutation.run_version == coordinator.current_context.expected_run_version
    assert trace.index("state:checkpoint") < trace.index("attempt:heartbeat") < trace.index("attempt:yield")


async def test_steer_during_tool_is_not_consumed_before_it_enters_checkpoint(interaction_object_store):
    trace: list[str] = []
    calls: list[tuple[ModelMessage, ...]] = []
    states, stored = await _stored_state(interaction_object_store, initial_state())
    inbox = _RecordingThreadInbox(trace)
    coordinator = RunAttemptControl(
        context=_context(stored.envelope.thread_id),
        execution=_RecordingAttemptExecution(trace),
        states=states,
        state=stored,
        inbox=inbox,
    )

    async def step() -> str:
        inbox.entries = (_inbox_entry(),)
        await coordinator.reconcile()
        await coordinator.request_handoff(RunAttemptYieldReason.service_drain)
        return "stepped"

    checkpoints: list[RunStateEnvelope] = []
    replace_state = states.replace

    async def record_checkpoint(*args, **kwargs):
        published = await replace_state(*args, **kwargs)
        checkpoints.append(published.envelope)
        return published

    states.replace = record_checkpoint
    result = await _run(
        control=coordinator,
        bindings=RunBindings.embedded(),
        state=stored,
        model=_tool_model(trace, calls),
        capabilities=(Capability(id="test.step", tools=[Tool(step, name="_step")]),),
    )
    for checkpoint in checkpoints:
        if _inbox_entry().receipt in checkpoint.host.consumed_inbox_entries:
            assert "new direction" in _business_prompts(checkpoint.harness.message_history)
    if result.status == "cancelled":
        assert "new direction" in _business_prompts(coordinator.current_state.envelope.harness.message_history)
    else:
        assert "new direction" in _business_prompts(calls[-1])


@pytest.mark.parametrize("write_committed", [False, True])
async def test_handoff_reconciles_uncertain_checkpoint_before_later_boundary(
    interaction_object_store, monkeypatch, write_committed
):
    from a13n_service.storage import ObjectStoreUnavailable

    trace: list[str] = []
    calls: list[tuple[ModelMessage, ...]] = []
    states, stored = await _stored_state(interaction_object_store, initial_state())
    original_replace = states.replace
    attempted: list[RunStateEnvelope] = []

    async def lose_first_response(prior, successor, **kwargs):
        attempted.append(successor)
        if len(attempted) == 1:
            if write_committed:
                await original_replace(prior, successor, **kwargs)
            raise ObjectStoreUnavailable("Unconfirmed checkpoint")
        return await original_replace(prior, successor, **kwargs)

    monkeypatch.setattr(states, "replace", lose_first_response)
    control = RunAttemptControl(
        context=_context(stored.envelope.thread_id),
        execution=_RecordingAttemptExecution(trace),
        states=states,
        state=stored,
        inbox=_RecordingThreadInbox(trace),
    )
    await control.request_handoff(RunAttemptYieldReason.service_drain)
    result = await _run(
        control=control,
        bindings=RunBindings.embedded(),
        state=stored,
        model=_tool_model(trace, calls),
        capabilities=(Capability(id="test.step", tools=[Tool(_step)]),),
    )
    assert result.status == "cancelled"
    assert len(calls) == 1
    assert control.current_state.envelope.checkpoint_seq == 2
    assert _business_prompts(control.current_state.envelope.harness.message_history) == ["accepted input"]
    if not write_committed:
        assert attempted[0] == attempted[1]


async def test_exhausted_handoff_budget_keeps_harness_running(interaction_object_store):
    trace: list[str] = []
    calls: list[tuple[ModelMessage, ...]] = []
    states, stored = await _stored_state(interaction_object_store, initial_state())
    execution = _RecordingAttemptExecution(trace, handoff_permitted=False)
    control = RunAttemptControl(
        context=_context(stored.envelope.thread_id),
        execution=execution,
        states=states,
        state=stored,
        inbox=_RecordingThreadInbox(trace),
    )
    await control.request_handoff(RunAttemptYieldReason.service_drain)
    result = await _run(control=control, bindings=RunBindings.embedded(), state=stored, model=_model(trace, calls))
    assert result.output_or_raise() == "turn-1"
    assert execution.yielded is None


async def test_waiting_handoff_preserves_isolated_first_request(interaction_object_store):
    trace: list[str] = []
    calls: list[tuple[ModelMessage, ...]] = []
    states, stored = await _stored_state(interaction_object_store, _waiting_gate_state())
    control = RunAttemptControl(
        context=_context(stored.envelope.thread_id),
        execution=_RecordingAttemptExecution(trace),
        states=states,
        state=stored,
        inbox=_RecordingThreadInbox(trace, entries=(_inbox_entry(),)),
    )
    await control.request_handoff(RunAttemptYieldReason.service_drain)
    result = await _run(
        control=control,
        bindings=RunBindings.embedded(),
        state=stored,
        model=_model(trace, calls),
        capabilities=(
            Capability(id="test.external", tools=[Tool(_step, name="external_step", requires_approval=True)]),
        ),
    )
    assert _business_prompts(calls[0]) == ["accepted input"]
    if result.status == "cancelled":
        assert "new direction" in _business_prompts(control.current_state.envelope.harness.message_history)
    else:
        assert "new direction" in _business_prompts(calls[-1])


async def test_driver_projects_events_and_control_commits_one_completed_result(
    interaction_object_store: ObjectStore,
) -> None:
    trace: list[str] = []
    calls: list[tuple[ModelMessage, ...]] = []
    states, stored = await _stored_state(interaction_object_store, initial_state())
    bindings = RunBindings.embedded()
    projector = _RecordingEventProjector()
    terminal = _RecordingTerminalCommitter()
    coordinator = RunAttemptControl(
        context=_context(stored.envelope.thread_id),
        execution=_RecordingAttemptExecution(trace),
        states=states,
        state=stored,
        inbox=_RecordingThreadInbox(trace),
    )
    result, finalization = await _consume(
        control=coordinator,
        bindings=bindings,
        state=stored,
        model=_model(trace, calls),
        projector=projector,
        outcome_adapter=_outcome_adapter(interaction_object_store),
        terminal_committer=terminal,
    )

    assert result.output_or_raise() == "turn-1"
    assert isinstance(finalization, RunTerminalReceipt)
    assert finalization.disposition is RunTerminalDisposition.completed
    assert projector.events
    assert len(terminal.states) == 1
    candidate = terminal.states[0].envelope.outcome_candidate
    assert candidate is not None and candidate.outcome == "completed"
    assert candidate.output == "turn-1"
    assert coordinator.current_state.envelope.checkpoint_kind == "completed"


async def test_control_excludes_provider_state_from_run_checkpoints(
    interaction_object_store: ObjectStore,
) -> None:
    trace: list[str] = []
    states, stored = await _stored_state(interaction_object_store, initial_state())
    bindings = RunBindings.embedded()
    terminal = _RecordingTerminalCommitter()
    coordinator = RunAttemptControl(
        context=_context(stored.envelope.thread_id),
        execution=_RecordingAttemptExecution(trace),
        states=states,
        state=stored,
        inbox=_RecordingThreadInbox(trace),
    )
    completed = await _run(
        control=coordinator,
        bindings=bindings,
        state=stored,
        model=_model(trace, []),
    )
    state = completed.state
    assert state is not None
    invalid_state = HarnessState.new(
        thread_id=state.thread_id,
        message_history=state.message_history,
        agent_context_state=state.agent_context_state,
        environment_states={
            "workspace": EnvironmentState(
                provider_key="test.provider",
                state_version="1",
                state={"target": "must-not-persist"},
            )
        },
    )
    invalid = HarnessRunResult[str](
        thread_id=completed.thread_id,
        run_id=completed.run_id,
        status="completed",
        output=completed.output,
        state=invalid_state,
        usage=completed.usage,
    )

    await coordinator.finalize(invalid, adapter=_outcome_adapter(interaction_object_store), committer=terminal)
    assert terminal.states
    assert all(not value.envelope.harness.environment_states for value in terminal.states)


async def test_control_commits_native_suspension_as_waiting(
    interaction_object_store: ObjectStore,
) -> None:
    trace: list[str] = []
    calls: list[tuple[ModelMessage, ...]] = []
    states, stored = await _stored_state(interaction_object_store, initial_state())
    bindings = RunBindings.embedded()
    terminal = _RecordingTerminalCommitter()
    coordinator = RunAttemptControl(
        context=_context(stored.envelope.thread_id),
        execution=_RecordingAttemptExecution(trace),
        states=states,
        state=stored,
        inbox=_RecordingThreadInbox(trace),
    )

    result, finalization = await _consume(
        control=coordinator,
        bindings=bindings,
        state=stored,
        model=_approval_model(trace, calls),
        capabilities=(Capability(id="test.approval", tools=[Tool(_step, requires_approval=True)]),),
        projector=_RecordingEventProjector(),
        outcome_adapter=_outcome_adapter(interaction_object_store),
        terminal_committer=terminal,
    )

    assert result.status == "suspended"
    assert isinstance(finalization, RunTerminalReceipt)
    assert finalization.disposition is RunTerminalDisposition.waiting
    assert len(terminal.states) == 1
    candidate = terminal.states[0].envelope.outcome_candidate
    assert candidate is not None and candidate.outcome == "waiting"
    assert terminal.states[0].envelope.host.deferred is not None


async def test_control_delegates_safe_harness_failure(
    interaction_object_store: ObjectStore,
) -> None:
    trace: list[str] = []
    calls: list[tuple[ModelMessage, ...]] = []
    states, stored = await _stored_state(interaction_object_store, initial_state())
    bindings = RunBindings.embedded()
    terminal = _RecordingTerminalCommitter()
    coordinator = RunAttemptControl(
        context=_context(stored.envelope.thread_id),
        execution=_RecordingAttemptExecution(trace),
        states=states,
        state=stored,
        inbox=_RecordingThreadInbox(trace),
    )

    result, finalization = await _consume(
        control=coordinator,
        bindings=bindings,
        state=stored,
        model=_failing_model(trace, calls),
        projector=_RecordingEventProjector(),
        outcome_adapter=_outcome_adapter(interaction_object_store),
        terminal_committer=terminal,
    )

    assert result.status == "failed"
    assert isinstance(finalization, RunTerminalReceipt)
    assert finalization.disposition is RunTerminalDisposition.failed
    assert len(terminal.failures) == 1
    assert terminal.states == []


async def test_control_reconciles_non_handoff_cancellation_candidate(
    interaction_object_store: ObjectStore,
) -> None:
    trace: list[str] = []
    calls: list[tuple[ModelMessage, ...]] = []
    states, stored = await _stored_state(interaction_object_store, initial_state())
    bindings = RunBindings.embedded()
    terminal = _RecordingTerminalCommitter()
    coordinator = RunAttemptControl(
        context=_context(stored.envelope.thread_id),
        execution=_RecordingAttemptExecution(trace),
        states=states,
        state=stored,
        inbox=_RecordingThreadInbox(trace),
    )
    completed = await _run(
        control=coordinator,
        bindings=bindings,
        state=stored,
        model=_model(trace, calls),
    )
    cancelled = HarnessRunResult[str](
        thread_id=completed.thread_id,
        run_id=completed.run_id,
        status="cancelled",
        output=None,
        state=None,
        usage=completed.usage,
    )
    finalization = await coordinator.finalize(
        cancelled,
        adapter=_outcome_adapter(interaction_object_store),
        committer=terminal,
    )

    assert isinstance(finalization, RunTerminalReceipt)
    assert finalization.disposition is RunTerminalDisposition.cancelled
    assert terminal.cancelled == 1
    assert len(calls) == 1


async def _step() -> str:
    return "stepped"


def _tool_model(
    trace: list[str],
    calls: list[tuple[ModelMessage, ...]],
) -> FunctionModel:
    async def stream(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        calls.append(tuple(messages))
        trace.append(f"model:{len(calls)}")
        if not any(
            isinstance(part, ToolReturnPart)
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
        ):
            yield {
                0: DeltaToolCall(
                    name="_step",
                    json_args=json.dumps({}),
                    tool_call_id="step-1",
                )
            }
            return
        yield "done"

    return FunctionModel(stream_function=stream)


def _recovering_model(
    trace: list[str],
    calls: list[tuple[ModelMessage, ...]],
) -> FunctionModel:
    async def stream(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> AsyncIterator[str]:
        del info
        calls.append(tuple(messages))
        trace.append(f"model:{len(calls)}")
        if len(calls) == 1:
            raise RuntimeError("provider interrupted")
        yield f"turn-{len(calls)}"

    return FunctionModel(stream_function=stream)


def _approval_model(
    trace: list[str],
    calls: list[tuple[ModelMessage, ...]],
) -> FunctionModel:
    async def stream(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> AsyncIterator[DeltaToolCalls]:
        del info
        calls.append(tuple(messages))
        trace.append(f"model:{len(calls)}")
        yield {
            0: DeltaToolCall(
                name="_step",
                json_args=json.dumps({}),
                tool_call_id="approval-1",
            )
        }

    return FunctionModel(stream_function=stream)


def _failing_model(
    trace: list[str],
    calls: list[tuple[ModelMessage, ...]],
) -> FunctionModel:
    async def stream(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> AsyncIterator[str]:
        del info
        calls.append(tuple(messages))
        trace.append(f"model:{len(calls)}")
        raise RuntimeError("provider failed")
        yield "unreachable"

    return FunctionModel(stream_function=stream)


def _business_prompts(messages: tuple[ModelMessage, ...]) -> list[str]:
    prompts: list[str] = []
    for message in messages:
        if not isinstance(message, ModelRequest):
            continue
        for part in message.parts:
            if isinstance(part, UserPromptPart):
                content = (part.content,) if isinstance(part.content, str) else part.content
                for item in content:
                    text = item.content if isinstance(item, TextContent) else item
                    if isinstance(text, str) and text in {"accepted input", "new direction"}:
                        prompts.append(text)
    return prompts


@pytest.mark.parametrize("lose_authority", [False, True])
async def test_slow_checkpoint_keeps_lease_live_and_fences_dispatch(
    interaction_sessions, interaction_object_store: ObjectStore, monkeypatch, lose_authority: bool
) -> None:
    from a13n_service.interactions.inbox import DatabaseThreadInboxReconciler
    from a13n_service.interactions.models import RunAttemptRecord
    from a13n_service.interactions.scheduling import AttemptScheduler, ClaimedAttempt
    from a13n_service.storage import short_session

    from tests.lifecycle_support import test_lifecycle_writer

    from .test_attempt_execution import _accept_root, _authority, _worker

    states, run, _ = await _accept_root(interaction_sessions, interaction_object_store)
    claim = await AttemptScheduler(interaction_sessions, clock=lambda: NOW, lifecycle=test_lifecycle_writer()).claim(
        run.id, _worker()
    )
    assert isinstance(claim, ClaimedAttempt)
    stored = await states.read(ORGANIZATION_ID, run.id)
    execution = AttemptExecutionService(
        interaction_sessions, clock=lambda: NOW + timedelta(seconds=1), lifecycle=test_lifecycle_writer()
    )

    async def unexpected_input(entry):
        raise AssertionError("No input was accepted in this test")

    control = RunAttemptControl(
        context=_authority(claim),
        execution=execution,
        states=states,
        state=stored,
        inbox=DatabaseThreadInboxReconciler(interaction_sessions, unexpected_input, clock=lambda: NOW),
    )
    await control.commit_preparation()
    writing, release = Event(), Event()
    original_put = interaction_object_store.put

    async def slow_put(*args, **kwargs):
        writing.set()
        await release.wait()
        return await original_put(*args, **kwargs)

    monkeypatch.setattr(interaction_object_store, "put", slow_put)
    calls: list[tuple[ModelMessage, ...]] = []
    results = []

    async def execute():
        results.append(
            await _run(control=control, bindings=RunBindings.embedded(), state=stored, model=_model([], calls))
        )

    with fail_after(5):
        async with create_task_group() as tasks:
            tasks.start_soon(execute)
            await writing.wait()
            before = control.current_context.expected_attempt_version
            await control.renew_lease()
            assert control.current_context.expected_attempt_version == before + 1
            await execution.validate(control.current_context)
            async with short_session(interaction_sessions) as database:
                attempt = await database.get(RunAttemptRecord, claim.attempt.id)
                assert attempt is not None and attempt.version == before + 1
            if lose_authority:
                await control.authority_lost()
            release.set()
    if lose_authority:
        assert calls == []
        assert results[0].status != "completed"
        assert control.current_state == stored
    else:
        assert len(calls) == 1
        assert results[0].status == "completed"
        assert control.current_state.envelope.input_disposition == "applied"
        await execution.validate(control.current_context)


async def test_recovery_receipt_repair_preserves_terminal_candidate(interaction_object_store):
    entry = _inbox_entry()
    states, initial = await _stored_state(interaction_object_store, initial_state())
    candidate = CompletedOutcomeCandidate(output="already finished")
    envelope = progress_state(initial.envelope).model_copy(
        update={
            "harness": HarnessState.new(
                thread_id=initial.envelope.thread_id,
                message_history=[ModelRequest(parts=[UserPromptPart(entry.tagged_input(RUN_ID))])],
            ),
            "checkpoint_kind": "completed",
            "outcome_candidate": candidate,
        }
    )
    stored = await states.replace(initial, envelope, run_attempt_id=ATTEMPT_ID, fence=1)
    inbox = _RecordingThreadInbox([], entries=(entry,))
    control = RunAttemptControl(
        context=_context(stored.envelope.thread_id),
        execution=_RecordingAttemptExecution([]),
        states=states,
        state=stored,
        inbox=inbox,
    )
    await control.commit_preparation()
    await control.reconcile_recovery_state()
    repaired = control.current_state.envelope
    assert repaired.checkpoint_kind == "completed"
    assert repaired.outcome_candidate == candidate
    assert repaired.harness == envelope.harness
    assert repaired.host.consumed_inbox_entries == (entry.receipt,)
    assert inbox.entries == ()
    await control.recover_outcome(_RecordingTerminalCommitter())
