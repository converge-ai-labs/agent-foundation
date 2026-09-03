from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from datetime import timedelta

import pytest
from a13n_harness import (
    AgentContext,
    AgentDefinition,
    AgentSpec,
    HarnessBuilder,
    HarnessRunResult,
    ModelRecoveryPolicy,
    RunBindings,
)
from a13n_service.interactions import (
    AdaptedThreadInboxEntry,
    AttemptAuthority,
    AttemptExecutionService,
    AttemptMutationReceipt,
    AttemptPreparationAccepted,
    ConsumedThreadInboxEntry,
    DeferredContinuationState,
    FoundationRunControlCoordinator,
    HostContinuationState,
    RunAttemptYieldReason,
    RunStateEnvelope,
    RunStateStore,
    StoredRunState,
    compose_run_control,
)
from a13n_service.storage import ObjectStore
from pydantic_ai import Tool
from pydantic_ai.capabilities import AbstractCapability, Capability
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel

from .conftest import ATTEMPT_ID, NOW, RUN_ID, TENANT_ID, initial_state

pytestmark = pytest.mark.anyio


@dataclass
class _RecordingAttemptExecution(AttemptExecutionService):
    trace: list[str]
    yielded: RunAttemptYieldReason | None = None

    async def validate(self, authority: AttemptAuthority) -> AttemptMutationReceipt:
        self.trace.append("attempt:validate")
        return _receipt(authority)

    async def enter_harness(
        self,
        authority: AttemptAuthority,
        *,
        preparation: AttemptPreparationAccepted,
        harness_run_id: str,
    ) -> AttemptMutationReceipt:
        assert preparation.run_attempt_id == authority.run_attempt_id
        assert preparation.fence == authority.fence
        assert harness_run_id
        self.trace.append("attempt:enter")
        return _receipt(authority, run_delta=1, attempt_delta=1)

    async def increment_model_request(self, authority: AttemptAuthority) -> AttemptMutationReceipt:
        self.trace.append("attempt:model")
        return _receipt(authority, attempt_delta=1)

    async def publish_checkpoint(
        self,
        authority: AttemptAuthority,
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
        authority: AttemptAuthority,
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
        authority: AttemptAuthority,
        state: StoredRunState,
    ) -> AttemptMutationReceipt:
        assert state.envelope.run_id == authority.run_id
        self.trace.append("inbox:confirm")
        return _receipt(authority)

    async def read_eligible(
        self,
        authority: AttemptAuthority,
    ) -> Sequence[AdaptedThreadInboxEntry]:
        assert authority.run_id == RUN_ID
        self.trace.append("inbox:read")
        if self._delivered:
            return ()
        self._delivered = True
        return self.entries


def _authority() -> AttemptAuthority:
    return AttemptAuthority(
        tenant_id=TENANT_ID,
        run_id=RUN_ID,
        run_attempt_id=ATTEMPT_ID,
        fence=1,
        lease_token="lease-token",
        worker_id="worker-1",
        worker_generation="generation-1",
        expected_run_version=1,
        expected_attempt_version=1,
    )


def _receipt(
    authority: AttemptAuthority,
    *,
    run_delta: int = 0,
    attempt_delta: int = 0,
) -> AttemptMutationReceipt:
    return AttemptMutationReceipt(
        run_version=authority.expected_run_version + run_delta,
        attempt_version=authority.expected_attempt_version + attempt_delta,
        lease_expires_at=NOW + timedelta(minutes=5),
    )


def _preparation(authority: AttemptAuthority) -> AttemptPreparationAccepted:
    return AttemptPreparationAccepted(
        run_attempt_id=authority.run_attempt_id,
        fence=authority.fence,
        mutation=_receipt(authority),
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


async def _stored_state(objects: ObjectStore, envelope: RunStateEnvelope) -> tuple[RunStateStore, StoredRunState]:
    states = RunStateStore(objects)
    return states, await states.create(TENANT_ID, envelope)


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
    coordinator: FoundationRunControlCoordinator,
    bindings: RunBindings,
    state: StoredRunState,
    model: FunctionModel,
    capabilities: tuple[AbstractCapability[AgentContext], ...] = (),
    model_recovery: ModelRecoveryPolicy | None = None,
) -> HarnessRunResult[str]:
    definition = compose_run_control(
        AgentDefinition(
            agent=AgentSpec(),
            output_type=str,
            model=model,
            capabilities=capabilities,
            model_recovery=model_recovery or ModelRecoveryPolicy(),
        ),
        coordinator,
    )
    executable = HarnessBuilder(instrumentation=None).build(definition)
    authority = coordinator.current_authority
    async with executable.stream(
        "accepted input",
        bindings=bindings,
        previous_state=state.envelope.harness,
    ) as stream:
        await coordinator.attach_stream(stream, _preparation(authority))
        _ = [item async for item in stream]
        result = stream.result
        assert result is not None
        return result


