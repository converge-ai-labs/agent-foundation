from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from unittest.mock import AsyncMock, Mock

import pytest
from a13n_harness import (
    AgentDefinition,
    AgentIdentityRef,
    AgentInstanceContext,
    AgentSpec,
    HarnessBuilder,
    HarnessEvent,
    HarnessRunResultEvent,
    HarnessState,
    SafeFailure,
)
from a13n_harness.errors import RunError
from a13n_service.environments.lifecycle import EnvironmentLifecycle
from a13n_service.interactions.attempt_executor import ControlWatcher, LeaseMonitor, RunAttemptExecutor
from a13n_service.interactions.attempts import (
    AttemptAuthorityError,
    AttemptContext,
    AttemptExecutionService,
    AttemptMutationReceipt,
    AttemptPreparationAccepted,
    AttemptPreparationRejected,
    AttemptPreparationResult,
)
from a13n_service.interactions.environment_observation import EnvironmentHookObservation
from a13n_service.interactions.harness_control import HarnessContextBinding, HarnessHookBoundary, HarnessRunIdentity
from a13n_service.interactions.harness_results import (
    HarnessOutcomeProjection,
    RunTerminalDisposition,
    RunTerminalReceipt,
)
from a13n_service.interactions.harness_runtime import (
    HarnessCollaborators,
    HarnessDriver,
    HarnessInvocation,
    ImmediateHarnessInput,
)
from a13n_service.interactions.objects import RunStateStore, StoredRunState
from a13n_service.interactions.run_control import AdaptedThreadInboxEntry, RunAttemptControl
from a13n_service.interactions.state import CompletedOutcomeCandidate, ConsumedThreadInboxEntry, RunStateEnvelope
from a13n_service.storage import ObjectStore
from anyio import Event, create_task_group, sleep_forever
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, FunctionModel

from .conftest import ATTEMPT_ID, NOW, ORGANIZATION_ID, RUN_ID, initial_state

pytestmark = pytest.mark.anyio


@dataclass
class _Execution(AttemptExecutionService):
    trace: list[str]
    heartbeat_seen: Event = field(default_factory=Event)
    lose_heartbeat: bool = False
    reject_preparation: bool = False

    async def validate(self, context: AttemptContext) -> AttemptMutationReceipt:
        self.trace.append("attempt:validate")
        return _receipt(context)

    async def heartbeat(
        self,
        context: AttemptContext,
        *,
        lease_duration: timedelta,
    ) -> AttemptMutationReceipt:
        assert lease_duration == context.lease_duration
        self.trace.append("attempt:heartbeat")
        self.heartbeat_seen.set()
        if self.lose_heartbeat:
            raise AttemptAuthorityError("lease lost")
        return _receipt(context, attempt_delta=1)

    async def commit_preparation_success(
        self,
        context: AttemptContext,
    ) -> AttemptPreparationResult:
        self.trace.append("attempt:preparation-commit")
        if self.reject_preparation:
            return AttemptPreparationRejected(
                run_attempt_id=context.run_attempt_id,
                fence=context.fence,
                mutation=_receipt(context, run_delta=1, attempt_delta=1),
                failure=SafeFailure(
                    code="preparation_rejected",
                    message="The Attempt failed dependency preflight.",
                ),
            )
        return _preparation(context)

    async def enter_harness(
        self,
        context: AttemptContext,
        *,
        preparation: AttemptPreparationAccepted,
        harness_run_id: str,
    ) -> AttemptMutationReceipt:
        assert preparation.run_attempt_id == context.run_attempt_id
        assert harness_run_id
        self.trace.append("attempt:enter")
        return _receipt(context, run_delta=1, attempt_delta=1)

    async def increment_model_request(self, context: AttemptContext) -> AttemptMutationReceipt:
        self.trace.append("attempt:model")
        return _receipt(context, attempt_delta=1)

    async def publish_checkpoint(
        self,
        context: AttemptContext,
        states: RunStateStore,
        current: StoredRunState,
        successor: RunStateEnvelope,
    ) -> StoredRunState:
        self.trace.append("attempt:checkpoint")
        return await states.replace(
            current,
            successor,
            run_attempt_id=context.run_attempt_id,
            fence=context.fence,
        )


