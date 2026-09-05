from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from a13n_service.agents.invocation_resolution import FrozenAgentInvocation
from a13n_service.durable_operations.models import IdempotencyEvidenceRecord
from a13n_service.endpoint_policy import EndpointPolicy
from a13n_service.gateway.commands import (
    ForkRunRequest,
    GatewayCommandError,
    NativeInteractionCommands,
    RetryRunRequest,
    StartRunRequest,
    WaitingContinueRunRequest,
)
from a13n_service.iam import AuthenticatedActor, PrincipalRef, PrincipalType
from a13n_service.interactions.acceptance import RunAcceptanceService
from a13n_service.interactions.attempts import AttemptExecutionService, AttemptPreparationAccepted
from a13n_service.interactions.control_domain import InterruptRequest, WaitingRunFeedbackRequest
from a13n_service.interactions.control_models import ThreadInboxRecord
from a13n_service.interactions.inbox import ThreadInboxStore
from a13n_service.interactions.models import RunRecord, SessionRecord, ThreadRecord
from a13n_service.interactions.objects import RunPayloadStore, RunStateStore
from a13n_service.interactions.outcomes import RunOutcomeService
from a13n_service.interactions.scheduling import AttemptScheduler, ClaimedAttempt
from a13n_service.interactions.state import CompletedOutcomeCandidate, ConsumedThreadInboxEntry
from a13n_service.storage import short_session
from a13n_service.storage.object_store import LocalObjectStore
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.hooks.support import seed_hook_actor_access
from tests.interactions.conftest import (
    AGENT_ID,
    AGENT_REVISION_ID,
    NOW,
    TENANT_ID,
    USER_ID,
    WORKSPACE_ID,
    effective_agent_config,
)
from tests.interactions.test_acceptance import _inline_hooks
from tests.interactions.test_attempt_execution import _authority, _completed_state, _waiting_state, _worker

pytestmark = pytest.mark.anyio


def _actor() -> AuthenticatedActor:
    return AuthenticatedActor(
        principal=PrincipalRef(principal_type=PrincipalType.user, principal_id=USER_ID),
        auth_method="session",
        credential_id="ses_gateway_test",
        boundary_workspace_id=WORKSPACE_ID,
    )


def _frozen(*, runtime_lock_digest: str = "a" * 64) -> FrozenAgentInvocation:
    config = effective_agent_config().model_copy(update={"runtime_lock_digest": runtime_lock_digest})
    return FrozenAgentInvocation(
        agent_id=AGENT_ID,
        agent_revision_id=AGENT_REVISION_ID,
        selector_kind="current",
        effective_config=config,
        sensitive_values={},
        sensitive_values_digest="0" * 64,
        connector_connection_selections=(),
        mcp_connection_selections=(),
    )


class _Preparation:
    def __init__(self) -> None:
        self.calls = 0

    async def prepare(self, **_kwargs):
        self.calls += 1
        return SimpleNamespace(organization_id=TENANT_ID)


class _Freezing:
    def __init__(self, values: list[FrozenAgentInvocation]) -> None:
        self._values = values
        self.calls = 0

    async def freeze_in_transaction(self, _database, *, prepared):
        del prepared
        selected = self._values[min(self.calls, len(self._values) - 1)]
        self.calls += 1
        return selected


def _request(text: str = "hello") -> StartRunRequest:
    return StartRunRequest.model_validate(
        {
            "agent_id": AGENT_ID,
            "input": {"schema_version": "2", "content": [{"type": "text", "text": text}]},
        }
    )


def _commands(
    sessions: async_sessionmaker[AsyncSession],
    objects,
    preparation: _Preparation,
    freezing: _Freezing,
    assets=None,
) -> NativeInteractionCommands:
    resolver = SimpleNamespace(preparation=preparation, freezing=freezing)
    payloads = RunPayloadStore(objects)
    acceptance = RunAcceptanceService(
        sessions,
        RunStateStore(objects),
        payloads,
        _inline_hooks(),
        clock=lambda: NOW,
    )
    return NativeInteractionCommands(
        sessions,
        resolver,
        acceptance,
        RunStateStore(objects),
        assets if assets is not None else AsyncMock(),
        EndpointPolicy(),
        outcomes=RunOutcomeService(sessions, payloads, clock=lambda: NOW),
        inbox=ThreadInboxStore(sessions, clock=lambda: NOW),
        payloads=payloads,
        clock=lambda: NOW,
    )


