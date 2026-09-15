from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import rfc8785
import zstandard
from a13n_service.agents.invocation_resolution import FrozenAgentInvocation
from a13n_service.durable_operations.models import IdempotencyEvidenceRecord
from a13n_service.endpoint_policy import EndpointPolicy
from a13n_service.gateway.queries import NativeInteractionQueries
from a13n_service.http_errors import application_error_status
from a13n_service.iam import AuthenticatedActor, PrincipalRef, PrincipalType
from a13n_service.interactions.acceptance import RunAcceptanceService
from a13n_service.interactions.attempts import AttemptExecutionService, AttemptPreparationAccepted
from a13n_service.interactions.command_values import (
    ContinueRunCommand,
    ForkRunCommand,
    RetryRunCommand,
    StartRunCommand,
    WaitingContinueRunCommand,
)
from a13n_service.interactions.commands import InteractionCommands
from a13n_service.interactions.control_domain import InterruptRequest, WaitingRunFeedbackRequest
from a13n_service.interactions.control_models import ThreadInboxRecord
from a13n_service.interactions.errors import InteractionCommandError
from a13n_service.interactions.inbox import ThreadInboxStore
from a13n_service.interactions.models import RunRecord, SessionRecord, ThreadRecord
from a13n_service.interactions.objects import RunObjectIntegrityError, RunPayloadStore, RunStateStore
from a13n_service.interactions.origin import SubmissionOrigin
from a13n_service.interactions.outcomes import RunOutcomeService
from a13n_service.interactions.scheduling import AttemptScheduler, ClaimedAttempt
from a13n_service.interactions.state import CompletedOutcomeCandidate, InboxReceipt
from a13n_service.run_stream import RunReplayStore
from a13n_service.storage import short_session, transaction
from a13n_service.storage.object_store import LocalObjectStore
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.hooks.support import seed_hook_actor_access
from tests.interactions.conftest import (
    AGENT_ID,
    AGENT_REVISION_ID,
    NOW,
    ORGANIZATION_ID,
    USER_ID,
    WORKSPACE_ID,
    effective_agent_config,
)
from tests.interactions.test_acceptance import _inline_hooks
from tests.interactions.test_attempt_execution import _authority, _completed_state, _waiting_state, _worker
from tests.lifecycle_support import test_lifecycle_writer

pytestmark = pytest.mark.anyio


def _actor() -> AuthenticatedActor:
    return AuthenticatedActor(
        principal=PrincipalRef(principal_type=PrincipalType.user, principal_id=USER_ID),
        auth_method="session",
        credential_id="ses_gateway_test",
        boundary_workspace_id=WORKSPACE_ID,
    )


def _frozen(*, content_digest: str | None = None) -> FrozenAgentInvocation:
    config = effective_agent_config()
    if content_digest is not None:
        config = config.model_copy(update={"content_digest": content_digest})
    return FrozenAgentInvocation(
        agent_id=AGENT_ID,
        agent_revision_id=AGENT_REVISION_ID,
        selector_kind="current",
        effective_config=config,
        connection_selections=(),
    )


class _Preparation:
    def __init__(self) -> None:
        self.calls = 0

    async def prepare(self, **_kwargs):
        self.calls += 1
        return SimpleNamespace(organization_id=ORGANIZATION_ID)


class _Freezing:
    def __init__(self, values: list[FrozenAgentInvocation]) -> None:
        self._values = values
        self.calls = 0

    async def freeze_in_transaction(self, _database, *, prepared):
        del prepared
        selected = self._values[min(self.calls, len(self._values) - 1)]
        self.calls += 1
        return selected