@dataclass
class _Inbox:
    trace: list[str]
    entries: tuple[AdaptedThreadInboxEntry, ...] = ()
    _delivered: bool = field(default=False, init=False)

    async def confirm_checkpoint(
        self,
        context: AttemptContext,
        state: StoredRunState,
    ) -> AttemptMutationReceipt:
        assert state.envelope.run_id == context.run_id
        self.trace.append("inbox:confirm")
        return _receipt(context)

    async def read_eligible(self, context: AttemptContext) -> Sequence[AdaptedThreadInboxEntry]:
        assert context.run_id == RUN_ID
        self.trace.append("inbox:read")
        if self._delivered:
            return ()
        self._delivered = True
        return self.entries


@dataclass
class _Wakeups:
    trace: list[str]
    receiving: Event = field(default_factory=Event)
    stopped: Event = field(default_factory=Event)
    acknowledged: Event = field(default_factory=Event)
    _first: bool = True

    async def receive(self) -> object:
        self.trace.append("wakeup:receive")
        self.receiving.set()
        if self._first:
            self._first = False
            return self
        try:
            await sleep_forever()
        finally:
            self.stopped.set()
        raise AssertionError("sleep_forever returned")

    async def acknowledge(self, signal: object) -> None:
        assert signal is self
        self.trace.append("wakeup:ack")
        self.acknowledged.set()


@dataclass
class _Projector:
    events: list[HarnessEvent | HarnessRunResultEvent[object]] = field(default_factory=list)

    def project(self, event: HarnessEvent | HarnessRunResultEvent[object]) -> None:
        self.events.append(event)

    def project_environment(self, observation: EnvironmentHookObservation) -> None:
        del observation

    async def close(self) -> None:
        pass


@dataclass
class _Preparer:
    context: AttemptContext
    invocation: HarnessInvocation[str]
    wakeups: _Wakeups
    heartbeat_seen: Event
    trace: list[str]

    async def prepare(self, context: AttemptContext) -> HarnessInvocation[str]:
        assert context is self.context
        await self.wakeups.receiving.wait()
        await self.heartbeat_seen.wait()
        self.trace.append("attempt:prepare")
        return self.invocation


@dataclass
class _Adapter:
    async def project(self, result) -> HarnessOutcomeProjection:
        assert result.state is not None
        return HarnessOutcomeProjection(
            harness=result.state,
            candidate=CompletedOutcomeCandidate(output=result.output),
            deferred=None,
        )


@dataclass
class _Committer:
    trace: list[str]

    async def commit_state_outcome(
        self,
        context: AttemptContext,
        state: StoredRunState,
    ) -> RunTerminalReceipt:
        assert state.envelope.checkpoint_kind == "completed"
        self.trace.append("attempt:terminal")
        return _terminal_receipt(context)

    async def commit_failure(self, context, failure):
        raise AssertionError("successful executor must not commit failure")

    async def reconcile_cancelled(self, context):
        raise AssertionError("successful executor must not reconcile cancellation")


@dataclass
class _Cleanup:
    wakeups: _Wakeups
    trace: list[str]

    async def close(
        self,
        context: AttemptContext,
        control: RunAttemptControl,
        driver: HarnessDriver,
    ) -> None:
        del context, control, driver
        assert self.wakeups.stopped.is_set()
        self.trace.append("attempt:cleanup")


@dataclass
class _CapacitySlot:
    trace: list[str]
    releases: int = 0

    def release(self) -> None:
        self.releases += 1
        self.trace.append("capacity:release")