async def _complete_run(
    sessions: async_sessionmaker[AsyncSession],
    objects: LocalObjectStore,
    *,
    run_id: str,
    expected_thread_version: int = 1,
    consumed_entries: tuple[ConsumedThreadInboxEntry, ...] = (),
) -> None:
    states = RunStateStore(objects)
    claim = await AttemptScheduler(
        sessions,
        clock=lambda: NOW + timedelta(seconds=1),
        token_factory=lambda: "gateway-lease-secret",
    ).claim(run_id, _worker())
    assert isinstance(claim, ClaimedAttempt)
    execution = AttemptExecutionService(sessions, clock=lambda: NOW + timedelta(seconds=2))
    authority = _authority(claim)
    preparation = await execution.commit_preparation_success(authority)
    assert isinstance(preparation, AttemptPreparationAccepted)
    entered = await execution.enter_harness(
        authority,
        preparation=preparation,
        harness_run_id=f"harness-{run_id}",
    )
    authority = _authority(
        claim,
        run_version=entered.run_version,
        attempt_version=entered.attempt_version,
    )
    current = await states.read(TENANT_ID, run_id)
    candidate = _completed_state(
        current.envelope,
        claim.attempt.id,
        claim.attempt.fence,
        outcome=CompletedOutcomeCandidate(output={"answer": 42}),
    )
    candidate = candidate.model_copy(
        update={"host": candidate.host.model_copy(update={"consumed_inbox_entries": consumed_entries})}
    )
    stored = await execution.publish_checkpoint(authority, states, current, candidate)
    await RunOutcomeService(
        sessions,
        RunPayloadStore(objects),
        clock=lambda: NOW + timedelta(seconds=3),
    ).commit_state_outcome(authority, stored, expected_thread_version=expected_thread_version)


async def _wait_run(
    sessions: async_sessionmaker[AsyncSession],
    objects: LocalObjectStore,
    *,
    run_id: str,
    pending_kind: str = "approval",
) -> str:
    states = RunStateStore(objects)
    claim = await AttemptScheduler(
        sessions,
        clock=lambda: NOW + timedelta(seconds=1),
        token_factory=lambda: "gateway-waiting-lease-secret",
    ).claim(run_id, _worker())
    assert isinstance(claim, ClaimedAttempt)
    execution = AttemptExecutionService(sessions, clock=lambda: NOW + timedelta(seconds=2))
    authority = _authority(claim)
    preparation = await execution.commit_preparation_success(authority)
    assert isinstance(preparation, AttemptPreparationAccepted)
    entered = await execution.enter_harness(
        authority,
        preparation=preparation,
        harness_run_id=f"harness-{run_id}",
    )
    authority = _authority(
        claim,
        run_version=entered.run_version,
        attempt_version=entered.attempt_version,
    )
    current = await states.read(TENANT_ID, run_id)
    waiting = _waiting_state(current.envelope, claim.attempt.id, claim.attempt.fence)
    if pending_kind == "client_tool":
        payload = waiting.model_dump(mode="python", by_alias=True)
        payload["host"]["deferred"] = {
            "requests": {
                "calls": [
                    {
                        "tool_name": "lookup_order",
                        "args": {"order_id": "order-1"},
                        "tool_call_id": "client-tool-1",
                    }
                ],
                "approvals": [],
                "metadata": {},
            },
            "effective_client_tool_surface": {"tools": ["lookup_order"]},
            "effective_surface_digest_sha256": "b" * 64,
        }
        payload["outcome_candidate"] = {
            "outcome": "waiting",
            "wait_reason": "client_tool",
            "pending": {
                "calls": [
                    {
                        "call_id": "client-tool-1",
                        "kind": "client_tool",
                        "tool_name": "lookup_order",
                    }
                ]
            },
        }
        waiting = type(waiting).model_validate(payload)
    elif pending_kind != "approval":
        raise ValueError("unsupported pending test kind")
    stored = await execution.publish_checkpoint(
        authority,
        states,
        current,
        waiting,
    )
    await RunOutcomeService(
        sessions,
        RunPayloadStore(objects),
        clock=lambda: NOW + timedelta(seconds=3),
    ).commit_state_outcome(authority, stored, expected_thread_version=1)
    return stored.digest_sha256


