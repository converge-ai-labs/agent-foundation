from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
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
from a13n_harness.errors import DefinitionError, RunError
from a13n_service.iam.attempts import AttemptAuthorization
from a13n_service.interactions.attempt_executor import ControlWatcher, LeaseMonitor, RunAttemptExecutor
from a13n_service.interactions.attempts import (
    AttemptAuthorityError,
    AttemptContext,
    AttemptExecutionService,
    AttemptLease,
    AttemptMutationReceipt,
    AttemptPreparationAccepted,
    AttemptPreparationRejected,
    AttemptPreparationResult,
)
from a13n_service.interactions.domain import RunAttemptYieldReason
from a13n_service.interactions.environment_observation import EnvironmentHookObservation
from a13n_service.interactions.harness_control import HarnessContextBinding, HarnessHookBoundary, HarnessRunIdentity
from a13n_service.interactions.harness_results import (
    AttemptDisposition,
    AttemptOutcome,
    HarnessOutcomeProjection,
)
from a13n_service.interactions.harness_runtime import (
    HarnessCollaborators,
    HarnessDriver,
    HarnessInvocation,
    ImmediateHarnessInput,
)
from a13n_service.interactions.inbox_delivery import AdaptedThreadInboxEntry
from a13n_service.interactions.objects import RunStateStore, StoredRunState
from a13n_service.interactions.outcomes import VerifiedRunOutcome
from a13n_service.interactions.run_control import RunAttemptControl
from a13n_service.interactions.state import CompletedOutcomeCandidate, InboxReceipt, RunCheckpoint
from a13n_service.storage import ObjectStore
from anyio import Event, create_task_group, sleep, sleep_forever
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

    async def ingest_usage(self, authority, *, harness_run_id, records):
        assert harness_run_id
        assert all(record.record_id for record in records)

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
                attempt_number=context.attempt_number,
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

    async def can_handoff(self, context: AttemptContext) -> bool:
        return True

    async def yield_attempt(self, context: AttemptContext, reason: RunAttemptYieldReason) -> AttemptMutationReceipt:
        self.trace.append("attempt:yield")
        return _receipt(context, run_delta=1, attempt_delta=1)

    async def increment_model_request(self, context: AttemptContext) -> AttemptMutationReceipt:
        self.trace.append("attempt:model")
        return _receipt(context, attempt_delta=1)


@dataclass
class _Inbox:
    trace: list[str]
    entries: tuple[AdaptedThreadInboxEntry, ...] = ()
    _delivered: bool = field(default=False, init=False)

    async def confirm_inbox_receipts(
        self,
        context: AttemptContext,
        state: StoredRunState,
    ) -> AttemptMutationReceipt:
        assert state.envelope.run_id == context.run_id
        self.trace.append("inbox:confirm")
        return _receipt(context)

    async def read_eligible(self, context: AttemptContext, config) -> Sequence[AdaptedThreadInboxEntry]:
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

    async def claim_state_writer(self):
        self.trace.append("attempt:claim-state")

    async def validate_dependencies(self, context: AttemptContext) -> None:
        assert context.run_attempt_id == self.context.run_attempt_id
        await self.wakeups.receiving.wait()
        await self.heartbeat_seen.wait()
        self.trace.append("attempt:validate-dependencies")

    @asynccontextmanager
    async def open_runtime(self, context: AttemptContext) -> AsyncIterator[HarnessInvocation[str]]:
        assert context.run_attempt_id == self.context.run_attempt_id
        self.trace.append("attempt:prepare")
        try:
            yield self.invocation
        finally:
            self.trace.append("attempt:resources-closed")


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

    async def verify_state_outcome(self, authority, state):
        return VerifiedRunOutcome(state, authority.organization_id, authority.run_id, self)

    async def commit_verified_state_outcome(self, context, verified):
        assert verified.state.envelope.checkpoint_kind == "completed"
        self.trace.append("attempt:terminal")
        return _terminal_receipt(context)

    async def commit_failure(self, context, failure):
        raise AssertionError("successful executor must not commit failure")

    async def reconcile_cancelled(self, context):
        raise AssertionError("successful executor must not reconcile cancellation")


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
    envelope: RunCheckpoint,
) -> tuple[RunStateStore, StoredRunState]:
    states = RunStateStore(objects)
    return states, await states.create(ORGANIZATION_ID, envelope)


def _context(thread_id: str, *, renewal_interval: timedelta = timedelta(milliseconds=1)) -> AttemptContext:
    return AttemptContext(
        organization_id=ORGANIZATION_ID,
        thread_id=thread_id,
        run_id=RUN_ID,
        run_attempt_id=ATTEMPT_ID,
        attempt_number=1,
        lease_token="lease-token",
        worker_id="worker-1",
        worker_build_id="build-1",
        lease_duration=timedelta(seconds=30),
        lease=AttemptLease(NOW + timedelta(seconds=30)),
        renewal_interval=renewal_interval,
        renewal_timeout=timedelta(seconds=5),
        reconciliation_timeout=timedelta(seconds=5),
        cleanup_timeout=timedelta(seconds=5),
        authorization=Mock(spec=AttemptAuthorization),
    )