@dataclass
class _DriverPort:
    steered: list[object] = field(default_factory=list)
    cancellations: int = 0

    async def steer(self, input) -> str:
        self.steered.append(input)
        return f"enqueue-{len(self.steered)}"

    async def cancel(self) -> None:
        self.cancellations += 1

    async def export_state(self) -> HarnessState:
        return HarnessState.new()

    def validate_binding(self, binding: HarnessContextBinding) -> None:
        del binding

    def validate_boundary(self, boundary: HarnessHookBoundary) -> None:
        del boundary


async def _model(
    messages: list[ModelMessage],
    info: AgentInfo,
) -> AsyncIterator[str]:
    del messages, info
    yield "done"


async def _stored_state(
    objects: ObjectStore,
    envelope: RunStateEnvelope,
) -> tuple[RunStateStore, StoredRunState]:
    states = RunStateStore(objects)
    return states, await states.create(ORGANIZATION_ID, envelope)


def _context(thread_id: str, *, renewal_interval: timedelta = timedelta(milliseconds=1)) -> AttemptContext:
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
        renewal_interval=renewal_interval,
        renewal_timeout=timedelta(seconds=5),
        reconciliation_timeout=timedelta(seconds=5),
        cleanup_timeout=timedelta(seconds=5),
    )


def _receipt(
    context: AttemptContext,
    *,
    run_delta: int = 0,
    attempt_delta: int = 0,
) -> AttemptMutationReceipt:
    return AttemptMutationReceipt(
        run_version=context.expected_run_version + run_delta,
        attempt_version=context.expected_attempt_version + attempt_delta,
        lease_expires_at=NOW + timedelta(minutes=5),
    )


def _preparation(context: AttemptContext) -> AttemptPreparationAccepted:
    return AttemptPreparationAccepted(
        run_attempt_id=context.run_attempt_id,
        fence=context.fence,
        mutation=_receipt(context),
    )


def _terminal_receipt(context: AttemptContext) -> RunTerminalReceipt:
    return RunTerminalReceipt(
        disposition=RunTerminalDisposition.completed,
        run_version=context.expected_run_version + 1,
        attempt_version=context.expected_attempt_version + 1,
        thread_version=2,
    )