async def test_start_accepts_root_run_and_replays_before_resolution(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    tmp_path,
) -> None:
    interaction_sessions = lifecycle_interaction_sessions
    interaction_object_store = await LocalObjectStore.create(tmp_path / "objects")
    preparation = _Preparation()
    freezing = _Freezing([_frozen()])
    commands = _commands(interaction_sessions, interaction_object_store, preparation, freezing)

    first = await commands.start(
        actor=_actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="start-one",
        request=_request(),
    )
    repeated = await commands.start(
        actor=_actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="start-one",
        request=_request(),
    )

    assert repeated == first
    assert preparation.calls == 1
    assert freezing.calls == 2
    assert first.thread_id.startswith("thread-")
    async with short_session(interaction_sessions) as database:
        run = await database.scalar(select(RunRecord).where(RunRecord.id == first.run_id))
        thread = await database.scalar(select(ThreadRecord).where(ThreadRecord.id == first.thread_id))
        session = await database.scalar(select(SessionRecord).where(SessionRecord.id == first.session_id))
    assert run is not None and run.input_text == "hello"
    assert thread is not None and thread.current_run_id == run.id
    assert session is not None and session.workspace_id == WORKSPACE_ID


async def test_start_rejects_idempotency_key_reuse_with_changed_request(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    tmp_path,
) -> None:
    interaction_sessions = lifecycle_interaction_sessions
    interaction_object_store = await LocalObjectStore.create(tmp_path / "objects")
    commands = _commands(interaction_sessions, interaction_object_store, _Preparation(), _Freezing([_frozen()]))
    await commands.start(
        actor=_actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="start-conflict",
        request=_request("first"),
    )

    with pytest.raises(GatewayCommandError) as captured:
        await commands.start(
            actor=_actor(),
            workspace_id=WORKSPACE_ID,
            idempotency_key="start-conflict",
            request=_request("different"),
        )

    assert captured.value.code == "idempotency_conflict"
    assert captured.value.status_code == 409


async def test_start_rejects_second_root_thread_in_existing_session(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    tmp_path,
) -> None:
    objects = await LocalObjectStore.create(tmp_path / "objects")
    commands = _commands(
        lifecycle_interaction_sessions,
        objects,
        _Preparation(),
        _Freezing([_frozen()]),
    )
    first = await commands.start(
        actor=_actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="first-root",
        request=_request(),
    )

    with pytest.raises(GatewayCommandError) as captured:
        await commands.start(
            actor=_actor(),
            workspace_id=WORKSPACE_ID,
            idempotency_key="second-root",
            request=_request("second").model_copy(update={"session_id": first.session_id}),
        )

    assert captured.value.code == "session_root_exists"
    assert captured.value.status_code == 409
    async with short_session(lifecycle_interaction_sessions) as database:
        roots = tuple(
            (
                await database.scalars(
                    select(ThreadRecord).where(
                        ThreadRecord.session_id == first.session_id,
                        ThreadRecord.role == "root",
                    )
                )
            ).all()
        )
    assert len(roots) == 1


async def test_start_rejects_final_invocation_drift_without_committing_run(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    tmp_path,
) -> None:
    interaction_sessions = lifecycle_interaction_sessions
    interaction_object_store = await LocalObjectStore.create(tmp_path / "objects")
    commands = _commands(
        interaction_sessions,
        interaction_object_store,
        _Preparation(),
        _Freezing([_frozen(), _frozen(runtime_lock_digest="b" * 64)]),
    )

    with pytest.raises(GatewayCommandError) as captured:
        await commands.start(
            actor=_actor(),
            workspace_id=WORKSPACE_ID,
            idempotency_key="start-drift",
            request=_request(),
        )

    assert captured.value.code == "run_invocation_changed"
    async with short_session(interaction_sessions) as database:
        count = len(tuple((await database.scalars(select(RunRecord).where(RunRecord.input_text == "hello"))).all()))
    assert count == 0


