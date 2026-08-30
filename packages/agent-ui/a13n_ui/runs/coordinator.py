"""Foreground Turn acceptance, Harness execution, continuation, and commit coordination."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import cast
from uuid import uuid4

from a13n_harness import (
    AgentIdentityRef,
    DeferredToolResume,
    ExecutableAgent,
    HarnessRunResult,
    HarnessRunResultEvent,
    HarnessRunStream,
    HarnessState,
    RunBindings,
    RunInputValue,
    RunModelResolver,
    SkillSelectionRunCapability,
)
from a13n_stream_protocol import HarnessAguiObserver
from anyio import CancelScope, Lock
from pydantic import JsonValue, TypeAdapter
from pydantic_ai.tools import DeferredToolRequests, DeferredToolResults

from a13n_ui.composition import CompositionService, ResolvedAgentSnapshot, ResolvedEnvironmentSnapshot
from a13n_ui.configuration.models import canonical_json_value
from a13n_ui.environments import EnvironmentService
from a13n_ui.errors import AgentUiError, EventStoreError, RunCoordinationError, SessionError
from a13n_ui.model_adapters import RunModelResolverFactory
from a13n_ui.sessions.events import SessionEventStore
from a13n_ui.sessions.models import (
    LocalSession,
    TurnState,
    TurnView,
    WaitingReason,
)
from a13n_ui.sessions.service import SessionService
from a13n_ui.storage.objects import ObjectKind, ObjectRef


@dataclass(frozen=True, slots=True)
class _PreparedSession:
    session: LocalSession
    agent: ResolvedAgentSnapshot
    environment: ResolvedEnvironmentSnapshot
    previous_state: HarnessState
    executable: ExecutableAgent[object]
    model_resolver: RunModelResolver


class ForegroundRunCoordinator:
    """Advance one durable root Thread through exactly one canonical Harness stream."""

    def __init__(
        self,
        *,
        sessions: SessionService,
        composition: CompositionService,
        environments: EnvironmentService,
        events: SessionEventStore,
        model_resolver_factory: RunModelResolverFactory,
    ) -> None:
        self._sessions = sessions
        self._composition = composition
        self._environments = environments
        self._events = events
        self._model_resolver_factory = model_resolver_factory
        self._active_lock = Lock()
        self._active: dict[str, HarnessRunStream[object]] = {}

    async def run_turn(
        self,
        *,
        session_id: str,
        thread_id: str,
        expected_thread_version: int,
        input_value: RunInputValue,
    ) -> TurnView:
        """Accept and synchronously advance one new root Turn to a durable boundary."""

        prepared = await self._prepare(
            session_id=session_id,
            thread_id=thread_id,
            expected_thread_version=expected_thread_version,
        )
        turn_id = f"turn-{uuid4().hex}"
        accepted = await self._sessions.repository.accept_turn(
            session_id=session_id,
            thread_id=thread_id,
            expected_version=expected_thread_version,
            turn_id=turn_id,
            input_value=TypeAdapter(RunInputValue).dump_python(input_value, mode="json"),
        )
        try:
            return await self._run_accepted(
                prepared=prepared,
                turn=accepted,
                expected_thread_version=expected_thread_version + 1,
                input_value=input_value,
                deferred_resume=None,
            )
        except BaseException as exc:
            await self._close_unstarted_after_failure(
                turn_id=turn_id,
                expected_thread_version=expected_thread_version + 1,
                exc=exc,
            )
            raise

    async def resume_turn(
        self,
        *,
        session_id: str,
        turn_id: str,
        expected_thread_version: int,
        results: DeferredToolResults,
    ) -> TurnView:
        """Consume one exact pending deferred request and advance the same Turn."""

        session = await self._sessions.get(session_id)
        if session.root.commit_version != expected_thread_version:
            raise SessionError(
                "The Thread commit version is stale.",
                code="thread_version_conflict",
                details={"current_version": session.root.commit_version},
            )
        turn = _turn(session, turn_id)
        if (
            turn.state is not TurnState.waiting
            or turn.pending_deferred is None
            or turn.selected_checkpoint is None
            or turn.thread_id != session.root.thread_id
        ):
            raise RunCoordinationError(
                "The selected Turn has no resumable deferred request.",
                code="turn_not_resumable",
            )
        prepared = await self._prepare(
            session_id=session_id,
            thread_id=turn.thread_id,
            expected_thread_version=expected_thread_version,
            checkpoint=turn.selected_checkpoint,
        )
        requests = await self._sessions.load_deferred_requests(
            session_id=session_id,
            thread_id=turn.thread_id,
            turn_id=turn_id,
            agent_snapshot_digest=session.agent_snapshot.logical_digest,
            reference=turn.pending_deferred,
        )
        resume = DeferredToolResume(requests=requests, results=results)
        return await self._run_accepted(
            prepared=prepared,
            turn=turn,
            expected_thread_version=expected_thread_version,
            input_value=None,
            deferred_resume=resume,
        )

    async def deferred_requests(
        self,
        *,
        session_id: str,
        turn_id: str,
    ) -> DeferredToolRequests:
        """Load the exact native pending request value selected by a waiting Turn."""

        session = await self._sessions.get(session_id)
        turn = _turn(session, turn_id)
        if turn.state is not TurnState.waiting or turn.pending_deferred is None:
            raise RunCoordinationError(
                "The selected Turn has no pending deferred request.",
                code="deferred_request_missing",
            )
        return await self._sessions.load_deferred_requests(
            session_id=session_id,
            thread_id=turn.thread_id,
            turn_id=turn.turn_id,
            agent_snapshot_digest=session.agent_snapshot.logical_digest,
            reference=turn.pending_deferred,
        )

    async def cancel_turn(
        self,
        *,
        session_id: str,
        turn_id: str,
        expected_thread_version: int,
    ) -> TurnView:
        """Request active cancellation or close accepted/waiting work immediately."""

        session = await self._sessions.get(session_id)
        if session.root.commit_version != expected_thread_version:
            raise SessionError(
                "The Thread commit version is stale.",
                code="thread_version_conflict",
                details={"current_version": session.root.commit_version},
            )
        turn = _turn(session, turn_id)
        async with self._active_lock:
            stream = self._active.get(turn_id)
            if stream is not None:
                stream.cancel()
                return turn
        if turn.state not in {TurnState.accepted, TurnState.waiting}:
            raise RunCoordinationError("The selected Turn is not cancellable.", code="turn_not_cancellable")
        return await self._sessions.repository.commit_unstarted_terminal(
            turn_id=turn_id,
            expected_thread_version=expected_thread_version,
            state=TurnState.cancelled,
            failure={"code": "run_cancelled", "message": "The Turn was cancelled before another Run."},
        )

    async def cancel_all(self) -> None:
        """Request cooperative cancellation for every process-local foreground Run."""

        async with self._active_lock:
            streams = tuple(self._active.values())
        for stream in streams:
            stream.cancel()

    async def close(self) -> None:
        await self.cancel_all()
        await self._events.close()

    async def _prepare(
        self,
        *,
        session_id: str,
        thread_id: str,
        expected_thread_version: int,
        checkpoint: object | None = None,
    ) -> _PreparedSession:
        session = await self._sessions.get(session_id)
        if session.root.thread_id != thread_id:
            raise SessionError("The selected Thread does not exist in this Session.", code="thread_missing")
        if session.root.commit_version != expected_thread_version:
            raise SessionError(
                "The Thread commit version is stale.",
                code="thread_version_conflict",
                details={"current_version": session.root.commit_version},
            )
        selected = checkpoint or session.root.selected_checkpoint
        if selected is None:
            raise RunCoordinationError("The selected Thread has no complete checkpoint.", code="checkpoint_missing")
        from a13n_ui.sessions import CheckpointRef

        if not isinstance(selected, CheckpointRef):
            raise TypeError("checkpoint must be a CheckpointRef")
        agent = await self._composition.agent(session.agent_snapshot)
        environment = await self._composition.environment(session.environment_snapshot)
        executable = await self._composition.executable(
            session.agent_snapshot,
            session.environment_snapshot,
        )
        previous_state = await self._sessions.load_state(session_id, selected)
        model_resolver = await self._model_resolver_factory(agent)
        if not callable(model_resolver):
            raise RunCoordinationError(
                "The Host Model resolver factory returned an invalid resolver.",
                code="model_resolver_invalid",
            )
        return _PreparedSession(
            session=session,
            agent=agent,
            environment=environment,
            previous_state=previous_state,
            executable=executable,
            model_resolver=model_resolver,
        )

    async def _run_accepted(
        self,
        *,
        prepared: _PreparedSession,
        turn: TurnView,
        expected_thread_version: int,
        input_value: RunInputValue | None,
        deferred_resume: DeferredToolResume | None,
    ) -> TurnView:
        executable = prepared.executable
        model_resolver = prepared.model_resolver
        capabilities = _run_capabilities(prepared.session, prepared.agent)
        stream: HarnessRunStream[object] | None = None
        started_version: int | None = None
        terminal_observed = False
        durable_committed = False
        presentation_failure: EventStoreError | None = None
        external_cancellation: asyncio.CancelledError | None = None
        selected: TurnView | None = None
        try:
            async with self._environments.run_environment(
                session_id=prepared.session.session_id,
                snapshot=prepared.environment,
            ) as environment:
                bindings = RunBindings.embedded(
                    identity=AgentIdentityRef(
                        issuer="a13n.agent-ui",
                        subject=prepared.session.session_id,
                    ),
                    environment=environment,
                    model_resolver=model_resolver,
                    capabilities=capabilities,
                    metadata={
                        "session_id": prepared.session.session_id,
                        "turn_id": turn.turn_id,
                        "agent_snapshot": prepared.session.agent_snapshot.logical_digest,
                        "environment_snapshot": prepared.session.environment_snapshot.logical_digest,
                    },
                )
                stream = executable.stream(
                    input_value,
                    bindings=bindings,
                    previous_state=prepared.previous_state,
                    deferred_resume=deferred_resume,
                )
                started_version = await self._sessions.repository.start_run(
                    turn_id=turn.turn_id,
                    expected_thread_version=expected_thread_version,
                    run_id=stream.run_id,
                    consume_deferred=deferred_resume is not None,
                )
                await self._register(turn.turn_id, stream)
                try:
                    terminal, presentation_failure, external_cancellation = await self._consume(
                        prepared.session.session_id,
                        turn,
                        stream,
                    )
                    terminal_observed = True
                finally:
                    await self._unregister(turn.turn_id, stream)
                with CancelScope(shield=True):
                    selected = await self._commit_result(
                        prepared=prepared,
                        turn=turn,
                        expected_thread_version=started_version,
                        terminal=terminal,
                    )
                if selected is None:
                    raise RunCoordinationError(
                        "The terminal Run result was not durably selected.",
                        code="run_commit_missing",
                    )
                durable_committed = True
            try:
                await self._environments.apply_idle_policy(
                    prepared.session.session_id,
                    prepared.environment,
                )
            except AgentUiError:
                pass
            if external_cancellation is not None:
                raise external_cancellation
            if presentation_failure is not None:
                raise presentation_failure
            return selected
        except asyncio.CancelledError as exc:
            if stream is not None:
                stream.cancel()
            if started_version is not None and not durable_committed and stream is not None:
                await self._commit_running_failure(
                    turn_id=turn.turn_id,
                    expected_thread_version=started_version,
                    run_id=stream.run_id,
                    exc=exc,
                    terminal_observed=terminal_observed,
                )
            raise
        except BaseException as exc:
            if started_version is not None and not durable_committed and stream is not None:
                await self._commit_running_failure(
                    turn_id=turn.turn_id,
                    expected_thread_version=started_version,
                    run_id=stream.run_id,
                    exc=exc,
                    terminal_observed=terminal_observed,
                )
            raise

    async def _consume(
        self,
        session_id: str,
        turn: TurnView,
        stream: HarnessRunStream[object],
    ) -> tuple[
        HarnessRunResult[object],
        EventStoreError | None,
        asyncio.CancelledError | None,
    ]:
        observer = HarnessAguiObserver()
        first_presentation_failure: EventStoreError | None = None
        terminal: HarnessRunResult[object] | None = None
        external_cancellation: asyncio.CancelledError | None = None
        async with stream:
            iterator = stream.__aiter__()

            async def consume_next() -> bool:
                nonlocal first_presentation_failure, terminal
                try:
                    item = await iterator.__anext__()
                except StopAsyncIteration:
                    return False
                events = observer.observe(item)
                if first_presentation_failure is None:
                    try:
                        await self._events.append(
                            session_id=session_id,
                            thread_id=turn.thread_id,
                            turn_id=turn.turn_id,
                            run_id=item.run_id,
                            events=events,
                        )
                    except EventStoreError as exc:
                        first_presentation_failure = exc
                if isinstance(item, HarnessRunResultEvent):
                    terminal = item.result
                return True

            try:
                while await consume_next():
                    pass
            except asyncio.CancelledError as exc:
                external_cancellation = exc
                stream.cancel()
                with CancelScope(shield=True):
                    while await consume_next():
                        pass
        if terminal is None:
            raise RunCoordinationError("The Harness stream ended without a terminal result.", code="run_result_missing")
        return terminal, first_presentation_failure, external_cancellation

    async def _commit_result(
        self,
        *,
        prepared: _PreparedSession,
        turn: TurnView,
        expected_thread_version: int,
        terminal: HarnessRunResult[object],
    ) -> TurnView:
        checkpoint = None
        if terminal.state is not None:
            checkpoint = await self._sessions.publish_turn_state(
                session_id=prepared.session.session_id,
                turn_id=turn.turn_id,
                state=terminal.state,
            )
        if terminal.status == "suspended":
            assert checkpoint is not None
            requests = terminal.deferred
            assert requests is not None
            deferred = await self._sessions.publish_deferred_requests(
                session_id=prepared.session.session_id,
                thread_id=turn.thread_id,
                turn_id=turn.turn_id,
                source_run_id=terminal.run_id,
                agent_snapshot_digest=prepared.session.agent_snapshot.logical_digest,
                requests=requests,
            )
            return await self._sessions.repository.commit_waiting(
                turn_id=turn.turn_id,
                expected_thread_version=expected_thread_version,
                run_id=terminal.run_id,
                checkpoint=checkpoint,
                state_object=ObjectRef(
                    object_kind=ObjectKind.harness_state,
                    object_schema_version="1",
                    logical_digest=checkpoint.state_object_digest,
                ),
                deferred=deferred,
                waiting_reason=WaitingReason.deferred_tool,
            )
        if terminal.status == "completed":
            assert checkpoint is not None
            return await self._sessions.repository.commit_terminal(
                turn_id=turn.turn_id,
                expected_thread_version=expected_thread_version,
                run_id=terminal.run_id,
                state=TurnState.completed,
                checkpoint=checkpoint,
                terminal_projection=canonical_json_value(terminal.output),
                failure=None,
            )
        if terminal.status == "failed":
            failure = terminal.failure
            assert failure is not None
            return await self._sessions.repository.commit_terminal(
                turn_id=turn.turn_id,
                expected_thread_version=expected_thread_version,
                run_id=terminal.run_id,
                state=TurnState.failed,
                checkpoint=checkpoint,
                terminal_projection=None,
                failure=cast(JsonValue, failure.model_dump(mode="json", by_alias=True)),
            )
        return await self._sessions.repository.commit_terminal(
            turn_id=turn.turn_id,
            expected_thread_version=expected_thread_version,
            run_id=terminal.run_id,
            state=TurnState.cancelled,
            checkpoint=None,
            terminal_projection=None,
            failure={"code": "run_cancelled", "message": "The Harness Run was cancelled."},
        )

    async def _commit_running_failure(
        self,
        *,
        turn_id: str,
        expected_thread_version: int,
        run_id: str,
        exc: BaseException,
        terminal_observed: bool,
    ) -> None:
        with CancelScope(shield=True):
            try:
                if terminal_observed:
                    state = TurnState.interrupted
                elif isinstance(exc, asyncio.CancelledError):
                    state = TurnState.cancelled
                else:
                    state = TurnState.failed
                await self._sessions.repository.commit_terminal(
                    turn_id=turn_id,
                    expected_thread_version=expected_thread_version,
                    run_id=run_id,
                    state=state,
                    checkpoint=None,
                    terminal_projection=None,
                    failure=_safe_failure(exc, "run_execution_failed"),
                )
            except SessionError:
                pass

    async def _close_unstarted_after_failure(
        self,
        *,
        turn_id: str,
        expected_thread_version: int,
        exc: BaseException,
    ) -> None:
        with CancelScope(shield=True):
            try:
                await self._sessions.repository.commit_unstarted_terminal(
                    turn_id=turn_id,
                    expected_thread_version=expected_thread_version,
                    state=(TurnState.cancelled if isinstance(exc, asyncio.CancelledError) else TurnState.interrupted),
                    failure=_safe_failure(exc, "run_not_started"),
                )
            except SessionError:
                pass

    async def _register(self, turn_id: str, stream: HarnessRunStream[object]) -> None:
        async with self._active_lock:
            if turn_id in self._active:
                raise RunCoordinationError("The Turn already has an active Run.", code="turn_run_active")
            self._active[turn_id] = stream

    async def _unregister(self, turn_id: str, stream: HarnessRunStream[object]) -> None:
        async with self._active_lock:
            if self._active.get(turn_id) is stream:
                self._active.pop(turn_id, None)


def _turn(session: LocalSession, turn_id: str) -> TurnView:
    for turn in session.root.turns:
        if turn.turn_id == turn_id:
            return turn
    raise SessionError("The selected Turn does not exist.", code="turn_missing")


def _run_capabilities(
    session: LocalSession, snapshot: ResolvedAgentSnapshot
) -> tuple[SkillSelectionRunCapability, ...]:
    root = next(node for node in snapshot.resolved_agents if node.agent_revision == snapshot.root_agent)
    selection = next(
        (item for item in session.skill_selections if item.agent_node_id == root.agent_id),
        None,
    )
    if not root.skills:
        return ()
    if selection is not None and selection.mode == "exact":
        names = frozenset(selection.names)
    elif root.default_skill_names is not None:
        names = frozenset(root.default_skill_names)
    else:
        return ()
    return (SkillSelectionRunCapability(names=names),)


def _safe_failure(exc: BaseException, fallback_code: str) -> JsonValue:
    if isinstance(exc, AgentUiError):
        return {
            "code": exc.code,
            "message": str(exc),
            "details": canonical_json_value(exc.details),
        }
    return {
        "code": fallback_code,
        "message": "The foreground Run did not reach its selected durable boundary.",
        "error_type": type(exc).__name__,
    }


__all__ = ["ForegroundRunCoordinator"]