async def test_checkpoint_commits_inbox_receipt_only_after_native_delivery(
    interaction_object_store: ObjectStore,
) -> None:
    trace: list[str] = []
    calls: list[tuple[ModelMessage, ...]] = []
    states, stored = await _stored_state(interaction_object_store, initial_state())
    authority = _authority()
    bindings = RunBindings.embedded()
    execution = _RecordingAttemptExecution(trace)
    inbox = _RecordingThreadInbox(trace, entries=(_inbox_entry(),))
    coordinator = FoundationRunControlCoordinator(
        authority=authority,
        execution=execution,
        states=states,
        state=stored,
        instance=bindings.instance,
        inbox=inbox,
    )

    result = await _run(
        coordinator=coordinator,
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
    waiting = initial_state().model_copy(
        update={
            "host": HostContinuationState(
                deferred=DeferredContinuationState(
                    requests={"calls": [], "approvals": [], "metadata": {}},
                )
            )
        }
    )
    states, stored = await _stored_state(interaction_object_store, waiting)
    bindings = RunBindings.embedded()
    coordinator = FoundationRunControlCoordinator(
        authority=_authority(),
        execution=_RecordingAttemptExecution(trace),
        states=states,
        state=stored,
        instance=bindings.instance,
        inbox=_RecordingThreadInbox(trace, entries=(_inbox_entry(),)),
    )

    result = await _run(
        coordinator=coordinator,
        bindings=bindings,
        state=stored,
        model=_model(trace, calls),
    )

    assert result.output_or_raise() == "turn-2"
    assert _business_prompts(calls[0]) == ["accepted input"]
    assert _business_prompts(calls[1]) == ["accepted input", "new direction"]
    assert trace.index("model:1") < trace.index("inbox:read") < trace.index("model:2")
    assert coordinator.current_state.envelope.host.deferred is None


async def test_waiting_gate_survives_internal_model_recovery(
    interaction_object_store: ObjectStore,
) -> None:
    trace: list[str] = []
    calls: list[tuple[ModelMessage, ...]] = []
    waiting = initial_state().model_copy(
        update={
            "host": HostContinuationState(
                deferred=DeferredContinuationState(
                    requests={"calls": [], "approvals": [], "metadata": {}},
                )
            )
        }
    )
    states, stored = await _stored_state(interaction_object_store, waiting)
    bindings = RunBindings.embedded()
    coordinator = FoundationRunControlCoordinator(
        authority=_authority(),
        execution=_RecordingAttemptExecution(trace),
        states=states,
        state=stored,
        instance=bindings.instance,
        inbox=_RecordingThreadInbox(trace, entries=(_inbox_entry(),)),
    )

    result = await _run(
        coordinator=coordinator,
        bindings=bindings,
        state=stored,
        model=_recovering_model(trace, calls),
        model_recovery=ModelRecoveryPolicy(
            enabled=True,
            max_attempts=2,
            backoff_initial_seconds=0,
            backoff_max_seconds=0,
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
    coordinator = FoundationRunControlCoordinator(
        authority=_authority(),
        execution=execution,
        states=states,
        state=stored,
        instance=bindings.instance,
        inbox=_RecordingThreadInbox(trace),
    )
    await coordinator.request_handoff(RunAttemptYieldReason.service_drain)

    result = await _run(
        coordinator=coordinator,
        bindings=bindings,
        state=stored,
        model=_model(trace, calls),
    )
    assert result.status == "cancelled"
    assert calls == []
    assert coordinator.handoff_ready
    assert coordinator.current_state.envelope.input_disposition == "applied"
    mutation = await coordinator.complete_handoff()

    assert not coordinator.handoff_ready
    assert execution.yielded is RunAttemptYieldReason.service_drain
    assert mutation.run_version == coordinator.current_authority.expected_run_version
    assert trace.index("attempt:checkpoint") < trace.index("attempt:yield")


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