async def test_interrupt_is_atomic_and_replays_exact_stable_receipt(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    tmp_path,
) -> None:
    interaction_object_store = await LocalObjectStore.create(tmp_path / "objects")
    commands = _commands(
        lifecycle_interaction_sessions,
        interaction_object_store,
        _Preparation(),
        _Freezing([_frozen()]),
    )
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    accepted = await commands.start(
        actor=_actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="interrupt-source",
        request=_request(),
    )
    request = InterruptRequest(expected_run_version=1, expected_thread_version=1)

    first = await commands.interrupt(
        actor=_actor(),
        run_id=accepted.run_id,
        idempotency_key="interrupt-one",
        request=request,
    )
    repeated = await commands.interrupt(
        actor=_actor(),
        run_id=accepted.run_id,
        idempotency_key="interrupt-one",
        request=request,
    )

    assert repeated == first
    assert first.status == "cancelled"
    async with short_session(lifecycle_interaction_sessions) as database:
        run = await database.scalar(select(RunRecord).where(RunRecord.id == accepted.run_id))
        thread = await database.scalar(select(ThreadRecord).where(ThreadRecord.id == accepted.thread_id))
        evidence = tuple((await database.scalars(select(IdempotencyEvidenceRecord))).all())
    assert run is not None and run.status == "cancelled" and run.version == 2
    assert thread is not None and thread.version == 2
    assert len(evidence) == 1


async def test_interrupt_idempotency_conflicts_before_terminal_precondition_check(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    tmp_path,
) -> None:
    interaction_object_store = await LocalObjectStore.create(tmp_path / "objects")
    commands = _commands(
        lifecycle_interaction_sessions,
        interaction_object_store,
        _Preparation(),
        _Freezing([_frozen()]),
    )
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    accepted = await commands.start(
        actor=_actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="interrupt-conflict-source",
        request=_request(),
    )
    await commands.interrupt(
        actor=_actor(),
        run_id=accepted.run_id,
        idempotency_key="interrupt-conflict",
        request=InterruptRequest(expected_run_version=1, expected_thread_version=1),
    )

    with pytest.raises(GatewayCommandError) as captured:
        await commands.interrupt(
            actor=_actor(),
            run_id=accepted.run_id,
            idempotency_key="interrupt-conflict",
            request=InterruptRequest(expected_run_version=2, expected_thread_version=2),
        )

    assert captured.value.code == "idempotency_conflict"


async def test_retry_copies_cancelled_root_intent_and_replays(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    tmp_path,
) -> None:
    interaction_object_store = await LocalObjectStore.create(tmp_path / "objects")
    commands = _commands(
        lifecycle_interaction_sessions,
        interaction_object_store,
        _Preparation(),
        _Freezing([_frozen()]),
    )
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    source_receipt = await commands.start(
        actor=_actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="retry-source",
        request=_request("same intent"),
    )
    await commands.interrupt(
        actor=_actor(),
        run_id=source_receipt.run_id,
        idempotency_key="retry-source-cancel",
        request=InterruptRequest(expected_run_version=1, expected_thread_version=1),
    )

    request = RetryRunRequest(expected_thread_version=2)
    first = await commands.retry(
        actor=_actor(),
        run_id=source_receipt.run_id,
        idempotency_key="retry-one",
        request=request,
    )
    repeated = await commands.retry(
        actor=_actor(),
        run_id=source_receipt.run_id,
        idempotency_key="retry-one",
        request=request,
    )

    assert repeated == first
    assert first.thread_id == source_receipt.thread_id
    assert first.thread_version == 3
    async with short_session(lifecycle_interaction_sessions) as database:
        source = await database.scalar(select(RunRecord).where(RunRecord.id == source_receipt.run_id))
        retried = await database.scalar(select(RunRecord).where(RunRecord.id == first.run_id))
        thread = await database.scalar(select(ThreadRecord).where(ThreadRecord.id == first.thread_id))
    assert source is not None and retried is not None and thread is not None
    assert retried.retry_of_run_id == source.id
    assert retried.parent_run_id is None
    assert retried.input_json == source.input_json
    assert retried.authority_principal_id == source.authority_principal_id
    assert thread.current_run_id == retried.id