def _request(text: str = "hello") -> StartRunCommand:
    return StartRunCommand.model_validate(
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
    *,
    clock=lambda: NOW,
    queue_name="default",
    priority=0,
    execution_max_attempts=3,
    max_handoffs=2,
) -> InteractionCommands:
    resolver = SimpleNamespace(preparation=preparation, freezing=freezing)
    payloads = RunPayloadStore(objects)
    acceptance = RunAcceptanceService(
        sessions,
        RunStateStore(objects),
        payloads,
        _inline_hooks(),
        clock=lambda: NOW,
        lifecycle=test_lifecycle_writer(),
    )
    return InteractionCommands(
        sessions,
        resolver,
        acceptance,
        RunStateStore(objects),
        assets if assets is not None else AsyncMock(),
        EndpointPolicy(),
        outcomes=RunOutcomeService(sessions, payloads, clock=clock, lifecycle=test_lifecycle_writer()),
        inbox=ThreadInboxStore(sessions, clock=lambda: NOW),
        payloads=payloads,
        queue_name=queue_name,
        priority=priority,
        execution_max_attempts=execution_max_attempts,
        max_handoffs=max_handoffs,
        clock=clock,
    )


async def _complete_run(
    sessions: async_sessionmaker[AsyncSession],
    objects: LocalObjectStore,
    *,
    run_id: str,
    expected_thread_version: int = 1,
    consumed_entries: tuple[InboxReceipt, ...] = (),
    a2a_enabled: bool = True,
) -> None:
    states = RunStateStore(objects)
    claim = await AttemptScheduler(
        sessions,
        clock=lambda: NOW + timedelta(seconds=1),
        token_factory=lambda: "gateway-lease-secret",
        lifecycle=test_lifecycle_writer(a2a_enabled=a2a_enabled),
    ).claim(run_id, _worker())
    assert isinstance(claim, ClaimedAttempt)
    execution = AttemptExecutionService(
        sessions, clock=lambda: NOW + timedelta(seconds=2), lifecycle=test_lifecycle_writer(a2a_enabled=a2a_enabled)
    )
    authority = _authority(claim)
    preparation = await execution.commit_preparation_success(authority)
    assert isinstance(preparation, AttemptPreparationAccepted)
    await execution.enter_harness(
        authority,
        preparation=preparation,
        harness_run_id=f"harness-{run_id}",
    )
    current = await states.read(ORGANIZATION_ID, run_id)
    candidate = _completed_state(
        current.envelope,
        claim.attempt.id,
        claim.attempt.attempt_number,
        outcome=CompletedOutcomeCandidate(output={"answer": 42}),
    )
    candidate = candidate.model_copy(
        update={"host": candidate.host.model_copy(update={"inbox_receipts": consumed_entries})}
    )
    stored = await execution.publish_checkpoint(authority, states, current, candidate)
    await RunOutcomeService(
        sessions,
        RunPayloadStore(objects),
        clock=lambda: NOW + timedelta(seconds=3),
        lifecycle=test_lifecycle_writer(a2a_enabled=a2a_enabled),
    ).commit_state_outcome(authority, stored)


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
        lifecycle=test_lifecycle_writer(),
    ).claim(run_id, _worker())
    assert isinstance(claim, ClaimedAttempt)
    execution = AttemptExecutionService(
        sessions, clock=lambda: NOW + timedelta(seconds=2), lifecycle=test_lifecycle_writer()
    )
    authority = _authority(claim)
    preparation = await execution.commit_preparation_success(authority)
    assert isinstance(preparation, AttemptPreparationAccepted)
    await execution.enter_harness(
        authority,
        preparation=preparation,
        harness_run_id=f"harness-{run_id}",
    )
    current = await states.read(ORGANIZATION_ID, run_id)
    waiting = _waiting_state(current.envelope, claim.attempt.id, claim.attempt.attempt_number)
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
        sessions, RunPayloadStore(objects), clock=lambda: NOW + timedelta(seconds=3), lifecycle=test_lifecycle_writer()
    ).commit_state_outcome(authority, stored)
    return stored.digest_sha256


