"""Root Session creation, continuation selection, and foreground Run coordination."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from a13n_harness import (
    AgentIdentityRef,
    AgentInstanceContext,
    HarnessRunResult,
    HarnessRunResultEvent,
    HarnessRunStream,
    HarnessState,
    RunBindings,
)
from a13n_harness import __version__ as harness_version
from a13n_harness.capabilities import SubagentOperator
from a13n_stream_protocol import HarnessAguiObserver
from anyio import CancelScope, Lock
from pydantic import JsonValue
from pydantic_ai.messages import ModelMessagesTypeAdapter

from a13n_ui.capability_runtime import production_run_capabilities
from a13n_ui.composition import AgentReconstructor, ReconstructedAgent
from a13n_ui.composition.models import ResolvedAgentSnapshot, ResolvedEnvironmentProfile
from a13n_ui.environment_runtime import (
    EnvironmentFinalization,
    EnvironmentRunService,
    EnvironmentSnapshotReconstructor,
    normalize_workspace_binding,
)
from a13n_ui.errors import RunCoordinationError, SessionError
from a13n_ui.live import AgentUiLiveHub
from a13n_ui.session_capability import AgentUiSessionCapability
from a13n_ui.storage import (
    LocalStore,
    ObjectKind,
    ObjectRef,
    Session,
    SnapshotRef,
    StoredSessionContinuation,
)
from a13n_ui.subagent_operator import AgentUiSubagentOperator


@dataclass(frozen=True, slots=True)
class RootContinuationSelection:
    """Root continuation publication outcome independent from Environment finalization."""

    status: Literal["selected", "not_available", "failed"]
    reference: ObjectRef | None = None
    error: Exception | None = None


@dataclass(frozen=True, slots=True)
class RootRunOutcome:
    """One terminal Harness outcome and its independent durable publication facts."""

    result: HarnessRunResult[str]
    environment: EnvironmentFinalization
    continuation: RootContinuationSelection


@dataclass(frozen=True, slots=True)
class RootSteerResult:
    """Process-local acknowledgement for one root steering request."""

    session_id: str
    accepted: bool
    enqueue_id: str | None = None


@dataclass(frozen=True, slots=True)
class RootCancelResult:
    """Process-local acknowledgement for one root cancellation request."""

    session_id: str
    accepted: bool


class SessionService:
    """Application service for pinned root Threads and one-live-Run admission."""

    def __init__(
        self,
        *,
        store: LocalStore,
        agent_reconstructor: AgentReconstructor,
        environment_reconstructor: EnvironmentSnapshotReconstructor,
        subagent_operator: SubagentOperator | None = None,
        live_hub: AgentUiLiveHub | None = None,
        cleanup_timeout_seconds: float = 30.0,
    ) -> None:
        self._store = store
        self._agents = agent_reconstructor
        self._environment_reconstructor = environment_reconstructor
        self._environments = EnvironmentRunService(store, environment_reconstructor)
        self._subagent_operator = subagent_operator
        self._live_hub = live_hub
        self._cleanup_timeout_seconds = cleanup_timeout_seconds
        self._admission_lock = Lock()
        self._active_sessions: set[str] = set()
        self._active_roots: dict[str, HarnessRunStream[Any]] = {}

    async def create(
        self,
        *,
        agent_name: str,
        environment_name: str,
        source_digest: str | None = None,
        title: str | None = None,
    ) -> Session:
        """Pin selected accepted snapshots and publish a baseline root continuation."""

        selected_digest = source_digest or await self._store.configurations.current_digest()
        if selected_digest is None:
            raise SessionError(
                "No accepted Agent UI configuration is selected.",
                code="configuration_not_accepted",
            )
        snapshots = await self._store.configurations.snapshots(selected_digest)
        agent_ref = snapshots.get(("agent", agent_name))
        environment_ref = snapshots.get(("environment", environment_name))
        if agent_ref is None:
            raise SessionError(
                "The selected accepted configuration has no such Agent snapshot.",
                code="agent_snapshot_missing",
                details={"agent_name": agent_name},
            )
        if environment_ref is None:
            raise SessionError(
                "The selected accepted configuration has no such Environment snapshot.",
                code="environment_snapshot_missing",
                details={"environment_name": environment_name},
            )

        profile = await self._store.objects.read_model(environment_ref, ResolvedEnvironmentProfile)
        self._environment_reconstructor.reconstruct(profile)

        baseline = HarnessState.new()
        continuation = await self._store.objects.publish_model(
            object_kind=ObjectKind.session_continuation,
            value=StoredSessionContinuation(
                harness_release=harness_version,
                harness_state=baseline,
                created_at=datetime.now(UTC),
            ),
        )
        return await self._store.sessions.create(
            session_id=f"session-{uuid4().hex[:20]}",
            root_thread_id=baseline.thread_id,
            agent_snapshot=SnapshotRef(snapshot_kind="agent", object=agent_ref),
            environment_snapshot=SnapshotRef(snapshot_kind="environment", object=environment_ref),
            continuation=continuation.ref,
            title=title,
        )

    async def get(self, session_id: str) -> Session:
        session = await self._store.sessions.get(session_id)
        if session is None:
            raise SessionError("Session does not exist.", code="session_missing")
        return session

    async def list(
        self,
        *,
        query: str | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[tuple[Session, ...], int]:
        """List bounded safe Session metadata for App surfaces and root tools."""

        if query is not None and len(query) > 512:
            raise SessionError("Session query is too large.", code="session_query_invalid")
        return await self._store.sessions.list(query=query, offset=offset, limit=limit)

    async def inspect(
        self,
        *,
        session_id: str,
        history_offset: int = 0,
        history_limit: int = 50,
    ) -> tuple[Session, list[JsonValue], int]:
        """Read detached Session metadata and a bounded selected root history page."""

        if history_offset < 0 or not 1 <= history_limit <= 100:
            raise SessionError("Session history page is invalid.", code="session_history_page_invalid")
        session = await self.get(session_id)
        stored = await self._load_continuation(session)
        history = stored.harness_state.message_history
        page = history[history_offset : history_offset + history_limit]
        projected = ModelMessagesTypeAdapter.dump_python(list(page), mode="json")
        if not isinstance(projected, list):  # pragma: no cover - adapter contract
            raise RuntimeError("Model history projection is not a list")
        return session, projected, len(history)

    async def steer(self, *, session_id: str, message: str) -> RootSteerResult:
        """Steer only a root Run currently owned by this process."""

        if not message.strip():
            raise RunCoordinationError("A steering message must not be blank.", code="run_input_invalid")
        async with self._admission_lock:
            stream = self._active_roots.get(session_id)
        if stream is None:
            return RootSteerResult(session_id=session_id, accepted=False)
        try:
            enqueue_id = await stream.steer(message)
        except Exception:
            return RootSteerResult(session_id=session_id, accepted=False)
        return RootSteerResult(session_id=session_id, accepted=True, enqueue_id=enqueue_id)

    async def cancel(self, *, session_id: str) -> RootCancelResult:
        """Request cancellation only for a root Run currently owned by this process."""

        async with self._admission_lock:
            stream = self._active_roots.get(session_id)
        if stream is None:
            return RootCancelResult(session_id=session_id, accepted=False)
        stream.cancel()
        return RootCancelResult(session_id=session_id, accepted=True)

    async def run(
        self,
        *,
        session_id: str,
        prompt: str,
        folders: tuple[Path | str, ...],
    ) -> RootRunOutcome:
        """Admit and execute one root message without holding a storage transaction."""

        if not prompt.strip():
            raise RunCoordinationError("A root message must not be blank.", code="run_input_invalid")
        binding = await normalize_workspace_binding(folders)
        await self._claim(session_id)
        try:
            session = await self.get(session_id)
            stored = await self._load_continuation(session)
            if stored.deferred_requests is not None:
                raise RunCoordinationError(
                    "The selected root continuation has unresolved deferred tool requests.",
                    code="session_deferred_pending",
                )
            snapshot = await self._store.objects.read_model(
                session.agent_snapshot.object,
                ResolvedAgentSnapshot,
            )
            reconstructed = self._agents.reconstruct(
                snapshot,
                subagent_operator=self._subagent_operator,
                root_capabilities=(
                    AgentUiSessionCapability(
                        service=self,
                        source_session_id=session.session_id,
                        binding=binding,
                    ),
                ),
            )
            environment = await self._environments.prepare(session, binding)
            result: HarnessRunResult[str] | None = None
            run_error: BaseException | None = None
            instance = AgentInstanceContext(
                identity=AgentIdentityRef(issuer="agent-ui", subject=session.session_id),
                agent_instance_id=f"agent-{uuid4().hex[:20]}",
                actor="agent-ui.root",
                host_refs={
                    "session_id": session.session_id,
                    "thread_id": session.root_thread_id,
                },
            )
            run_capability_ids = (
                reconstructed.run_capability_ids if isinstance(reconstructed, ReconstructedAgent) else frozenset()
            )
            bindings = RunBindings(
                instance=instance,
                model_resolver=reconstructed.model_resolver,
                capabilities=production_run_capabilities(run_capability_ids),
            )
            try:
                if isinstance(self._subagent_operator, AgentUiSubagentOperator):
                    stream = reconstructed.executable.stream(
                        prompt,
                        environments=environment.environments,
                        default_environment=environment.default_environment,
                        bindings=bindings,
                        previous_state=stored.harness_state,
                    )
                    await self._register_root(session.session_id, stream)
                    observer = HarnessAguiObserver()
                    try:
                        async with self._subagent_operator.bind_parent_run(
                            session_id=session.session_id,
                            thread_id=stream.thread_id,
                            run_id=stream.run_id,
                            agent_instance_id=instance.agent_instance_id,
                            binding=binding,
                            model_resolver=reconstructed.model_resolver,
                        ):
                            async with stream:
                                async for item in stream:
                                    try:
                                        events = observer.observe(item)
                                        await self._publish_live(
                                            session_id=session.session_id,
                                            thread_id=stream.thread_id,
                                            run_id=stream.run_id,
                                            events=events,
                                        )
                                    except Exception:
                                        pass
                                    if isinstance(item, HarnessRunResultEvent):
                                        result = item.result
                    finally:
                        with CancelScope(shield=True):
                            await self._unregister_root(session.session_id, stream)
                else:
                    result = await reconstructed.executable.run(
                        prompt,
                        environments=environment.environments,
                        default_environment=environment.default_environment,
                        bindings=bindings,
                        previous_state=stored.harness_state,
                    )
            except BaseException as exc:
                run_error = exc

            finalization: EnvironmentFinalization | None = None
            finalization_error: BaseException | None = None
            with CancelScope(shield=True):
                try:
                    finalization = await environment.finalize(
                        timeout_seconds=self._cleanup_timeout_seconds,
                    )
                except BaseException as exc:
                    finalization_error = exc
            if run_error is not None:
                if finalization_error is not None:
                    run_error.add_note(f"Environment finalization also failed: {finalization_error!r}")
                raise run_error
            if finalization_error is not None:
                raise finalization_error
            if result is None or finalization is None:  # pragma: no cover - closed construction above
                raise RuntimeError("Harness execution or Environment finalization produced no result")
            selection = await self._select_result(session, result)
            return RootRunOutcome(
                result=result,
                environment=finalization,
                continuation=selection,
            )
        finally:
            with CancelScope(shield=True):
                await self._release(session_id)

    async def _publish_live(
        self,
        *,
        session_id: str,
        thread_id: str,
        run_id: str,
        events: tuple[Any, ...],
    ) -> None:
        if self._live_hub is None:
            return
        try:
            await self._live_hub.publish(
                run_kind="root",
                session_id=session_id,
                thread_id=thread_id,
                run_id=run_id,
                events=events,
            )
        except Exception:
            return

    async def _load_continuation(self, session: Session) -> StoredSessionContinuation:
        stored = await self._store.objects.read_model(session.continuation, StoredSessionContinuation)
        if stored.harness_release != harness_version:
            raise SessionError(
                "The selected root continuation requires a different Harness release.",
                code="session_continuation_incompatible",
            )
        if stored.harness_state.thread_id != session.root_thread_id:
            raise SessionError(
                "The selected root continuation belongs to another Thread.",
                code="session_continuation_incompatible",
            )
        return stored

    async def _select_result(
        self,
        session: Session,
        result: HarnessRunResult[str],
    ) -> RootContinuationSelection:
        state = result.state
        if state is None or result.status not in {"completed", "suspended"}:
            return RootContinuationSelection(status="not_available")
        value = StoredSessionContinuation(
            harness_release=harness_version,
            harness_state=state,
            deferred_requests=result.deferred,
            created_at=datetime.now(UTC),
        )
        published_ref: ObjectRef | None = None
        try:
            published = await self._store.objects.publish_model(
                object_kind=ObjectKind.session_continuation,
                value=value,
            )
            published_ref = published.ref
            await self._store.sessions.select_continuation(
                session_id=session.session_id,
                expected=session.continuation,
                replacement=published_ref,
            )
        except Exception as exc:
            return RootContinuationSelection(
                status="failed",
                reference=published_ref,
                error=exc,
            )
        return RootContinuationSelection(status="selected", reference=published_ref)

    async def _claim(self, session_id: str) -> None:
        async with self._admission_lock:
            if session_id in self._active_sessions:
                raise RunCoordinationError(
                    "This Session already has an active root Run.",
                    code="session_run_active",
                )
            self._active_sessions.add(session_id)

    async def _register_root(self, session_id: str, stream: HarnessRunStream[Any]) -> None:
        async with self._admission_lock:
            if session_id not in self._active_sessions or session_id in self._active_roots:
                raise RunCoordinationError(
                    "The root Run admission is no longer current.",
                    code="session_run_admission_invalid",
                )
            self._active_roots[session_id] = stream

    async def _unregister_root(self, session_id: str, stream: HarnessRunStream[Any]) -> None:
        async with self._admission_lock:
            if self._active_roots.get(session_id) is stream:
                self._active_roots.pop(session_id, None)

    async def _release(self, session_id: str) -> None:
        async with self._admission_lock:
            self._active_roots.pop(session_id, None)
            self._active_sessions.discard(session_id)


__all__ = [
    "RootCancelResult",
    "RootContinuationSelection",
    "RootRunOutcome",
    "RootSteerResult",
    "SessionService",
]