async def test_retry_rejects_terminal_run_after_thread_advances(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    tmp_path,
) -> None:
    interaction_object_store = await LocalObjectStore.create(tmp_path / "objects")
    commands = _commands(
        lifecycle_interaction_sessions,
        interaction_object_store,
        _Preparation(),
        _Freezing([_frozen()]),
    )
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    source = await commands.start(
        actor=_actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="stale-retry-source",
        request=_request(),
    )
    await commands.interrupt(
        actor=_actor(),
        run_id=source.run_id,
        idempotency_key="stale-retry-cancel",
        request=InterruptRequest(expected_run_version=1, expected_thread_version=1),
    )
    await commands.retry(
        actor=_actor(),
        run_id=source.run_id,
        idempotency_key="stale-retry-first",
        request=RetryRunRequest(expected_thread_version=2),
    )

    with pytest.raises(GatewayCommandError) as captured:
        await commands.retry(
            actor=_actor(),
            run_id=source.run_id,
            idempotency_key="stale-retry-second",
            request=RetryRunRequest(expected_thread_version=3),
        )

    assert captured.value.code == "run_not_retryable"


async def test_fork_creates_child_thread_and_replays(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    tmp_path,
) -> None:
    interaction_object_store = await LocalObjectStore.create(tmp_path / "objects")
    commands = _commands(
        lifecycle_interaction_sessions,
        interaction_object_store,
        _Preparation(),
        _Freezing([_frozen()]),
    )
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    source = await commands.start(
        actor=_actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="fork-source",
        request=_request("source"),
    )
    await _complete_run(
        lifecycle_interaction_sessions,
        interaction_object_store,
        run_id=source.run_id,
    )
    request = ForkRunRequest(input=_request("fork input").input)

    first = await commands.fork(
        actor=_actor(),
        run_id=source.run_id,
        idempotency_key="fork-one",
        request=request,
    )
    repeated = await commands.fork(
        actor=_actor(),
        run_id=source.run_id,
        idempotency_key="fork-one",
        request=request,
    )

    assert repeated == first
    assert first.session_id == source.session_id
    assert first.thread_id != source.thread_id
    async with short_session(lifecycle_interaction_sessions) as database:
        forked_run = await database.scalar(select(RunRecord).where(RunRecord.id == first.run_id))
        forked_thread = await database.scalar(select(ThreadRecord).where(ThreadRecord.id == first.thread_id))
    assert forked_run is not None and forked_thread is not None
    assert forked_run.parent_run_id == source.run_id
    assert forked_run.lineage_kind == "fork"
    assert forked_thread.role == "child"
    assert forked_thread.origin_kind == "fork"
    assert forked_thread.origin_thread_id == source.thread_id
    assert forked_thread.origin_run_id == source.run_id


async def test_fork_idempotency_rejects_changed_input(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    tmp_path,
) -> None:
    interaction_object_store = await LocalObjectStore.create(tmp_path / "objects")
    commands = _commands(
        lifecycle_interaction_sessions,
        interaction_object_store,
        _Preparation(),
        _Freezing([_frozen()]),
    )
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    source = await commands.start(
        actor=_actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="fork-conflict-source",
        request=_request(),
    )
    await _complete_run(
        lifecycle_interaction_sessions,
        interaction_object_store,
        run_id=source.run_id,
    )
    await commands.fork(
        actor=_actor(),
        run_id=source.run_id,
        idempotency_key="fork-conflict",
        request=ForkRunRequest(input=_request("first").input),
    )

    with pytest.raises(GatewayCommandError) as captured:
        await commands.fork(
            actor=_actor(),
            run_id=source.run_id,
            idempotency_key="fork-conflict",
            request=ForkRunRequest(input=_request("different").input),
        )

    assert captured.value.code == "idempotency_conflict"