def _receipt(
    context: AttemptContext,
    *,
    run_delta: int = 0,
    attempt_delta: int = 0,
) -> AttemptMutationReceipt:
    return AttemptMutationReceipt(
        run_version=1 + run_delta,
        attempt_version=1 + attempt_delta,
        lease_expires_at=NOW + timedelta(minutes=5),
    )


def _preparation(context: AttemptContext) -> AttemptPreparationAccepted:
    return AttemptPreparationAccepted(
        run_attempt_id=context.run_attempt_id,
        attempt_number=context.attempt_number,
        mutation=_receipt(context),
    )


def _terminal_receipt(context: AttemptContext) -> AttemptOutcome:
    return AttemptOutcome(
        disposition=AttemptDisposition.completed,
        run_version=1 + 1,
        attempt_version=1 + 1,
        thread_version=2,
    )


@pytest.mark.parametrize(
    ("reject_preparation", "preflight_code", "error_type"),
    [
        (False, None, RunError),
        (True, None, RunError),
        (False, "environment_required", RunError),
        (False, "search_provider_unavailable", RunError),
        (False, "web_operation_unavailable", RunError),
        (False, "untrusted_provider_code", RunError),
        (False, "skill_materialization_invalid", DefinitionError),
        (False, "skill_materialization_unavailable", DefinitionError),
        (False, "skill_materialization_stale", DefinitionError),
        (False, "untrusted_provider_code", DefinitionError),
    ],
)
async def test_executor_supervises_two_children_before_cleanup_and_capacity_release(
    interaction_object_store: ObjectStore,
    reject_preparation: bool,
    preflight_code: str | None,
    error_type: type[RunError] | type[DefinitionError],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
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
                identity=AgentIdentityRef(issuer="a13n.service", subject="test-user"),
                agent_instance_id="instance-1",
                actor="user:test-user",
                host_refs={"session_id": "session-1"},
            )
        ),
    )

    class ClosingPreparer(_Preparer):
        @asynccontextmanager
        async def open_runtime(self, context):
            async with super().open_runtime(context) as prepared:
                yield prepared
            read = AsyncMock(side_effect=AssertionError("Input cannot use closed runtime resources"))
            monkeypatch.setattr(inbox, "read_eligible", read)
            await control.reconcile()
            read.assert_not_awaited()

    committer = _Committer(trace)
    failure_commit = AsyncMock(return_value=_terminal_receipt(context))
    if preflight_code is not None:
        monkeypatch.setattr(
            ClosingPreparer,
            "validate_dependencies",
            AsyncMock(side_effect=error_type("private diagnostic", code=preflight_code)),
        )
        monkeypatch.setattr(committer, "commit_failure", failure_commit)
    executor = RunAttemptExecutor(
        activate_publication=AsyncMock(side_effect=lambda context: trace.append("publication:activate")),
        context=context,
        control=control,
        driver=driver,
        preparer=ClosingPreparer(context, invocation, wakeups, execution.heartbeat_seen, trace),
        wakeups=wakeups,
        adapter=_Adapter,
        committer=committer,
        capacity_slot=capacity,
    )

    receipt = await executor.run()
    if preflight_code is not None:
        failure_commit.assert_awaited_once()
        failure = failure_commit.call_args.args[1]
        expected = preflight_code if preflight_code != "untrusted_provider_code" else "attempt_execution_failed"
        assert failure.code == expected
        assert failure.message == "The RunAttempt could not complete execution."
        record = next(record for record in caplog.records if record.msg == "run_attempt_execution_failed")
        assert record.run_id == RUN_ID and record.attempt_number == context.attempt_number
        assert record.exception_chain[0]["type"] == f"a13n_harness.errors.{error_type.__name__}"
        assert record.exception_chain[0]["frames"] and record.exc_info is None
        assert "private diagnostic" not in str(record.exception_chain)
        assert "attempt:enter" not in trace
        assert not projector.events
        assert capacity.releases == 1
        return
    await control.reconcile()
    await control.renew_lease()

    if reject_preparation:
        assert isinstance(receipt, AttemptPreparationRejected)
        assert receipt.failure.code == "preparation_rejected"
        assert "attempt:enter" not in trace
        assert not projector.events
    else:
        assert isinstance(receipt, AttemptOutcome)
        assert receipt.disposition is AttemptDisposition.completed
        assert projector.events
    assert trace.index("publication:activate") < trace.index("attempt:claim-state")
    assert execution.heartbeat_seen.is_set()
    assert wakeups.acknowledged.is_set()
    if reject_preparation:
        assert "inbox:confirm" not in trace
        assert "attempt:prepare" not in trace
        assert "attempt:resources-closed" not in trace
    else:
        assert trace.index("attempt:preparation-commit") < trace.index("inbox:confirm") < trace.index("attempt:prepare")
        assert trace.count("attempt:resources-closed") == 1
    assert trace[-1] == "capacity:release"
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
    with pytest.raises(AttemptAuthorityError):
        context.lease.require_current(NOW)
    with pytest.raises(RunError) as error:
        await control.reconcile()
    assert error.value.code == "service_control_fenced"


