"""Root Thread creation, configuration, admission, and continuation coordination."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
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
from a13n_ui.composition import (
    AgentReconstructor,
    CompositionAcceptanceService,
    ResolvedRunComposition,
    RunCompositionService,
    ThreadCompositionSelection,
)
from a13n_ui.configuration import LoadedAgentUiConfiguration, ProjectResource
from a13n_ui.environment_runtime import EnvironmentFinalization, EnvironmentRunService
from a13n_ui.errors import RunCoordinationError, ThreadError
from a13n_ui.live import AgentUiLiveHub
from a13n_ui.model_runtime import SubscriptionSource
from a13n_ui.storage import (
    AgentResourceSource,
    LocalStore,
    ObjectKind,
    ObjectRef,
    StoredContinuation,
    StoredThreadInitialState,
    Thread,
    ThreadConfiguration,
    ThreadConfigurationMutation,
)
from a13n_ui.subagent_operator import AgentUiSubagentOperator
from a13n_ui.thread_capability import AgentUiThreadCapability


@dataclass(frozen=True, slots=True)
class RootThreadDefaults:
    project_id: str | None = None
    agent_id: str | None = None
    environment_profile_id: str | None = None
    harness_plugin_ids: tuple[str, ...] | None = None
    environment_run_extension_ids: tuple[str, ...] | None = None
    mcp_server_ids: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class ProjectProjection:
    project: ProjectResource
    last_active_at: datetime | None


@dataclass(frozen=True, slots=True)
class RootContinuationSelection:
    status: Literal["selected", "not_available", "failed"]
    reference: ObjectRef | None = None
    error: Exception | None = None


@dataclass(frozen=True, slots=True)
class RootRunOutcome:
    result: HarnessRunResult[str]
    environment: EnvironmentFinalization
    continuation: RootContinuationSelection
    composition: ObjectRef


@dataclass(frozen=True, slots=True)
class RootSteerResult:
    thread_id: str
    accepted: bool
    enqueue_id: str | None = None


@dataclass(frozen=True, slots=True)
class RootCancelResult:
    thread_id: str
    accepted: bool


class ThreadService:
    """The application service for root Thread state and one-live-Run admission."""

    def __init__(
        self,
        *,
        store: LocalStore,
        configurations: CompositionAcceptanceService,
        compositions: RunCompositionService,
        agent_reconstructor: AgentReconstructor,
        environment_service: EnvironmentRunService,
        subagent_operator: SubagentOperator | None = None,
        subscription_sources: dict[str, SubscriptionSource] | None = None,
        live_hub: AgentUiLiveHub | None = None,
        cleanup_timeout_seconds: float = 30.0,
    ) -> None:
        self._store = store
        self._configurations = configurations
        self._compositions = compositions
        self._agents = agent_reconstructor
        self._environments = environment_service
        self._subagent_operator = subagent_operator
        self._subscription_sources = dict(subscription_sources or {})
        self._live_hub = live_hub
        self._cleanup_timeout_seconds = cleanup_timeout_seconds
        self._admission_lock = Lock()
        self._active_threads: set[str] = set()
        self._active_roots: dict[str, HarnessRunStream[Any]] = {}

    async def create(
        self,
        *,
        defaults: RootThreadDefaults | None = None,
        title: str | None = None,
    ) -> Thread:
        source = await self._required_configuration()
        requested = defaults or RootThreadDefaults()
        project_id = requested.project_id or source.document.defaults.project
        agent_id = requested.agent_id or source.document.defaults.agent
        if project_id is None or project_id not in source.projects:
            raise ThreadError("A root Thread requires an available Project.", code="thread_project_missing")
        if agent_id is None or agent_id not in source.agents:
            raise ThreadError("A root Thread requires an available Agent.", code="thread_agent_missing")
        agent = source.agents[agent_id]
        environment_profile_id = (
            requested.environment_profile_id or source.document.defaults.environment_profile or "environment-native"
        )
        configuration = ThreadConfiguration(
            version=1,
            project_id=project_id,
            agent_source=AgentResourceSource(id=agent_id),
            environment_profile_id=environment_profile_id,
            harness_plugin_ids=(
                source.selected_plugins(agent) if requested.harness_plugin_ids is None else requested.harness_plugin_ids
            ),
            environment_run_extension_ids=(
                source.document.defaults.environment_run_extensions
                if requested.environment_run_extension_ids is None
                else requested.environment_run_extension_ids
            ),
            mcp_server_ids=(
                source.selected_mcp_servers(agent) if requested.mcp_server_ids is None else requested.mcp_server_ids
            ),
        )
        _validate_configuration(source, configuration, root=True)
        baseline = HarnessState.new()
        initial = await self._store.objects.publish_model(
            object_kind=ObjectKind.thread_initial_state,
            value=StoredThreadInitialState(harness_state=baseline, created_at=datetime.now(UTC)),
        )
        return await self._store.threads.create(
            thread_id=baseline.thread_id,
            configuration=configuration,
            initial_state=initial.ref,
            title=title,
        )

    async def get(self, thread_id: str) -> Thread:
        thread = await self._store.threads.get(thread_id)
        if thread is None:
            raise ThreadError("Thread does not exist.", code="thread_missing")
        return thread

    async def list(
        self,
        *,
        query: str | None = None,
        include_archived: bool = False,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[tuple[Thread, ...], int]:
        if query is not None and len(query) > 512:
            raise ThreadError("Thread query is too large.", code="thread_query_invalid")
        return await self._store.threads.list(
            query=query,
            include_archived=include_archived,
            offset=offset,
            limit=limit,
        )

    async def archive(self, *, thread_id: str, archived: bool = True) -> Thread:
        thread = await self.get(thread_id)
        if thread.parent_thread_id is not None:
            raise ThreadError("Child Threads are managed through their parent execution.", code="child_thread_scoped")
        return await self._store.threads.set_archived(thread_id=thread_id, archived=archived)

    async def update_configuration(
        self,
        *,
        thread_id: str,
        mutation: ThreadConfigurationMutation,
    ) -> Thread:
        thread = await self.get(thread_id)
        source = await self._required_configuration()
        replacement = mutation.patch.apply(thread.configuration)
        _validate_configuration(source, replacement, root=thread.parent_thread_id is None)
        return await self._store.threads.update_configuration(
            thread_id=thread_id,
            expected_version=mutation.expected_version,
            replacement=replacement,
        )

    async def inspect(
        self,
        *,
        thread_id: str,
        history_offset: int = 0,
        history_limit: int = 50,
    ) -> tuple[Thread, list[JsonValue], int]:
        if history_offset < 0 or not 1 <= history_limit <= 100:
            raise ThreadError("Thread history page is invalid.", code="thread_history_page_invalid")
        thread = await self.get(thread_id)
        state = await self._load_state(thread)
        history = state.message_history
        projected = ModelMessagesTypeAdapter.dump_python(
            list(history[history_offset : history_offset + history_limit]), mode="json"
        )
        if not isinstance(projected, list):
            raise RuntimeError("Model history projection is not a list")
        return thread, projected, len(history)

    async def projects(self) -> tuple[ProjectProjection, ...]:
        source = await self._required_configuration()
        threads, _ = await self._store.threads.list(
            include_children=True,
            include_archived=False,
            offset=0,
            limit=100,
        )
        recency: dict[str, datetime] = {}
        for thread in threads:
            current = recency.get(thread.configuration.project_id)
            if current is None or thread.updated_at > current:
                recency[thread.configuration.project_id] = thread.updated_at
        return tuple(
            ProjectProjection(project=item, last_active_at=recency.get(item.id))
            for item in sorted(source.projects.values(), key=lambda item: (item.position, item.id))
        )

    async def steer(self, *, thread_id: str, message: str) -> RootSteerResult:
        if not message.strip():
            raise RunCoordinationError("A steering message must not be blank.", code="run_input_invalid")
        async with self._admission_lock:
            stream = self._active_roots.get(thread_id)
        if stream is None:
            return RootSteerResult(thread_id=thread_id, accepted=False)
        try:
            enqueue_id = await stream.steer(message)
        except Exception:
            return RootSteerResult(thread_id=thread_id, accepted=False)
        return RootSteerResult(thread_id=thread_id, accepted=True, enqueue_id=enqueue_id)

    async def cancel(self, *, thread_id: str) -> RootCancelResult:
        async with self._admission_lock:
            stream = self._active_roots.get(thread_id)
        if stream is None:
            return RootCancelResult(thread_id=thread_id, accepted=False)
        stream.cancel()
        return RootCancelResult(thread_id=thread_id, accepted=True)

    async def run(
        self,
        *,
        thread_id: str,
        prompt: str,
        mutation: ThreadConfigurationMutation | None = None,
    ) -> RootRunOutcome:
        if not prompt.strip():
            raise RunCoordinationError("A root message must not be blank.", code="run_input_invalid")
        await self._claim(thread_id)
        try:
            thread = await self.get(thread_id)
            if thread.parent_thread_id is not None:
                raise ThreadError("run_thread accepts only root Threads.", code="child_thread_scoped")
            if mutation is not None:
                thread = await self.update_configuration(thread_id=thread_id, mutation=mutation)
            source = await self._required_configuration()
            selection = _selection(thread)
            published = await self._compositions.publish(source, selection)
            previous_state, deferred = await self._load_run_state(thread)
            if deferred is not None:
                raise RunCoordinationError(
                    "The selected Thread continuation has unresolved deferred tool requests.",
                    code="thread_deferred_pending",
                )
            reconstructed = self._agents.reconstruct(
                published.value,
                subagent_operator=self._subagent_operator,
                root_capabilities=(
                    AgentUiThreadCapability(
                        service=self,
                        source_thread_id=thread.thread_id,
                    ),
                ),
                subscription_sources=self._subscription_sources,
            )
            environment = await self._environments.prepare(published.value)
            instance = AgentInstanceContext(
                identity=AgentIdentityRef(issuer="agent-ui", subject=thread.thread_id),
                agent_instance_id=f"agent-{uuid4().hex[:20]}",
                actor="agent-ui.root",
                host_refs={"thread_id": thread.thread_id},
            )
            bindings = RunBindings(
                instance=instance,
                environment=environment.runtime,
                model_resolver=reconstructed.model_resolver,
                capabilities=production_run_capabilities(reconstructed.definition_capability_ids),
            )
            result: HarnessRunResult[str] | None = None
            run_error: BaseException | None = None
            try:
                stream = reconstructed.executable.stream(
                    prompt,
                    bindings=bindings,
                    previous_state=previous_state,
                )
                await self._register_root(thread.thread_id, stream)
                observer = HarnessAguiObserver()
                try:
                    async with self._bind_subagent_parent(
                        thread_id=thread.thread_id,
                        run_id=stream.run_id,
                        instance=instance,
                        composition=published.value,
                    ):
                        async with stream:
                            async for item in stream:
                                try:
                                    await self._publish_live(
                                        thread_id=thread.thread_id,
                                        run_id=stream.run_id,
                                        events=observer.observe(item),
                                    )
                                except Exception:
                                    pass
                                if isinstance(item, HarnessRunResultEvent):
                                    result = item.result
                finally:
                    with CancelScope(shield=True):
                        await self._unregister_root(thread.thread_id, stream)
            except BaseException as exc:
                run_error = exc

            finalization: EnvironmentFinalization | None = None
            finalization_error: Exception | None = None
            with CancelScope(shield=True):
                try:
                    finalization = await environment.finalize(timeout_seconds=self._cleanup_timeout_seconds)
                except Exception as exc:
                    finalization_error = exc
            if run_error is not None:
                if finalization_error is not None:
                    run_error.add_note(f"Environment finalization also failed: {finalization_error!r}")
                raise run_error
            if result is None:
                raise RuntimeError("Harness execution did not produce a terminal result")
            if finalization is None:
                assert finalization_error is not None
                finalization = EnvironmentFinalization(
                    cleanup_errors=(finalization_error,),
                    state_publications=(),
                )
            continuation = await self._select_result(
                thread=thread,
                composition=published.reference,
                result=result,
            )
            return RootRunOutcome(
                result=result,
                environment=finalization,
                continuation=continuation,
                composition=published.reference,
            )
        finally:
            with CancelScope(shield=True):
                await self._release(thread_id)

    @asynccontextmanager
    async def _bind_subagent_parent(
        self,
        *,
        thread_id: str,
        run_id: str,
        instance: AgentInstanceContext,
        composition: ResolvedRunComposition,
    ) -> AsyncIterator[None]:
        operator = self._subagent_operator
        if isinstance(operator, AgentUiSubagentOperator):
            async with operator.bind_parent_run(
                thread_id=thread_id,
                run_id=run_id,
                instance=instance,
                composition=composition,
            ):
                yield
            return
        yield

    async def _required_configuration(self) -> LoadedAgentUiConfiguration:
        source = await self._configurations.current()
        if source is None:
            raise ThreadError(
                "No accepted Agent UI configuration is selected.",
                code="configuration_not_accepted",
            )
        return source

    async def _load_state(self, thread: Thread) -> HarnessState:
        state, _deferred = await self._load_run_state(thread)
        return state

    async def _load_run_state(self, thread: Thread) -> tuple[HarnessState, object | None]:
        if thread.continuation is None:
            stored = await self._store.objects.read_model(thread.initial_state, StoredThreadInitialState)
            state = stored.harness_state
            deferred = None
        else:
            stored_continuation = await self._store.objects.read_model(thread.continuation, StoredContinuation)
            state = stored_continuation.harness_state
            deferred = stored_continuation.deferred_requests
        if state.thread_id != thread.thread_id:
            raise ThreadError(
                "The selected Thread state belongs to another Thread.",
                code="thread_continuation_incompatible",
            )
        return state, deferred

    async def _select_result(
        self,
        *,
        thread: Thread,
        composition: ObjectRef,
        result: HarnessRunResult[str],
    ) -> RootContinuationSelection:
        if result.state is None or result.status not in {"completed", "suspended"}:
            return RootContinuationSelection(status="not_available")
        published_ref: ObjectRef | None = None
        try:
            published_ref = (
                await self._store.objects.publish_model(
                    object_kind=ObjectKind.continuation,
                    value=StoredContinuation(
                        harness_release=harness_version,
                        run_composition=composition,
                        harness_state=result.state,
                        deferred_requests=result.deferred,
                        created_at=datetime.now(UTC),
                    ),
                )
            ).ref
            await self._store.threads.select_continuation(
                thread_id=thread.thread_id,
                expected=thread.continuation,
                replacement=published_ref,
            )
        except Exception as exc:
            return RootContinuationSelection(status="failed", reference=published_ref, error=exc)
        return RootContinuationSelection(status="selected", reference=published_ref)

    async def _publish_live(
        self,
        *,
        thread_id: str,
        run_id: str,
        events: tuple[Any, ...],
    ) -> None:
        if self._live_hub is None:
            return
        await self._live_hub.publish(
            run_kind="root",
            thread_id=thread_id,
            run_id=run_id,
            events=events,
        )

    async def _claim(self, thread_id: str) -> None:
        async with self._admission_lock:
            if thread_id in self._active_threads:
                raise RunCoordinationError(
                    "This Thread already has an active root Run.",
                    code="thread_run_active",
                )
            self._active_threads.add(thread_id)

    async def _register_root(self, thread_id: str, stream: HarnessRunStream[Any]) -> None:
        async with self._admission_lock:
            if thread_id not in self._active_threads or thread_id in self._active_roots:
                raise RunCoordinationError(
                    "The root Run admission is no longer current.",
                    code="thread_run_admission_invalid",
                )
            self._active_roots[thread_id] = stream

    async def _unregister_root(self, thread_id: str, stream: HarnessRunStream[Any]) -> None:
        async with self._admission_lock:
            if self._active_roots.get(thread_id) is stream:
                self._active_roots.pop(thread_id, None)

    async def _release(self, thread_id: str) -> None:
        async with self._admission_lock:
            self._active_roots.pop(thread_id, None)
            self._active_threads.discard(thread_id)


def _selection(thread: Thread) -> ThreadCompositionSelection:
    source = thread.configuration.agent_source
    return ThreadCompositionSelection(
        thread_id=thread.thread_id,
        version=thread.configuration.version,
        project_id=thread.configuration.project_id,
        agent_source_kind=source.kind,
        agent_source_id=source.id,
        environment_profile_id=thread.configuration.environment_profile_id,
        harness_plugin_ids=thread.configuration.harness_plugin_ids,
        environment_run_extension_ids=thread.configuration.environment_run_extension_ids,
        mcp_server_ids=thread.configuration.mcp_server_ids,
    )


def _validate_configuration(
    source: LoadedAgentUiConfiguration,
    value: ThreadConfiguration,
    *,
    root: bool,
) -> None:
    if value.project_id not in source.projects:
        raise ThreadError("The selected Project is unavailable.", code="thread_project_missing")
    if root and value.agent_source.kind != "agent":
        raise ThreadError("A root Thread cannot select a Markdown source.", code="thread_agent_invalid")
    resources = source.agents if value.agent_source.kind == "agent" else source.subagents
    if value.agent_source.id not in resources:
        raise ThreadError("The selected Agent source is unavailable.", code="thread_agent_missing")
    if (
        value.environment_profile_id != "environment-native"
        and value.environment_profile_id not in source.environment_profiles
    ):
        raise ThreadError(
            "The selected Environment profile is unavailable.",
            code="thread_environment_missing",
        )
    for selected, resources, code in (
        (value.harness_plugin_ids, source.harness_plugins, "thread_plugin_missing"),
        (
            value.environment_run_extension_ids,
            source.environment_run_extensions,
            "thread_run_extension_missing",
        ),
        (value.mcp_server_ids, source.mcp_servers, "thread_mcp_missing"),
    ):
        if any(item not in resources for item in selected):
            raise ThreadError("A selected Thread resource is unavailable.", code=code)


__all__ = [
    "ProjectProjection",
    "RootCancelResult",
    "RootContinuationSelection",
    "RootRunOutcome",
    "RootSteerResult",
    "RootThreadDefaults",
    "ThreadService",
]