async def test_feedback_advances_waiting_run_and_replays_semantically_equivalent_request(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    tmp_path,
) -> None:
    interaction_object_store = await LocalObjectStore.create(tmp_path / "objects")
    commands = _commands(
        lifecycle_interaction_sessions,
        interaction_object_store,
        _Preparation(),
        _Freezing([_frozen()]),
    )
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    source = await commands.start(
        actor=_actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="feedback-source",
        request=_request(),
    )
    digest = await _wait_run(
        lifecycle_interaction_sessions,
        interaction_object_store,
        run_id=source.run_id,
    )

    omitted = WaitingRunFeedbackRequest(
        expected_thread_version=2,
        sealed_state_digest_sha256=digest,
    )
    first = await commands.feedback(
        actor=_actor(),
        run_id=source.run_id,
        idempotency_key="feedback-one",
        request=omitted,
    )
    explicit_reject = WaitingRunFeedbackRequest.model_validate(
        {
            "expected_thread_version": 2,
            "sealed_state_digest_sha256": digest,
            "resolutions": [{"call_id": "approval-1", "action": "reject"}],
        }
    )
    repeated = await commands.feedback(
        actor=_actor(),
        run_id=source.run_id,
        idempotency_key="feedback-one",
        request=explicit_reject,
    )

    assert repeated == first
    assert first.thread_id == source.thread_id
    assert first.thread_version == 3
    async with short_session(lifecycle_interaction_sessions) as database:
        waiting = await database.scalar(select(RunRecord).where(RunRecord.id == source.run_id))
        successor = await database.scalar(select(RunRecord).where(RunRecord.id == first.run_id))
        thread = await database.scalar(select(ThreadRecord).where(ThreadRecord.id == source.thread_id))
    assert waiting is not None and waiting.status == "waiting"
    assert successor is not None and thread is not None
    assert successor.parent_run_id == waiting.id
    assert successor.input_kind == "waiting_feedback"
    assert successor.authority_principal_id == waiting.authority_principal_id
    assert successor.input_json["resolutions"] == [
        {
            "call_id": "approval-1",
            "kind": "approval",
            "outcome": "reject",
            "result": None,
        }
    ]
    assert thread.current_run_id == successor.id
    assert thread.head_run_id == waiting.id


async def test_feedback_rejects_changed_idempotent_intent_before_stale_head(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    tmp_path,
) -> None:
    interaction_object_store = await LocalObjectStore.create(tmp_path / "objects")
    commands = _commands(
        lifecycle_interaction_sessions,
        interaction_object_store,
        _Preparation(),
        _Freezing([_frozen()]),
    )
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    source = await commands.start(
        actor=_actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="feedback-conflict-source",
        request=_request(),
    )
    digest = await _wait_run(
        lifecycle_interaction_sessions,
        interaction_object_store,
        run_id=source.run_id,
    )
    rejected = WaitingRunFeedbackRequest(
        expected_thread_version=2,
        sealed_state_digest_sha256=digest,
    )
    await commands.feedback(
        actor=_actor(),
        run_id=source.run_id,
        idempotency_key="feedback-conflict",
        request=rejected,
    )
    approved = WaitingRunFeedbackRequest.model_validate(
        {
            "expected_thread_version": 2,
            "sealed_state_digest_sha256": digest,
            "resolutions": [{"call_id": "approval-1", "action": "approve"}],
        }
    )

    with pytest.raises(GatewayCommandError) as captured:
        await commands.feedback(
            actor=_actor(),
            run_id=source.run_id,
            idempotency_key="feedback-conflict",
            request=approved,
        )

    assert captured.value.code == "idempotency_conflict"