async def test_control_watcher_acknowledges_before_durable_reconciliation(
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

    assert trace == [
        "attempt:validate",
        "wakeup:receive",
        "wakeup:ack",
        "attempt:validate",
    ]


async def test_watcher_restart_reconciles_after_post_ack_failure_without_redelivery(
    interaction_object_store: ObjectStore,
    monkeypatch: pytest.MonkeyPatch,
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
    reconcile = control.reconcile
    calls = 0

    async def reconcile_with_failure():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ConnectionError("PostgreSQL temporarily unavailable")
        await reconcile()

    monkeypatch.setattr(control, "reconcile", reconcile_with_failure)
    wakeups = _Wakeups(trace)
    with pytest.raises(ConnectionError):
        await ControlWatcher(control.current_context, control, wakeups).run()
    assert wakeups.acknowledged.is_set()
    assert trace == ["attempt:validate", "wakeup:receive", "wakeup:ack"]

    # The acknowledged signal is gone. Startup reconciliation runs before receive.
    async with create_task_group() as tasks:
        await tasks.start(ControlWatcher(control.current_context, control, wakeups).run)
        tasks.cancel_scope.cancel()
    assert calls == 3
    assert trace.count("attempt:validate") == 2
    assert trace.count("wakeup:ack") == 1


async def test_active_reconciliation_uses_driver_steer_without_consuming_receipt(
    interaction_object_store: ObjectStore,
) -> None:
    trace: list[str] = []
    envelope = initial_state()
    states, stored = await _stored_state(interaction_object_store, envelope)
    context = _context(envelope.thread_id)
    receipt = InboxReceipt(inbox_entry_id="inbx_1234567890abcdef", kind="message")
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

    await control.bind_model_attempt(HarnessContextBinding(object(), object(), object()))
    await control.reconcile()

    assert driver.steered == [AdaptedThreadInboxEntry(1, receipt, "steer").tagged_input(context.run_id)]
    assert control.current_state.envelope.host.inbox_receipts == ()


@pytest.mark.parametrize("failure", [None, "cleanup", "yield"])
async def test_handoff_closes_runtime_while_renewing_before_yield(
    interaction_object_store,
    monkeypatch,
    failure,
):
    trace = []
    envelope = initial_state()
    states, stored = await _stored_state(interaction_object_store, envelope)
    context = _context(envelope.thread_id)
    execution = _Execution(trace)
    control = RunAttemptControl(context=context, execution=execution, states=states, state=stored, inbox=_Inbox(trace))
    driver = HarnessDriver(HarnessBuilder(instrumentation=None), control=control, projector=_Projector())
    wakeups = _Wakeups(trace)
    capacity = _CapacitySlot(trace)
    invocation = HarnessInvocation(
        definition=AgentDefinition(agent=AgentSpec(), output_type=str, model=FunctionModel(stream_function=_model)),
        input=ImmediateHarnessInput("accepted input"),
        collaborators=HarnessCollaborators(
            instance=AgentInstanceContext(
                identity=AgentIdentityRef(issuer="a13n.service", subject="test-user"),
                agent_instance_id="instance-1",
                actor="user:test-user",
            )
        ),
    )

    class ClosingPreparer(_Preparer):
        @asynccontextmanager
        async def open_runtime(self, context):
            async with super().open_runtime(context) as prepared:
                yield prepared
                trace.append("cleanup:start")
                await sleep(0.01)
                if failure == "cleanup":
                    raise RuntimeError("cleanup unavailable")
                trace.append("cleanup:end")

    if failure == "yield":
        monkeypatch.setattr(execution, "yield_attempt", AsyncMock(side_effect=RuntimeError("PG unavailable")))
    await control.request_handoff(RunAttemptYieldReason.service_drain)
    executor = RunAttemptExecutor(
        activate_publication=AsyncMock(side_effect=lambda context: trace.append("publication:activate")),
        context=context,
        control=control,
        driver=driver,
        preparer=ClosingPreparer(context, invocation, wakeups, execution.heartbeat_seen, trace),
        wakeups=wakeups,
        adapter=_Adapter,
        committer=_Committer(trace),
        capacity_slot=capacity,
    )
    if failure is not None:
        with pytest.raises((ExceptionGroup, RuntimeError)):
            await executor.run()
        assert "attempt:yield" not in trace
        if failure == "yield":
            assert trace.count("cleanup:start") == trace.count("cleanup:end") == 1
    else:
        await executor.run()
        start, end = trace.index("cleanup:start"), trace.index("cleanup:end")
        assert "attempt:heartbeat" in trace[start:end]
        assert end < trace.index("attempt:yield") < trace.index("capacity:release")
    assert wakeups.stopped.is_set()
    assert capacity.releases == 1
