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
from a13n_harness.errors import RunError
from a13n_service.interactions import (
    AdaptedThreadInboxEntry,
    AttemptContext,
    AttemptExecutionService,
    AttemptMutationReceipt,
    AttemptPreparationAccepted,
    ConsumedThreadInboxEntry,
    DeferredContinuationState,
    EnvironmentHookObservation,
    FoundationHarnessCollaborators,
    FoundationHarnessInvocation,
    FoundationHarnessOutcomeAdapter,
    HarnessDriver,
    HostContinuationState,
    ImmediateHarnessInput,
    RunAttemptControl,
    RunAttemptYieldReason,
    RunPayloadStore,
    RunStateEnvelope,
    RunStateStore,
    RunTerminalDisposition,
    RunTerminalReceipt,
    StoredRunState,
)
from a13n_service.storage import ObjectStore
from pydantic import TypeAdapter
from pydantic_ai import Tool
from pydantic_ai.capabilities import AbstractCapability, Capability
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel
from pydantic_ai.tools import DeferredToolRequests, DeferredToolResults

from .conftest import ATTEMPT_ID, NOW, RUN_ID, TENANT_ID, initial_state

pytestmark = pytest.mark.anyio


@dataclass
class _RecordingAttemptExecution(AttemptExecutionService):
    trace: list[str]
    yielded: RunAttemptYieldReason | None = None

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

    async def publish_checkpoint(
        self,
        authority: AttemptContext,
        states: RunStateStore,
        current: StoredRunState,
        successor: RunStateEnvelope,
    ) -> StoredRunState:
        self.trace.append("attempt:checkpoint")
        return await states.replace(
            current,
            successor,
            run_attempt_id=authority.run_attempt_id,
            fence=authority.fence,
        )

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
    _delivered: bool = field(default=False, init=False)

    async def confirm_checkpoint(
        self,
        authority: AttemptContext,
        state: StoredRunState,
    ) -> AttemptMutationReceipt:
        assert state.envelope.run_id == authority.run_id
        self.trace.append("inbox:confirm")
        return _receipt(authority)

    async def read_eligible(
        self,
        authority: AttemptContext,
    ) -> Sequence[AdaptedThreadInboxEntry]:
        assert authority.run_id == RUN_ID
        self.trace.append("inbox:read")
        if self._delivered:
            return ()
        self._delivered = True
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
        tenant_id=TENANT_ID,
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
    return states, await states.create(TENANT_ID, envelope)


def _outcome_adapter(objects: ObjectStore) -> FoundationHarnessOutcomeAdapter:
    return FoundationHarnessOutcomeAdapter(
        tenant_id=TENANT_ID,
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
        FoundationHarnessInvocation(
            definition=AgentDefinition(
                agent=AgentSpec(),
                output_type=str,
                model=model,
                capabilities=capabilities,
                model_recovery=model_recovery or ModelRecoveryPolicy(),
            ),
            input=ImmediateHarnessInput("accepted input"),
            collaborators=FoundationHarnessCollaborators(instance=bindings.instance),
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
    outcome_adapter: FoundationHarnessOutcomeAdapter,
    terminal_committer: _RecordingTerminalCommitter,
    capabilities: tuple[AbstractCapability[AgentContext], ...] = (),
) -> tuple[HarnessRunResult[str], RunTerminalReceipt | AttemptMutationReceipt]:
    result = await _run(
        control=control,
        bindings=bindings,
        state=state,
        model=model,
        projector=projector,
        capabilities=capabilities,
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
) -> None:
    trace: list[str] = []
    calls: list[tuple[ModelMessage, ...]] = []
    states, stored = await _stored_state(interaction_object_store, initial_state())
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

    result, mutation = await _consume(
        control=coordinator,
        bindings=bindings,
        state=stored,
        model=_model(trace, calls),
        projector=projector,
        outcome_adapter=_outcome_adapter(interaction_object_store),
        terminal_committer=terminal,
    )
    assert result.status == "cancelled"
    assert isinstance(mutation, AttemptMutationReceipt)
    assert calls == []
    assert not coordinator.handoff_ready
    assert coordinator.current_state.envelope.input_disposition == "applied"
    assert terminal.states == []
    assert terminal.failures == []
    assert terminal.cancelled == 0

    assert execution.yielded is RunAttemptYieldReason.service_drain
    assert mutation.run_version == coordinator.current_context.expected_run_version
    assert trace.index("attempt:checkpoint") < trace.index("attempt:yield")


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


async def test_control_rejects_provider_target_state_from_foundation_attachment(
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

    with pytest.raises(RunError) as error:
        await coordinator.finalize(
            invalid,
            adapter=_outcome_adapter(interaction_object_store),
            committer=terminal,
        )

    assert error.value.code == "foundation_environment_state_invalid"
    assert terminal.states == []


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
            if (
                isinstance(part, UserPromptPart)
                and isinstance(part.content, str)
                and part.content in {"accepted input", "new direction"}
            ):
                prompts.append(part.content)
    return prompts