async def test_waiting_continue_defaults_feedback_and_preserves_new_input(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    tmp_path,
) -> None:
    interaction_object_store = await LocalObjectStore.create(tmp_path / "objects")
    commands = _commands(
        lifecycle_interaction_sessions,
        interaction_object_store,
        _Preparation(),
        _Freezing([_frozen()]),
    )
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    source = await commands.start(
        actor=_actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="waiting-continue-source",
        request=_request(),
    )
    digest = await _wait_run(
        lifecycle_interaction_sessions,
        interaction_object_store,
        run_id=source.run_id,
    )
    request = WaitingContinueRunRequest(
        expected_thread_version=2,
        sealed_state_digest_sha256=digest,
        input=_request("handle this instead").input,
    )

    first = await commands.continue_waiting(
        actor=_actor(),
        run_id=source.run_id,
        idempotency_key="waiting-continue-one",
        request=request,
    )
    repeated = await commands.continue_waiting(
        actor=_actor(),
        run_id=source.run_id,
        idempotency_key="waiting-continue-one",
        request=request,
    )

    assert repeated == first
    assert first.thread_version == 3
    async with short_session(lifecycle_interaction_sessions) as database:
        successor = await database.scalar(select(RunRecord).where(RunRecord.id == first.run_id))
        thread = await database.scalar(select(ThreadRecord).where(ThreadRecord.id == source.thread_id))
    assert successor is not None and thread is not None
    assert successor.input_kind == "waiting_continue"
    assert successor.parent_run_id == source.run_id
    assert successor.authority_principal_id == USER_ID
    assert successor.input_text == "handle this instead"
    assert successor.input_json["resolutions"][0]["outcome"] == "reject"
    assert successor.input_json["input"]["content"] == [{"type": "text", "text": "handle this instead"}]
    assert thread.current_run_id == successor.id
    assert thread.head_run_id == source.run_id


async def test_steer_is_atomic_replayable_and_does_not_advance_thread(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    tmp_path,
) -> None:
    interaction_object_store = await LocalObjectStore.create(tmp_path / "objects")
    commands = _commands(
        lifecycle_interaction_sessions,
        interaction_object_store,
        _Preparation(),
        _Freezing([_frozen()]),
    )
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    accepted = await commands.start(
        actor=_actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="steer-source",
        request=_request(),
    )

    first = await commands.steer(
        actor=_actor(),
        run_id=accepted.run_id,
        idempotency_key="steer-one",
        input=_request("follow up").input,
    )
    repeated = await commands.steer(
        actor=_actor(),
        run_id=accepted.run_id,
        idempotency_key="steer-one",
        input=_request("follow up").input,
    )
    status = await commands.get_steer(
        actor=_actor(),
        run_id=accepted.run_id,
        steer_id=first.steer_id,
    )

    assert repeated == first
    assert status.status == "pending"
    assert status.accepted_against_run_id == accepted.run_id
    assert status.target_run_id == accepted.run_id
    async with short_session(lifecycle_interaction_sessions) as database:
        thread = await database.scalar(select(ThreadRecord).where(ThreadRecord.id == accepted.thread_id))
        entries = tuple((await database.scalars(select(ThreadInboxRecord))).all())
        evidence = tuple((await database.scalars(select(IdempotencyEvidenceRecord))).all())
    assert thread is not None and thread.version == 1 and thread.queue_version == 0
    assert len(entries) == 1
    assert len(evidence) == 1


async def test_steer_idempotency_rejects_changed_input(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    tmp_path,
) -> None:
    interaction_object_store = await LocalObjectStore.create(tmp_path / "objects")
    commands = _commands(
        lifecycle_interaction_sessions,
        interaction_object_store,
        _Preparation(),
        _Freezing([_frozen()]),
    )
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    accepted = await commands.start(
        actor=_actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="steer-conflict-source",
        request=_request(),
    )
    await commands.steer(
        actor=_actor(),
        run_id=accepted.run_id,
        idempotency_key="steer-conflict",
        input=_request("first").input,
    )

    with pytest.raises(GatewayCommandError) as captured:
        await commands.steer(
            actor=_actor(),
            run_id=accepted.run_id,
            idempotency_key="steer-conflict",
            input=_request("different").input,
        )

    assert captured.value.code == "idempotency_conflict"