@pytest.mark.parametrize("reject_preparation", [False, True])
async def test_executor_supervises_two_children_before_cleanup_and_capacity_release(
    interaction_object_store: ObjectStore,
    reject_preparation: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace: list[str] = []
    envelope = initial_state()
    states, stored = await _stored_state(interaction_object_store, envelope)
    context = _context(envelope.thread_id)
    execution = _Execution(trace, reject_preparation=reject_preparation)
    inbox = _Inbox(trace)
    control = RunAttemptControl(
        context=context,
        execution=execution,
        states=states,
        state=stored,
        inbox=inbox,
    )
    projector = _Projector()
    driver = HarnessDriver(
        HarnessBuilder(instrumentation=None),
        control=control,
        projector=projector,
    )
    wakeups = _Wakeups(trace)
    capacity = _CapacitySlot(trace)
    invocation = HarnessInvocation(
        definition=AgentDefinition(
            agent=AgentSpec(),
            output_type=str,
            model=FunctionModel(stream_function=_model),
        ),
        input=ImmediateHarnessInput("hello"),
        collaborators=HarnessCollaborators(
            instance=AgentInstanceContext(
                identity=AgentIdentityRef(issuer="foundation", subject="test-user"),
                agent_instance_id="instance-1",
                actor="user:test-user",
                host_refs={"session_id": "session-1"},
            )
        ),
    )
    environment_preparer = AsyncMock(return_value=None)
    monkeypatch.setattr("a13n_service.interactions.attempt_executor.prepare_run_environment", environment_preparer)
    lifecycle = Mock(spec=EnvironmentLifecycle)
    executor = RunAttemptExecutor(
        environments=lifecycle,
        context=context,
        control=control,
        driver=driver,
        preparer=_Preparer(context, invocation, wakeups, execution.heartbeat_seen, trace),
        wakeups=wakeups,
        adapter=_Adapter(),
        committer=_Committer(trace),
        cleanup=_Cleanup(wakeups, trace),
        capacity_slot=capacity,
    )

    receipt = await executor.run()
    environment_preparer.assert_awaited_once_with(lifecycle, context)
    await control.reconcile()
    await control.renew_lease()

    if reject_preparation:
        assert isinstance(receipt, AttemptPreparationRejected)
        assert receipt.failure.code == "preparation_rejected"
        assert "attempt:enter" not in trace
        assert not projector.events
    else:
        assert isinstance(receipt, RunTerminalReceipt)
        assert receipt.disposition is RunTerminalDisposition.completed
        assert projector.events
    assert execution.heartbeat_seen.is_set()
    assert wakeups.acknowledged.is_set()
    assert trace.index("inbox:confirm") < trace.index("attempt:prepare")
    assert trace[-2:] == ["attempt:cleanup", "capacity:release"]
    assert capacity.releases == 1


async def test_lease_monitor_fences_control_and_cancels_scope_on_authority_loss(
    interaction_object_store: ObjectStore,
) -> None:
    trace: list[str] = []
    envelope = initial_state()
    states, stored = await _stored_state(interaction_object_store, envelope)
    context = _context(envelope.thread_id)
    control = RunAttemptControl(
        context=context,
        execution=_Execution(trace, lose_heartbeat=True),
        states=states,
        state=stored,
        inbox=_Inbox(trace),
    )
    driver = _DriverPort()
    cancellations: list[str] = []
    control.bind_executor(driver, lambda: cancellations.append("scope"))

    with pytest.raises(AttemptAuthorityError):
        await LeaseMonitor(context, control).run()

    assert driver.cancellations == 1
    assert cancellations == ["scope"]
    with pytest.raises(RunError) as error:
        await control.reconcile()
    assert error.value.code == "foundation_control_fenced"


async def test_control_watcher_acknowledges_only_after_each_durable_reconciliation(
    interaction_object_store: ObjectStore,
) -> None:
    trace: list[str] = []
    envelope = initial_state()
    states, stored = await _stored_state(interaction_object_store, envelope)
    control = RunAttemptControl(
        context=_context(envelope.thread_id),
        execution=_Execution(trace),
        states=states,
        state=stored,
        inbox=_Inbox(trace),
    )
    wakeups = _Wakeups(trace)

    async with create_task_group() as tasks:
        tasks.start_soon(ControlWatcher(control.current_context, control, wakeups).run)
        await wakeups.acknowledged.wait()
        tasks.cancel_scope.cancel()

    assert trace[:6] == [
        "attempt:validate",
        "inbox:confirm",
        "wakeup:receive",
        "attempt:validate",
        "inbox:confirm",
        "wakeup:ack",
    ]


async def test_active_reconciliation_uses_driver_steer_without_consuming_receipt(
    interaction_object_store: ObjectStore,
) -> None:
    trace: list[str] = []
    envelope = initial_state()
    states, stored = await _stored_state(interaction_object_store, envelope)
    context = _context(envelope.thread_id)
    receipt = ConsumedThreadInboxEntry(inbox_entry_id="inbx_1234567890abcdef", kind="message")
    control = RunAttemptControl(
        context=context,
        execution=_Execution(trace),
        states=states,
        state=stored,
        inbox=_Inbox(trace, entries=(AdaptedThreadInboxEntry(1, receipt, "steer"),)),
    )
    driver = _DriverPort()
    control.bind_executor(driver, lambda: None)
    await control.enter_harness(
        HarnessRunIdentity(thread_id=envelope.thread_id, run_id="harness-run-1"),
        _preparation(context),
    )

    await control.reconcile()

    assert driver.steered == ["steer"]
    assert control.current_state.envelope.host.consumed_inbox_entries == ()
