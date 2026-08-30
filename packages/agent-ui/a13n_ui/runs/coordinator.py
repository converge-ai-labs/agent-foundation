"""Process-local foreground Harness execution with best-effort continuation saves."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

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
)
from a13n_harness.capabilities import SkillSelectionRunCapability
from a13n_stream_protocol import HarnessAguiObserver
from anyio import Lock
from pydantic import JsonValue
from pydantic_ai.tools import DeferredToolResults

from a13n_ui.composition import CompositionService, ResolvedAgentSnapshot, ResolvedEnvironmentSnapshot
from a13n_ui.configuration.models import canonical_json_value
from a13n_ui.environments import EnvironmentService
from a13n_ui.errors import LivePresentationError, RunCoordinationError
from a13n_ui.model_adapters import RunModelResolverFactory
from a13n_ui.sessions.events import SessionEventHub
from a13n_ui.sessions.models import LocalSession, SessionRunResult, SessionRunStatus
from a13n_ui.sessions.service import SessionService


@dataclass(frozen=True, slots=True)
class _PreparedSession:
    session: LocalSession
    agent: ResolvedAgentSnapshot
    environment: ResolvedEnvironmentSnapshot
    previous_state: HarnessState
    executable: ExecutableAgent[object]
    model_resolver: RunModelResolver


class ForegroundRunCoordinator:
    """Run one process-local Session advancement at a time per Host."""

    def __init__(
        self,
        *,
        sessions: SessionService,
        composition: CompositionService,
        environments: EnvironmentService,
        events: SessionEventHub,
        model_resolver_factory: RunModelResolverFactory,
    ) -> None:
        self._sessions = sessions
        self._composition = composition
        self._environments = environments
        self._events = events
        self._model_resolver_factory = model_resolver_factory
        self._registry_lock = Lock()
        self._session_locks: dict[str, Lock] = {}
        self._active: dict[str, HarnessRunStream[object]] = {}

    async def run(
        self,
        *,
        session_id: str,
        input_value: RunInputValue,
    ) -> SessionRunResult:
        lock = await self._session_lock(session_id)
        async with lock:
            prepared, deferred = await self._prepare(session_id)
            if deferred is not None:
                raise RunCoordinationError(
                    "The selected Session continuation is suspended and requires deferred results.",
                    code="session_suspended",
                )
            return await self._execute(
                prepared=prepared,
                input_value=input_value,
                deferred_resume=None,
            )

    async def resume(
        self,
        *,
        session_id: str,
        results: DeferredToolResults,
    ) -> SessionRunResult:
        lock = await self._session_lock(session_id)
        async with lock:
            prepared, deferred = await self._prepare(session_id)
            if deferred is None:
                raise RunCoordinationError(
                    "The selected Session continuation has no deferred requests.",
                    code="session_not_suspended",
                )
            return await self._execute(
                prepared=prepared,
                input_value=None,
                deferred_resume=DeferredToolResume(requests=deferred, results=results),
            )

    async def deferred_requests(self, session_id: str):
        """Return the deferred requests stored in the selected continuation."""

        return await self._sessions.selected_deferred_requests(session_id)

    async def cancel(self, session_id: str) -> bool:
        """Request cancellation of the current Host's active Session Run."""

        async with self._registry_lock:
            stream = self._active.get(session_id)
            if stream is None:
                return False
            stream.cancel()
            return True

    async def cancel_all(self) -> None:
        async with self._registry_lock:
            streams = tuple(self._active.values())
        for stream in streams:
            stream.cancel()

    async def close(self) -> None:
        await self.cancel_all()
        await self._events.close()

    async def _prepare(self, session_id: str):
        session = await self._sessions.get(session_id)
        previous_state, deferred = await self._sessions.load_continuation(session.continuation)
        agent = await self._composition.agent(session.agent_snapshot)
        environment = await self._composition.environment(session.environment_snapshot)
        executable = await self._composition.executable(
            session.agent_snapshot,
            session.environment_snapshot,
        )
        model_resolver = await self._model_resolver_factory(agent)
        if not callable(model_resolver):
            raise RunCoordinationError(
                "The Host Model resolver factory returned an invalid resolver.",
                code="model_resolver_invalid",
            )
        return (
            _PreparedSession(
                session=session,
                agent=agent,
                environment=environment,
                previous_state=previous_state,
                executable=executable,
                model_resolver=model_resolver,
            ),
            deferred,
        )

    async def _execute(
        self,
        *,
        prepared: _PreparedSession,
        input_value: RunInputValue | None,
        deferred_resume: DeferredToolResume | None,
    ) -> SessionRunResult:
        session_id = prepared.session.session_id
        result: SessionRunResult | None = None
        try:
            async with self._environments.run_environment(
                session_id=session_id,
                snapshot=prepared.environment,
            ) as environment:
                bindings = RunBindings.embedded(
                    identity=AgentIdentityRef(
                        issuer="a13n.agent-ui",
                        subject=session_id,
                    ),
                    environment=environment,
                    model_resolver=prepared.model_resolver,
                    capabilities=_run_capabilities(prepared.session, prepared.agent),
                    metadata={
                        "session_id": session_id,
                        "agent_snapshot": prepared.session.agent_snapshot.logical_digest,
                        "environment_snapshot": prepared.session.environment_snapshot.logical_digest,
                    },
                )
                stream = prepared.executable.stream(
                    input_value,
                    bindings=bindings,
                    previous_state=prepared.previous_state,
                    deferred_resume=deferred_resume,
                )
                await self._register(session_id, stream)
                try:
                    result = await self._consume(session_id, stream)
                finally:
                    await self._unregister(session_id, stream)
        except Exception as exc:
            if result is not None:
                raise _cleanup_error(result) from exc
            raise
        if result is None:
            raise RunCoordinationError(
                "The Harness Run ended without a result.",
                code="run_result_missing",
            )
        try:
            await self._environments.apply_idle_policy(session_id, prepared.environment)
        except Exception as exc:
            raise _cleanup_error(result) from exc
        return result

    async def _consume(
        self,
        session_id: str,
        stream: HarnessRunStream[object],
    ) -> SessionRunResult:
        observer = HarnessAguiObserver()
        terminal: SessionRunResult | None = None
        async with stream:
            async for item in stream:
                try:
                    await self._events.append(
                        session_id=session_id,
                        run_id=item.run_id,
                        events=observer.observe(item),
                    )
                except LivePresentationError:
                    pass
                if isinstance(item, HarnessRunResultEvent):
                    terminal = await self._save_result(session_id, item.result)
        if terminal is None:
            raise RunCoordinationError(
                "The Harness stream ended without a terminal result.",
                code="run_result_missing",
            )
        return terminal

    async def _save_result(
        self,
        session_id: str,
        terminal: HarnessRunResult[object],
    ) -> SessionRunResult:
        continuation = None
        if terminal.state is not None:
            continuation = await self._sessions.publish_continuation(
                terminal.state,
                terminal.deferred if terminal.status == "suspended" else None,
            )
            await self._sessions.select_continuation(session_id, continuation)
        if terminal.status == "completed":
            return SessionRunResult(
                run_id=terminal.run_id,
                status=SessionRunStatus.completed,
                output=canonical_json_value(terminal.output),
                continuation=continuation,
            )
        if terminal.status == "suspended":
            if continuation is None:
                raise RunCoordinationError(
                    "A suspended Harness result has no complete continuation.",
                    code="continuation_missing",
                )
            return SessionRunResult(
                run_id=terminal.run_id,
                status=SessionRunStatus.suspended,
                continuation=continuation,
            )
        if terminal.status == "failed":
            failure = terminal.failure
            return SessionRunResult(
                run_id=terminal.run_id,
                status=SessionRunStatus.failed,
                failure=(
                    cast(JsonValue, failure.model_dump(mode="json", by_alias=True))
                    if failure is not None
                    else {"code": "run_failed"}
                ),
                continuation=continuation,
            )
        return SessionRunResult(
            run_id=terminal.run_id,
            status=SessionRunStatus.cancelled,
            failure={"code": "run_cancelled", "message": "The Harness Run was cancelled."},
            continuation=continuation,
        )

    async def _session_lock(self, session_id: str) -> Lock:
        async with self._registry_lock:
            return self._session_locks.setdefault(session_id, Lock())

    async def _register(self, session_id: str, stream: HarnessRunStream[object]) -> None:
        async with self._registry_lock:
            if session_id in self._active:
                raise RunCoordinationError(
                    "The Session already has an active Run in this Host.",
                    code="session_run_active",
                )
            self._active[session_id] = stream

    async def _unregister(self, session_id: str, stream: HarnessRunStream[object]) -> None:
        async with self._registry_lock:
            if self._active.get(session_id) is stream:
                self._active.pop(session_id, None)


def _cleanup_error(result: SessionRunResult) -> RunCoordinationError:
    continuation = result.continuation
    selected = continuation is not None
    return RunCoordinationError(
        (
            "The Harness result was saved, but Environment cleanup failed."
            if selected
            else "The Harness Run finished, but Environment cleanup failed."
        ),
        code="run_saved_cleanup_failed" if selected else "run_cleanup_failed",
        details={
            "run_id": result.run_id,
            "status": result.status.value,
            "continuation_object_digest": continuation.object_digest if continuation is not None else None,
        },
    )


def _run_capabilities(
    session: LocalSession,
    snapshot: ResolvedAgentSnapshot,
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


__all__ = ["ForegroundRunCoordinator"]