async def test_start_accepts_root_run_and_replays_before_resolution(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    tmp_path,
) -> None:
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    interaction_sessions = lifecycle_interaction_sessions
    interaction_object_store = await LocalObjectStore.create(tmp_path / "objects")
    preparation = _Preparation()
    freezing = _Freezing([_frozen()])
    commands = _commands(interaction_sessions, interaction_object_store, preparation, freezing)

    first = await commands.runs.start(
        actor=_actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="start-one",
        request=_request(),
    )
    repeated = await commands.runs.start(
        actor=_actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="start-one",
        request=_request(),
    )

    assert repeated == first
    assert preparation.calls == 1
    assert freezing.calls == 2
    assert first.thread_id.startswith("thread_")
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
    await commands.runs.start(
        actor=_actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="start-conflict",
        request=_request("first"),
    )

    with pytest.raises(InteractionCommandError) as captured:
        await commands.runs.start(
            actor=_actor(),
            workspace_id=WORKSPACE_ID,
            idempotency_key="start-conflict",
            request=_request("different"),
        )

    assert captured.value.code == "idempotency_conflict"
    assert application_error_status(captured.value) == 409


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
    first = await commands.runs.start(
        actor=_actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="first-root",
        request=_request(),
    )

    with pytest.raises(InteractionCommandError) as captured:
        await commands.runs.start(
            actor=_actor(),
            workspace_id=WORKSPACE_ID,
            idempotency_key="second-root",
            request=_request("second").model_copy(update={"session_id": first.session_id}),
        )

    assert captured.value.code == "session_root_exists"
    assert application_error_status(captured.value) == 409
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
        _Freezing([_frozen(), _frozen(content_digest="b" * 64)]),
    )

    with pytest.raises(InteractionCommandError) as captured:
        await commands.runs.start(
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
    clock_values = (NOW + timedelta(microseconds=index) for index in range(100))
    commands = _commands(
        lifecycle_interaction_sessions,
        interaction_object_store,
        _Preparation(),
        _Freezing([_frozen()]),
        clock=lambda: next(clock_values),
    )
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    accepted = await commands.runs.start(
        actor=_actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="interrupt-source",
        request=_request(),
    )
    request = InterruptRequest(expected_run_version=1, expected_thread_version=1)

    first = await commands.active.interrupt(
        actor=_actor(),
        run_id=accepted.run_id,
        idempotency_key="interrupt-one",
        request=request,
    )
    repeated = await commands.active.interrupt(
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
        evidence = tuple(
            (
                await database.scalars(
                    select(IdempotencyEvidenceRecord).where(IdempotencyEvidenceRecord.operation != "run.accept")
                )
            ).all()
        )
    assert run is not None and run.status == "cancelled" and run.version == 2
    assert first.interrupted_at == run.to_resource().sealed_at
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
    accepted = await commands.runs.start(
        actor=_actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="interrupt-conflict-source",
        request=_request(),
    )
    await commands.active.interrupt(
        actor=_actor(),
        run_id=accepted.run_id,
        idempotency_key="interrupt-conflict",
        request=InterruptRequest(expected_run_version=1, expected_thread_version=1),
    )

    with pytest.raises(InteractionCommandError) as captured:
        await commands.active.interrupt(
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
        queue_name="accepted-queue",
        priority=7,
        execution_max_attempts=4,
        max_handoffs=3,
    )
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    source_receipt = await commands.runs.start(
        actor=_actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="retry-source",
        request=_request("same intent"),
        origin=SubmissionOrigin(native_tool_contexts=({"context_id": "accepted-native-context"},)),
    )
    await commands.active.interrupt(
        actor=_actor(),
        run_id=source_receipt.run_id,
        idempotency_key="retry-source-cancel",
        request=InterruptRequest(expected_run_version=1, expected_thread_version=1),
    )

    async with transaction(lifecycle_interaction_sessions) as database:
        terminal = await database.get(RunRecord, source_receipt.run_id)
        assert terminal is not None
        terminal.labels = {"batch": "source", "source-only": "yes"}
        terminal.attempts_started = 2
        terminal.attempts_charged = 1
        terminal.handoffs_completed = 1
        terminal.started_at = NOW

    # A service restart with new defaults must preserve the accepted execution policy.
    commands = _commands(
        lifecycle_interaction_sessions,
        interaction_object_store,
        _Preparation(),
        _Freezing([_frozen()]),
    )
    request = RetryRunCommand(expected_thread_version=2, labels={"batch": "retry"})
    first = await commands.continuations.retry(
        actor=_actor(),
        run_id=source_receipt.run_id,
        idempotency_key="retry-one",
        request=request,
    )
    repeated = await commands.continuations.retry(
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
    assert retried.labels == {"batch": "retry", "source-only": "yes"}
    assert retried.queue_name == source.queue_name == "accepted-queue"
    assert retried.priority == source.priority == 7
    assert retried.execution_policy_version == source.execution_policy_version == "1"
    assert retried.max_attempts == source.max_attempts == 4
    assert retried.max_handoffs == source.max_handoffs == 3
    assert retried.native_tool_contexts_json == [{"context_id": "accepted-native-context"}]
    assert retried.parent_run_id is None
    assert retried.input_json == source.input_json
    assert retried.authority_principal_id == source.authority_principal_id
    assert thread.current_run_id == retried.id
    assert retried.attempts_started == retried.attempts_charged == retried.handoffs_completed == 0
    assert retried.started_at is None and retried.sealed_at is None and retried.environment_use_started_at is None


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
    source = await commands.runs.start(
        actor=_actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="stale-retry-source",
        request=_request(),
    )
    await commands.active.interrupt(
        actor=_actor(),
        run_id=source.run_id,
        idempotency_key="stale-retry-cancel",
        request=InterruptRequest(expected_run_version=1, expected_thread_version=1),
    )
    await commands.continuations.retry(
        actor=_actor(),
        run_id=source.run_id,
        idempotency_key="stale-retry-first",
        request=RetryRunCommand(expected_thread_version=2),
    )

    with pytest.raises(InteractionCommandError) as captured:
        await commands.continuations.retry(
            actor=_actor(),
            run_id=source.run_id,
            idempotency_key="stale-retry-second",
            request=RetryRunCommand(expected_thread_version=3),
        )

    assert captured.value.code == "run_not_retryable"


@pytest.mark.parametrize("command", ["continue", "fork", "feedback", "waiting_continue"])
async def test_parent_consumers_reject_self_consistent_state_that_differs_from_seal(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession], tmp_path, command: str
) -> None:
    sessions = lifecycle_interaction_sessions
    objects = await LocalObjectStore.create(tmp_path / "objects")
    commands = _commands(sessions, objects, _Preparation(), _Freezing([_frozen()]))
    await seed_hook_actor_access(sessions)
    source = await commands.runs.start(
        actor=_actor(), workspace_id=WORKSPACE_ID, idempotency_key="seal-source", request=_request()
    )
    if command in {"feedback", "waiting_continue"}:
        await _wait_run(sessions, objects, run_id=source.run_id)
    else:
        await _complete_run(sessions, objects, run_id=source.run_id)
    states = RunStateStore(objects)
    original = await states.read(ORGANIZATION_ID, source.run_id)
    payload = json.loads(zstandard.ZstdDecompressor().decompress(original.body))
    payload["checkpoint_seq"] += 1
    body = zstandard.ZstdCompressor(level=1, write_checksum=True).compress(rfc8785.dumps(payload))
    await objects.put(
        original.info.key,
        body,
        content_type=original.info.content_type,
        metadata={
            **original.info.metadata,
            "checkpoint-seq": str(payload["checkpoint_seq"]),
            "digest-sha256": hashlib.sha256(body).hexdigest(),
        },
        if_match=original.info.version,
    )
    assert (await states.read(ORGANIZATION_ID, source.run_id)).body == body
    with pytest.raises(RunObjectIntegrityError, match="sealed state"):
        if command == "continue":
            await commands.runs.continue_from(
                actor=_actor(),
                source_run_id=source.run_id,
                idempotency_key="seal-consumer",
                request=ContinueRunCommand(expected_thread_version=2, input=_request().input),
            )
        elif command == "fork":
            await commands.runs.fork(
                actor=_actor(),
                run_id=source.run_id,
                idempotency_key="seal-consumer",
                request=ForkRunCommand(input=_request().input),
            )
        elif command == "feedback":
            await commands.continuations.feedback(
                actor=_actor(),
                run_id=source.run_id,
                idempotency_key="seal-consumer",
                request=WaitingRunFeedbackRequest(
                    expected_thread_version=2, sealed_state_digest_sha256=original.digest_sha256
                ),
            )
        else:
            await commands.continuations.continue_waiting(
                actor=_actor(),
                run_id=source.run_id,
                idempotency_key="seal-consumer",
                request=WaitingContinueRunCommand(
                    expected_thread_version=2, sealed_state_digest_sha256=original.digest_sha256, input=_request().input
                ),
            )
    async with short_session(sessions) as database:
        assert await database.scalar(select(func.count()).select_from(RunRecord)) == 1
        assert await database.scalar(select(func.count()).select_from(ThreadRecord)) == 1
        thread = await database.get(ThreadRecord, source.thread_id)
        assert thread is not None and thread.current_run_id == source.run_id and thread.version == 2


async def test_fork_creates_child_thread_and_replays(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    tmp_path,
) -> None:
    interaction_object_store = await LocalObjectStore.create(tmp_path / "objects")
    preparation = _Preparation()
    source_config = _frozen()
    source_config = FrozenAgentInvocation(
        agent_id=source_config.agent_id,
        agent_revision_id=source_config.agent_revision_id,
        selector_kind=source_config.selector_kind,
        effective_config=source_config.effective_config.model_copy(update={"instructions": "Run-only instructions"}),
        connection_selections=(),
    )
    config = source_config.effective_config
    config = config.model_copy(
        update={
            "content_digest": hashlib.sha256(
                rfc8785.dumps(config.model_dump(mode="json", by_alias=True, exclude={"content_digest"}))
            ).hexdigest()
        }
    )
    source_config = FrozenAgentInvocation(
        agent_id=source_config.agent_id,
        agent_revision_id=source_config.agent_revision_id,
        selector_kind=source_config.selector_kind,
        effective_config=config,
        connection_selections=(),
    )
    commands = _commands(
        lifecycle_interaction_sessions,
        interaction_object_store,
        preparation,
        _Freezing([source_config]),
    )
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    source = await commands.runs.start(
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
    async with transaction(lifecycle_interaction_sessions) as database:
        source_thread = await database.get(ThreadRecord, source.thread_id)
        source_thread.labels = {"team": "source", "stage": "dev"}
    request = ForkRunCommand(
        input=_request("fork input").input, thread_labels={"stage": "fork"}, labels={"batch": "one"}
    )

    first = await commands.runs.fork(
        actor=_actor(),
        run_id=source.run_id,
        idempotency_key="fork-one",
        request=request,
    )
    repeated = await commands.runs.fork(
        actor=_actor(),
        run_id=source.run_id,
        idempotency_key="fork-one",
        request=request,
    )

    assert preparation.calls == 1
    async with short_session(lifecycle_interaction_sessions) as database:
        source_record = await database.get(RunRecord, source.run_id)
        fork_record = await database.get(RunRecord, first.run_id)
        assert source_record is not None and fork_record is not None
        source_resource, fork_resource = source_record.to_resource(), fork_record.to_resource()
    source_state = await RunStateStore(interaction_object_store).read_run(source_resource)
    fork_state = await RunStateStore(interaction_object_store).read_run(fork_resource)
    assert fork_state.envelope.effective_agent_config == source_state.envelope.effective_agent_config
    assert fork_state.envelope.effective_agent_config.instructions == "Run-only instructions"
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
    assert forked_thread.labels == {"team": "source", "stage": "fork"}
    assert forked_run.labels == {"team": "source", "stage": "fork", "batch": "one"}
    assert forked_thread.origin_kind == "fork"
    assert forked_thread.origin_thread_id == source.thread_id
    assert forked_thread.origin_run_id == source.run_id
    assert (forked_thread.next_delivery_sequence, forked_thread.pending_count, forked_thread.pending_bytes) == (1, 0, 0)


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
    source = await commands.runs.start(
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
    await commands.runs.fork(
        actor=_actor(),
        run_id=source.run_id,
        idempotency_key="fork-conflict",
        request=ForkRunCommand(input=_request("first").input),
    )

    with pytest.raises(InteractionCommandError) as captured:
        await commands.runs.fork(
            actor=_actor(),
            run_id=source.run_id,
            idempotency_key="fork-conflict",
            request=ForkRunCommand(input=_request("different").input),
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
    source = await commands.runs.start(
        actor=_actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="feedback-source",
        request=_request(),
        origin=SubmissionOrigin(native_tool_contexts=({"context_id": "accepted-native-context"},)),
    )
    digest = await _wait_run(
        lifecycle_interaction_sessions,
        interaction_object_store,
        run_id=source.run_id,
    )

    queries = NativeInteractionQueries(lifecycle_interaction_sessions, RunReplayStore(interaction_object_store))
    waiting_resource = await queries.get_run(actor=_actor(), run_id=source.run_id)
    public_digest = waiting_resource.model_dump(mode="json")["sealed_state_digest_sha256"]
    assert public_digest == digest
    collection = await queries.list_runs(
        actor=_actor(), workspace_id=WORKSPACE_ID, thread_id=None, limit=50, cursor=None
    )
    assert collection.items[0].sealed_state_digest_sha256 == public_digest

    omitted = WaitingRunFeedbackRequest(
        expected_thread_version=2,
        sealed_state_digest_sha256=public_digest,
    )
    first = await commands.continuations.feedback(
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
    repeated = await commands.continuations.feedback(
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
    assert successor.native_tool_contexts_json == [{"context_id": "accepted-native-context"}]
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
    source = await commands.runs.start(
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
    await commands.continuations.feedback(
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

    with pytest.raises(InteractionCommandError) as captured:
        await commands.continuations.feedback(
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
    source = await commands.runs.start(
        actor=_actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="waiting-continue-source",
        request=_request(),
        origin=SubmissionOrigin(native_tool_contexts=({"context_id": "accepted-native-context"},)),
    )
    digest = await _wait_run(
        lifecycle_interaction_sessions,
        interaction_object_store,
        run_id=source.run_id,
    )
    request = WaitingContinueRunCommand(
        expected_thread_version=2,
        sealed_state_digest_sha256=digest,
        input=_request("handle this instead").input,
    )

    first = await commands.continuations.continue_waiting(
        actor=_actor(),
        run_id=source.run_id,
        idempotency_key="waiting-continue-one",
        request=request,
    )
    repeated = await commands.continuations.continue_waiting(
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
    assert successor.native_tool_contexts_json == [{"context_id": "accepted-native-context"}]
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
    accepted = await commands.runs.start(
        actor=_actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="steer-source",
        request=_request(),
    )

    first = await commands.active.steer(
        actor=_actor(),
        run_id=accepted.run_id,
        idempotency_key="steer-one",
        input=_request("follow up").input,
    )
    repeated = await commands.active.steer(
        actor=_actor(),
        run_id=accepted.run_id,
        idempotency_key="steer-one",
        input=_request("follow up").input,
    )
    status = await commands.active.get_steer(
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
        evidence = tuple(
            (
                await database.scalars(
                    select(IdempotencyEvidenceRecord).where(IdempotencyEvidenceRecord.operation != "run.accept")
                )
            ).all()
        )
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
    accepted = await commands.runs.start(
        actor=_actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="steer-conflict-source",
        request=_request(),
    )
    await commands.active.steer(
        actor=_actor(),
        run_id=accepted.run_id,
        idempotency_key="steer-conflict",
        input=_request("first").input,
    )

    with pytest.raises(InteractionCommandError) as captured:
        await commands.active.steer(
            actor=_actor(),
            run_id=accepted.run_id,
            idempotency_key="steer-conflict",
            input=_request("different").input,
        )

    assert captured.value.code == "idempotency_conflict"
