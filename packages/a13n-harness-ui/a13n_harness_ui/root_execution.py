"""One fresh root Harness execution and continuation publication."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from functools import partial
from typing import Any, Literal
from uuid import uuid4

from a13n_harness import (
    AgentIdentityRef,
    AgentInstanceContext,
    DeferredToolResume,
    HarnessEvent,
    HarnessRunResult,
    HarnessRunResultEvent,
    HarnessRunStream,
    HarnessState,
    RunBindings,
)
from a13n_harness import __version__ as harness_version
from a13n_harness.capabilities import AskUserQuestionRequest, SubagentOperator, UserQuestionAnswers
from a13n_harness.context import AgentContext
from a13n_harness.environment.dynamic import DynamicEnvironmentCapability
from a13n_harness.input import RunInputFactory, RunInputValue, RunPreparationContext
from a13n_harness.observation import record_span_metadata
from a13n_harness.pricing import get_current_pricing_catalog
from a13n_harness.usage import UsageSnapshot
from a13n_logging import get_logger
from anyio import CancelScope, get_cancelled_exc_class, to_thread
from opentelemetry.trace import StatusCode
from pydantic_ai import RunContext, ToolDenied, ToolFailed
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import AgentStreamEvent, FunctionToolResultEvent
from pydantic_ai.tools import DeferredToolApprovalResult, DeferredToolRequests, ToolApproved

from a13n_harness_ui.capability_runtime import production_run_bindings
from a13n_harness_ui.composition import (
    AgentReconstructor,
    CompositionAcceptanceService,
    PublishedRunComposition,
    ResolvedRunComposition,
    RunCompositionService,
    ThreadCompositionSelection,
)
from a13n_harness_ui.configuration import LoadedHarnessUiConfiguration
from a13n_harness_ui.conversation import ConversationExcerpt, ExcerptCollector, checkpoint_excerpt
from a13n_harness_ui.diagnostics import exception_feedback
from a13n_harness_ui.display_history import (
    DisplayHistory,
    DisplayHistoryCollector,
    DisplayPublication,
    detach_display_history,
    saved_display_history,
    with_display_history,
)
from a13n_harness_ui.environment_bindings import EnvironmentSelectionPatch
from a13n_harness_ui.environment_runtime import EnvironmentFinalization, EnvironmentRunService
from a13n_harness_ui.errors import RunCoordinationError, ThreadError
from a13n_harness_ui.goal import GoalCapability, GoalView, saved_goal, with_goal
from a13n_harness_ui.live import HarnessUiLiveHub, HarnessUiSummaryHub
from a13n_harness_ui.mcp_apps.snapshots import AppSnapshots
from a13n_harness_ui.memory import MemoryOrganizationRun
from a13n_harness_ui.model_runtime import SubscriptionSource
from a13n_harness_ui.observation import (
    phase,
    record_configuration,
    record_output,
    record_phase_result,
    record_skill_event,
)
from a13n_harness_ui.restart import GracefulRestart, RestartPauseCapability
from a13n_harness_ui.restart_models import RestartItem
from a13n_harness_ui.root_checkpoint import RootCheckpointCapability
from a13n_harness_ui.root_input import RootInputFiles, detach_input
from a13n_harness_ui.storage import (
    LocalStore,
    ObjectKind,
    ObjectRef,
    StoredContinuation,
    StoredThreadInitialState,
    Thread,
    ThreadConfigurationMutation,
)
from a13n_harness_ui.storage.checkpoint_lock import checkpoint_lock
from a13n_harness_ui.storage.contracts import StoredDeferredInput, ThreadReadModel
from a13n_harness_ui.storage.read_models import project_continuation
from a13n_harness_ui.subagent_operator import HarnessUiSubagentOperator
from a13n_harness_ui.surfaces import ApprovalDecision, ExternalToolResult, RunModelOverrides, ThreadDeferredResponse
from a13n_harness_ui.thread_files import ThreadFiles
from a13n_harness_ui.thread_projection import build_thread_inspection
from a13n_harness_ui.thread_service import ThreadService
from a13n_harness_ui.thread_work import ThreadWorkService
from a13n_harness_ui.tool_evidence import ToolEvidenceCollector
from a13n_harness_ui.tool_images import ToolImageCollector


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
    interaction_timeout_seconds: float = 120.0


@dataclass(frozen=True, slots=True)
class RootRunAdmission:
    """Detached input captured before scheduling, with no live Environment resources."""

    thread: Thread
    source: LoadedHarnessUiConfiguration
    published: PublishedRunComposition
    previous_state: HarnessState
    deferred_resume: DeferredToolResume | None
    prompt: RunInputValue | None
    response: ThreadDeferredResponse | None
    memory_positions: dict[str, str | None] = field(default_factory=dict)
    organization: MemoryOrganizationRun | None = None
    resume_usage: bool = False


class RootRunExecutor:
    """Execute one coordinator-admitted root input with fresh native collaborators."""

    def __init__(
        self,
        *,
        store: LocalStore,
        threads: ThreadService,
        configurations: CompositionAcceptanceService,
        compositions: RunCompositionService,
        agent_reconstructor: AgentReconstructor,
        environment_service: EnvironmentRunService,
        subagent_operator: SubagentOperator | None = None,
        subscription_sources: Mapping[str, SubscriptionSource] | None = None,
        live_hub: HarnessUiLiveHub | None = None,
        summary_hub: HarnessUiSummaryHub | None = None,
        cleanup_timeout_seconds: float = 30.0,
        thread_files: ThreadFiles | None = None,
        work: ThreadWorkService | None = None,
        restart_coordinator: GracefulRestart | None = None,
        mcp_apps: AppSnapshots | None = None,
    ) -> None:
        self._store = store
        self._mcp_apps = mcp_apps
        self._restart = restart_coordinator
        self._threads = threads
        self._configurations = configurations
        self._compositions = compositions
        self._agents = agent_reconstructor
        self._environments = environment_service
        self._subagent_operator = subagent_operator
        self._subscription_sources = dict(subscription_sources or {})
        self._live_hub = live_hub
        self._summary_hub = summary_hub
        self._cleanup_timeout_seconds = cleanup_timeout_seconds
        self._thread_files = thread_files
        self._work = work
        self._root_capability_factory: Callable[[ResolvedRunComposition], AbstractCapability[AgentContext]] | None = (
            None
        )

    def replace_subscription_sources(self, sources: Mapping[str, SubscriptionSource]) -> None:
        """Apply account rediscovery to future Runs; existing resolvers retain their sources."""
        self._subscription_sources = dict(sources)

    def set_root_capability_factory(
        self,
        factory: Callable[[ResolvedRunComposition], AbstractCapability[AgentContext]],
    ) -> None:
        if self._root_capability_factory is not None:
            raise RuntimeError("Root Thread Capability factory is already configured")
        self._root_capability_factory = factory

    async def capture(
        self,
        *,
        thread_id: str,
        prompt: RunInputValue | None = None,
        response: ThreadDeferredResponse | None = None,
        restart: RestartItem | None = None,
        mutation: ThreadConfigurationMutation | None = None,
        model_overrides: RunModelOverrides | None = None,
        environment: EnvironmentSelectionPatch | None = None,
        organization: MemoryOrganizationRun | None = None,
    ) -> RootRunAdmission:
        """Resolve admission using detached store reads; never connect to a Device."""
        if sum(value is not None for value in (prompt, response, restart)) != 1:
            raise RunCoordinationError(
                "A root operation requires one prompt, deferred response, or planned continuation.",
                code="run_input_invalid",
            )
        prompt = None if prompt is None else detach_input(prompt)
        response = None if response is None else response.model_copy(deep=True)
        thread = await self._threads.get(thread_id)
        if thread.parent_thread_id is not None:
            raise ThreadError("run_thread accepts only root Threads.", code="child_thread_scoped")
        if thread.archived:
            raise ThreadError("An archived Thread cannot run.", code="thread_archived")
        if thread.memory_scope is not None:
            if organization is None or thread.memory_scope != organization.scope.key or prompt is None:
                raise ThreadError("Memory Threads are observation-only.", code="memory_thread_read_only")
            source = organization.source
            published = await self._compositions.publish_memory(source, thread)
            previous, _, _, _, _ = await self._load_run_state(thread)

            def prepare_memory_history() -> HarnessState:
                saved = saved_display_history(previous)
                display = DisplayHistoryCollector(previous.message_history, saved).capture()
                return with_display_history(HarnessState.new(thread_id=thread_id), display)

            fresh = await to_thread.run_sync(prepare_memory_history)
            return RootRunAdmission(thread, source, published, fresh, None, prompt, None, organization=organization)
        if organization is not None:
            raise ThreadError("Organization requires a Memory Thread.", code="memory_thread_required")
        if mutation is not None:
            thread = await self._threads.update_configuration(thread_id=thread_id, mutation=mutation)
        if restart is not None and (thread.continuation != restart.checkpoint or thread_id != restart.thread_id):
            raise RunCoordinationError("The saved restart continuation changed.", code="restart_conflict")
        previous_state, deferred, previous_composition, accepted, positions = await self._load_run_state(thread)
        if prompt is not None and deferred is not None:
            assert thread.continuation is not None
            response = ThreadDeferredResponse(
                expected_continuation_id=thread.continuation.logical_digest,
                responses=(),
            )
        deferred_resume = _deferred_resume(thread=thread, requests=deferred, response=response)
        if deferred_resume is None and accepted is not None:
            deferred_resume = accepted.recover()
        owner = (await self._store.threads.worker_owners((thread_id,))).get(thread_id)
        selection = replace(
            _selection(thread),
            role="worker"
            if owner is not None
            else "coordinator"
            if await self._store.threads.is_coordinator(thread_id)
            else "ordinary",
            coordinator_thread_id=owner,
        )
        if environment is not None:
            selection = replace(
                selection,
                local_roots=selection.local_roots if environment.local_roots is None else environment.local_roots,
                environment_profile_id=environment.environment_profile_id or selection.environment_profile_id,
                environment_bindings=(
                    selection.environment_bindings
                    if environment.environment_bindings is None
                    else environment.environment_bindings
                ),
                default_environment=(
                    environment.default_environment
                    if "default_environment" in environment.model_fields_set
                    else selection.default_environment
                ),
            )
        if response is not None:
            assert previous_composition is not None
            captured = await self._store.objects.read_model(previous_composition, ResolvedRunComposition)
            patched = set() if mutation is None else set(mutation.patch.model_fields_set)
            if environment is not None:
                patched.update(environment.model_fields_set)
            selection = replace(
                selection,
                environment_profile_id=(
                    captured.environment_profile.profile_id
                    if "environment_profile_id" not in patched
                    else selection.environment_profile_id
                ),
                local_roots=(captured.project_roots if "local_roots" not in patched else selection.local_roots),
                environment_bindings=(
                    tuple(item.selection for item in captured.environment_bindings)
                    if "environment_bindings" not in patched
                    else selection.environment_bindings
                ),
                default_environment=(
                    captured.default_environment
                    if "default_environment" not in patched
                    else selection.default_environment
                ),
            )
        if restart is None:
            source = await self._required_configuration()
            published = await self._compositions.publish(source, selection, model_overrides=model_overrides)
        else:
            captured = await self._store.objects.read_model(restart.composition, ResolvedRunComposition)
            source = await self._configurations.load(captured.generation_digest)
            # Restart retains the captured dependencies, but coordination belongs to the
            # durable Thread, just as in ordinary/deferred admission.
            captured = captured.model_copy(
                update={"role": selection.role, "coordinator_thread_id": selection.coordinator_thread_id}
            )
            envelope = await self._store.objects.publish_model(object_kind=ObjectKind.run_composition, value=captured)
            published = PublishedRunComposition(value=captured, reference=envelope.ref)
        if restart is not None:
            previous_state = await self._store.usage.restore(thread_id=thread_id, state=previous_state)
        return RootRunAdmission(
            thread,
            source,
            published,
            previous_state,
            deferred_resume,
            prompt,
            response,
            positions,
            resume_usage=restart is not None and UsageSnapshot.from_state(previous_state) is not None,
        )

    async def execute(
        self,
        admission: RootRunAdmission,
        *,
        on_stream: Callable[[HarnessRunStream[Any], RootInputFiles | None], Awaitable[None]] | None = None,
        goal: GoalView | None = None,
        on_goal: Callable[[GoalView], Awaitable[None]] | None = None,
    ) -> RootRunOutcome:
        thread = admission.thread
        thread_id = thread.thread_id
        run_checkpoint: ObjectRef | None = None
        source, published = admission.source, admission.published
        previous_state, deferred_resume = admission.previous_state, admission.deferred_resume
        prompt = admission.prompt
        with phase("prepare") as preparation_span:
            record_span_metadata(
                preparation_span,
                {"prepare.input_kind": "deferred_response" if admission.response is not None else "prompt"},
            )

            def prepare_history() -> tuple[HarnessState, GoalView | None, DisplayHistoryCollector]:
                # Admission without a prompt is an explicit deferred/planned continuation;
                # an ordinary prompt can never reactivate a checkpointed Goal.
                selected_goal = saved_goal(previous_state) if admission.prompt is None else goal
                state, saved = detach_display_history(previous_state)
                display = DisplayHistoryCollector(state.message_history, saved)
                # The collector owns inspection history during execution. Native
                # snapshots need not repeatedly decode and serialize that history;
                # _select_state reattaches it at every durable root boundary.
                state = with_goal(state, selected_goal)
                return state, selected_goal, display

            # These detached snapshots can be large. Join preparation before any
            # stream or Capability hook can access the collector, even on cancellation.
            previous_state, goal, display = await to_thread.run_sync(prepare_history)
            goal_capability = GoalCapability(goal, changed=on_goal)
            base_continuation_id = thread.continuation.logical_digest if thread.continuation is not None else None

            async def save_checkpoint(state: HarnessState) -> tuple[str, str]:
                nonlocal thread, run_checkpoint
                assert stream is not None
                frozen = display.capture(state.message_history)
                excerpt = await to_thread.run_sync(lambda: checkpoint_excerpt(thread.excerpt, state.message_history))
                # RootCheckpointCapability joins this operation and its marker
                # before propagating either native or AnyIO cancellation.
                selected = await self._select_state(
                    thread=thread,
                    superseded=run_checkpoint,
                    composition=published.reference,
                    memory_positions=reconstructed.memory_cursors.snapshot(),
                    accepted=stream.pending_deferred_input,
                    state=state,
                    display=frozen,
                    excerpt=excerpt,
                    activity_changed=excerpt != thread.excerpt,
                )
                if selected.status != "selected" or selected.reference is None:
                    assert selected.error is not None
                    raise selected.error
                run_checkpoint = selected.reference
                thread = thread.model_copy(update={"continuation": selected.reference, "excerpt": excerpt})
                if self._summary_hub is not None:
                    await self._summary_hub.publish(kind="thread", thread_id=thread_id)
                return selected.reference.logical_digest, str(frozen.position)

            async def retain_tool_presentation(event: AgentStreamEvent) -> None:
                if not isinstance(event, FunctionToolResultEvent):
                    return
                assert stream is not None
                observed = HarnessEvent(
                    thread_id=thread_id,
                    run_id=stream.run_id,
                    sequence=0,
                    occurred_at=datetime.now(UTC),
                    event=event,
                )
                image_events = await tool_images.observe(observed)
                app_events = (
                    await self._mcp_apps.observe(observed, thread_id=thread_id, run_id=stream.run_id)
                    if self._mcp_apps is not None
                    else ()
                )
                display.supplement((*image_events, *app_events))

            preparation_span.set_attribute("a13n.phase.step", "reconstruction")
            pricing_catalog = await to_thread.run_sync(get_current_pricing_catalog)
            reconstructed = self._agents.reconstruct(
                published.value,
                pricing_catalog=pricing_catalog,
                memory_positions=admission.memory_positions,
                organization=admission.organization,
                subagent_operator=self._subagent_operator,
                root_capabilities=(
                    goal_capability,
                    RootCheckpointCapability(save_checkpoint),
                    _ToolPresentationCapability(retain_tool_presentation),
                    *(
                        (RestartPauseCapability(self._restart, thread_id),)
                        if self._restart is not None and admission.organization is None
                        else ()
                    ),
                    *(
                        ()
                        if self._root_capability_factory is None or admission.organization is not None
                        else (self._root_capability_factory(published.value),)
                    ),
                ),
                subscription_sources=self._subscription_sources,
            )
            record_configuration(published.value, reconstructed.definition_capability_ids)
            input_files = (
                RootInputFiles(
                    self._thread_files,
                    thread_id,
                    source.document.input,
                    view_enabled=(published.value.root.tools is None or "view" in published.value.root.tools)
                    and any(
                        isinstance(capability, DynamicEnvironmentCapability) and capability.configuration.files_enabled
                        for capability in reconstructed.executable.definition.capabilities
                    ),
                )
                if self._thread_files is not None and admission.organization is None
                else None
            )
            input_factory: RunInputFactory | None = None
            if prompt is not None and input_files is not None:
                submitted = prompt

                async def prepare_input(context: RunPreparationContext) -> RunInputValue:
                    assert input_files is not None
                    return await input_files.prepare(submitted, context.environment)

                input_factory = prepare_input
                prompt = None
            preparation_span.set_attribute("a13n.phase.step", "environment")
            environment = (
                None if admission.organization is not None else await self._environments.prepare(published.value)
            )
            instance = AgentInstanceContext(
                identity=AgentIdentityRef(issuer="a13n-harness-ui", subject=thread.thread_id),
                agent_instance_id=f"agent-{uuid4().hex[:20]}",
                actor="a13n-harness-ui.root",
                host_refs={"thread_id": thread.thread_id},
            )
            bindings = RunBindings(
                instance=instance,
                configuration=reconstructed.run_configuration,
                environment=None if environment is None else environment.runtime,
                tool_result_directory=None if environment is None else environment.tool_result_directory,
                model_resolver=reconstructed.model_resolver,
                usage_reporter=self._store.usage.reporter(thread_id),
                producer_observer=display.observe,
                file_media_understanding=reconstructed.file_media_understanding(previous_state.thread_id),
                working_state_observer=(
                    partial(self._work.observe, thread_id, base_continuation_id=base_continuation_id)
                    if self._work is not None
                    else None
                ),
            )
            bindings = production_run_bindings(
                bindings,
                reconstructed.definition_capability_ids,
            )
            record_phase_result(
                preparation_span,
                status="completed",
                continuation_loaded=previous_state is not None,
                deferred_resume=deferred_resume is not None,
                capability_count=len(reconstructed.definition_capability_ids),
                environment_prepared=True,
            )
        result: HarnessRunResult[str] | None = None
        stream: HarnessRunStream[str] | None = None
        stream_entry_attempted = False
        run_error: BaseException | None = None
        excerpts: ExcerptCollector | None = None
        try:
            stream = reconstructed.executable.stream(
                prompt,
                input_factory=input_factory,
                bindings=bindings,
                previous_state=previous_state,
                resume_usage=admission.resume_usage,
                deferred_resume=deferred_resume,
            )
            excerpts = ExcerptCollector(thread.excerpt, run_id=stream.run_id)
            tool_evidence = ToolEvidenceCollector(run_id=stream.run_id)
            tool_images = ToolImageCollector(run_id=stream.run_id, thread_id=thread.thread_id, files=self._thread_files)
            if on_stream is not None:
                await on_stream(stream, input_files)
            async with self._bind_subagent_parent(
                thread_id=thread.thread_id,
                run_id=stream.run_id,
                instance=instance,
                composition=published.value,
            ):
                stream_entry_attempted = True
                async with stream:
                    async for item in stream:
                        record_skill_event(item)
                        excerpts.observe(item)
                        tool_evidence.observe(item)
                        await self._store.usage.observe(thread_id=thread.thread_id, item=item)
                        if isinstance(item, HarnessRunResultEvent):
                            display.observe(item)
                            result = item.result
                        publication = display.drain()
                        if publication is not None:
                            try:
                                await self._publish_live(
                                    thread_id=thread.thread_id,
                                    run_id=stream.run_id,
                                    display=publication,
                                    base_continuation_id=base_continuation_id,
                                )
                            except Exception:
                                display.publication_failed()
        except BaseException as exc:
            run_error = exc
            if result is None and stream is not None:
                # A suspended candidate includes deferred requests as well as state.
                # Preserve the whole envelope even when cleanup/cancellation prevents delivery.
                result = stream.outcome
        finally:
            if self._mcp_apps is not None and stream is not None:
                self._mcp_apps.connections.captures.discard_run(stream.run_id)

        if result is not None:
            record_output(result.output, status=result.status)
        finalization: EnvironmentFinalization | None = None
        finalization_error: Exception | None = None
        continuation = RootContinuationSelection(status="not_available")
        with phase("finalize") as finalization_span:
            with CancelScope(shield=True):
                finalization_span.set_attribute("a13n.phase.step", "environment")
                try:
                    finalization = (
                        EnvironmentFinalization(cleanup_errors=(), state_publications=())
                        if environment is None
                        else await environment.finalize(timeout_seconds=self._cleanup_timeout_seconds)
                    )
                except Exception as exc:
                    finalization_error = exc
                if (
                    result is not None
                    and result.failure is not None
                    and stream is not None
                    and stream.diagnostic_error is not None
                ):
                    feedback = await to_thread.run_sync(
                        partial(
                            exception_feedback,
                            stream.diagnostic_error,
                            thread_id=thread.thread_id,
                            run_id=stream.run_id,
                            phase="root_execution",
                        )
                    )
                    result = result.replace(
                        failure=result.failure.model_copy(update={"message": f"{result.failure.message}\n{feedback}"})
                    )
                finalization_span.set_attribute("a13n.phase.step", "continuation")
                paused_state = self._restart.saved_state(thread_id) if self._restart is not None else None
                if paused_state is not None:
                    continuation = await self._select_state(
                        thread=thread,
                        superseded=run_checkpoint,
                        composition=published.reference,
                        memory_positions=reconstructed.memory_cursors.snapshot(),
                        accepted=deferred_resume if stream is None else stream.pending_deferred_input,
                        state=paused_state,
                        display=display.capture(paused_state.message_history) if display is not None else None,
                        excerpt=checkpoint_excerpt(thread.excerpt, paused_state.message_history),
                        activity_changed=True,
                    )
                    if (
                        continuation.reference is not None
                        and continuation.status == "selected"
                        and finalization is not None
                        and not finalization.cleanup_errors
                        and all(publication.status != "failed" for publication in finalization.state_publications)
                        and finalization_error is None
                        and stream is not None
                    ):
                        assert self._restart is not None
                        self._restart.saved(
                            RestartItem(
                                thread_id=thread_id,
                                root_thread_id=thread_id,
                                run_id=stream.run_id,
                                composition=published.reference,
                                checkpoint=continuation.reference,
                            )
                        )
                elif result is not None:
                    if result.state is not None:
                        goal_status = (
                            "cancelled"
                            if isinstance(run_error, get_cancelled_exc_class())
                            else "failed"
                            if run_error is not None
                            else result.status
                        )
                        result = result.replace(
                            state=await goal_capability.finish(
                                result.state,
                                status=goal_status,
                                usage=result.usage,
                            )
                        )
                    completed_run_id = (
                        stream.run_id
                        if stream is not None and result.status == "completed" and run_error is None
                        else None
                    )
                    continuation = await self._select_state(
                        thread=thread,
                        superseded=run_checkpoint,
                        composition=published.reference,
                        memory_positions=reconstructed.memory_cursors.snapshot(),
                        accepted=deferred_resume if stream is None else stream.pending_deferred_input,
                        state=result.state,
                        display=(
                            display.capture(result.state.message_history, completed=completed_run_id is not None)
                            if display is not None and result.state is not None
                            else None
                        ),
                        deferred=result.deferred,
                        completed_run_id=completed_run_id,
                        excerpt=thread.excerpt if excerpts is None else excerpts.finish(result),
                        activity_changed=excerpts is not None and excerpts.changed,
                    )
                elif stream is not None and stream_entry_attempted:
                    try:
                        state = await stream.export_state()
                    except Exception as exc:
                        continuation = RootContinuationSelection(status="failed", error=exc)
                    else:
                        state = await goal_capability.finish(
                            state,
                            status="cancelled" if isinstance(run_error, get_cancelled_exc_class()) else "failed",
                        )
                        continuation = await self._select_state(
                            thread=thread,
                            superseded=run_checkpoint,
                            composition=published.reference,
                            memory_positions=reconstructed.memory_cursors.snapshot(),
                            accepted=deferred_resume if stream is None else stream.pending_deferred_input,
                            state=state,
                            display=display.capture(state.message_history) if display is not None else None,
                            excerpt=thread.excerpt if excerpts is None else excerpts.finish(None),
                            activity_changed=excerpts is not None and excerpts.changed,
                        )
                else:
                    continuation = RootContinuationSelection(status="not_available")
                if continuation.error is not None:
                    get_logger(__name__).error(
                        "Root continuation save failed: thread_id=%s exception_type=%s",
                        thread.thread_id,
                        type(continuation.error).__name__,
                    )
                    if run_error is not None:
                        run_error.add_note(
                            "The latest continuation could not be saved; the previous selection is unchanged."
                        )
            finalization_span.set_attribute("a13n.ui.continuation.status", continuation.status)
            phase_failed = (
                finalization_error is not None
                or continuation.error is not None
                or bool(finalization is not None and finalization.cleanup_errors)
            )
            record_phase_result(
                finalization_span,
                status="failed" if phase_failed else "completed",
                continuation_status=continuation.status,
                environment_finalized=finalization is not None,
                cleanup_error_count=len(finalization.cleanup_errors) if finalization is not None else 0,
                result_status=result.status if result is not None else "unavailable",
            )
            if (
                finalization_error is not None
                or continuation.error is not None
                or (finalization is not None and finalization.cleanup_errors)
            ):
                finalization_span.set_attribute("a13n.phase.status", "failed")
                finalization_span.set_status(StatusCode.ERROR)
        if stream is not None and self._work is not None:
            with CancelScope(shield=True):
                await self._work.finish(thread_id, stream.run_id)
        if stream is not None and self._live_hub is not None:
            with CancelScope(shield=True):
                await self._live_hub.finish_root(
                    thread_id=thread.thread_id,
                    run_id=stream.run_id,
                    saved_continuation_id=(
                        continuation.reference.logical_digest
                        if continuation.status == "selected" and continuation.reference is not None
                        else None
                    ),
                )
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
        return RootRunOutcome(
            result=result,
            environment=finalization,
            continuation=continuation,
            composition=published.reference,
            interaction_timeout_seconds=source.document.tools.interaction_timeout_seconds,
        )

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
        if isinstance(operator, HarnessUiSubagentOperator):
            async with operator.bind_parent_run(
                thread_id=thread_id,
                run_id=run_id,
                instance=instance,
                composition=composition,
            ):
                yield
            return
        yield

    async def _required_configuration(self) -> LoadedHarnessUiConfiguration:
        source = await self._configurations.current()
        if source is None:
            raise ThreadError(
                "No accepted Harness UI configuration is selected.",
                code="configuration_not_accepted",
            )
        return source

    async def _load_run_state(
        self, thread: Thread
    ) -> tuple[
        HarnessState, DeferredToolRequests | None, ObjectRef | None, StoredDeferredInput | None, dict[str, str | None]
    ]:
        accepted = None
        positions: dict[str, str | None] = {}
        if thread.continuation is None:
            stored = await self._store.objects.read_model(thread.initial_state, StoredThreadInitialState)
            state = stored.harness_state
            deferred = None
            composition = None
        else:
            stored_continuation = await self._store.read_continuation(thread.thread_id, thread.continuation)
            state = stored_continuation.harness_state
            deferred = stored_continuation.deferred_requests
            composition = stored_continuation.run_composition
            accepted = stored_continuation.accepted_input
            positions = stored_continuation.memory_cursors
        if state.thread_id != thread.thread_id:
            raise ThreadError(
                "The selected Thread state belongs to another Thread.",
                code="thread_continuation_incompatible",
            )
        return state, deferred, composition, accepted, positions

    async def _select_state(
        self,
        *,
        thread: Thread,
        composition: ObjectRef,
        state: HarnessState | None,
        display: DisplayHistory | None = None,
        deferred: DeferredToolRequests | None = None,
        accepted: DeferredToolResume | None = None,
        excerpt: ConversationExcerpt,
        activity_changed: bool,
        completed_run_id: str | None = None,
        superseded: ObjectRef | None = None,
        memory_positions: Mapping[str, str | None] | None = None,
    ) -> RootContinuationSelection:
        """Select native state with a display already frozen on the producer's event loop."""
        if state is None:
            return RootContinuationSelection(status="not_available")
        published_ref: ObjectRef | None = None

        def prepare_continuation() -> tuple[StoredContinuation, ThreadReadModel]:
            continuation = StoredContinuation(
                harness_release=harness_version,
                run_composition=composition,
                memory_cursors=dict(memory_positions or {}),
                harness_state=with_display_history(state, display) if display is not None else state,
                excerpt=excerpt,
                deferred_requests=deferred,
                accepted_input=StoredDeferredInput.capture(accepted, state),
                created_at=datetime.now(UTC),
            )
            return continuation, project_continuation(continuation)

        try:
            # The request checkpoint joins this work before model execution or
            # cancellation; terminal saving starts only after stream teardown.
            # Join serialization before propagating cancellation.
            continuation, read_model = await to_thread.run_sync(prepare_continuation)
            async with checkpoint_lock(self._store.layout.root, thread.thread_id):
                published_ref = (
                    await self._store.objects.publish_model(object_kind=ObjectKind.continuation, value=continuation)
                ).ref
                selected = await self._store.threads.select_continuation(
                    thread_id=thread.thread_id,
                    expected=thread.continuation,
                    replacement=published_ref,
                    read_model=read_model,
                    completed_run_id=completed_run_id,
                    excerpt=excerpt,
                    activity_changed=activity_changed,
                )
                # Only this Run's previous successful checkpoint is replaceable.
                # Previous Runs, completions, and finalized restart handoffs stay immutable.
                if superseded is not None and superseded == thread.continuation and superseded != published_ref:
                    try:
                        await self._store.objects.remove(superseded)
                    except OSError as exc:
                        get_logger(__name__).warning(
                            "Could not remove superseded Run checkpoint",
                            extra={"thread_id": thread.thread_id, "error_type": type(exc).__name__},
                        )
        except Exception as exc:
            return RootContinuationSelection(status="failed", reference=published_ref, error=exc)
        await self._store.publish_work(thread.thread_id, published_ref, state)
        # A disposable inspection index cannot change a successfully selected
        # execution checkpoint. Missing indexes rebuild on the next inspection.
        try:
            inspection = await to_thread.run_sync(build_thread_inspection, selected, continuation)
            await self._store.inspections.publish(thread.thread_id, published_ref.logical_digest, inspection)
        except Exception as exc:
            get_logger("a13n_harness_ui.storage").warning(
                "Could not publish Thread inspection index",
                extra={"thread_id": thread.thread_id, "error_type": type(exc).__name__},
            )
        return RootContinuationSelection(status="selected", reference=published_ref)

    async def _publish_live(
        self,
        *,
        thread_id: str,
        run_id: str,
        display: DisplayPublication,
        base_continuation_id: str | None,
    ) -> None:
        if self._live_hub is None:
            return
        await self._live_hub.publish(
            run_kind="root",
            root_thread_id=thread_id,
            parent_thread_id=None,
            thread_id=thread_id,
            run_id=run_id,
            events=(),
            display=display,
            base_continuation_id=base_continuation_id,
        )


class _ToolPresentationCapability(AbstractCapability[AgentContext]):
    """Join Host asset retention before the next native execution checkpoint."""

    def __init__(self, retain: Callable[[AgentStreamEvent], Awaitable[None]]) -> None:
        self._retain = retain

    async def on_event(self, ctx: RunContext[AgentContext], *, event: AgentStreamEvent) -> None:
        await self._retain(event)


def _deferred_resume(
    *,
    thread: Thread,
    requests: DeferredToolRequests | None,
    response: ThreadDeferredResponse | None,
) -> DeferredToolResume | None:
    if response is None:
        return None
    if thread.continuation is None or requests is None:
        raise RunCoordinationError(
            "The selected Thread continuation has no deferred tool requests.",
            code="thread_deferred_not_pending",
        )
    if thread.continuation.logical_digest != response.expected_continuation_id:
        raise RunCoordinationError(
            "The selected Thread continuation changed before deferred response admission.",
            code="thread_continuation_conflict",
        )
    approval_ids = {request.tool_call_id for request in requests.approvals}
    external_ids = {request.tool_call_id for request in requests.calls}
    expected_ids = approval_ids | external_ids
    supplied_ids = {item.request_id for item in response.responses}
    if not supplied_ids <= expected_ids:
        raise RunCoordinationError(
            "The deferred response contains an unknown request.",
            code="thread_deferred_response_incomplete",
        )

    no_response = "The user continued without responding. No approval or result was supplied."
    approvals: dict[str, bool | DeferredToolApprovalResult] = {
        request_id: ToolDenied(no_response) for request_id in approval_ids
    }
    calls: dict[str, object] = {request_id: ToolFailed(no_response) for request_id in external_ids}
    for item in response.responses:
        if isinstance(item, ApprovalDecision):
            if item.request_id not in approval_ids:
                raise RunCoordinationError(
                    "A deferred response kind does not match its selected request.",
                    code="thread_deferred_response_kind_mismatch",
                )
            approvals[item.request_id] = (
                ToolApproved(override_args=item.override_arguments)
                if item.approved
                else ToolDenied(item.denial_message or "The tool call was denied.")
            )
        elif isinstance(item, ExternalToolResult):
            if item.request_id not in external_ids:
                raise RunCoordinationError(
                    "A deferred response kind does not match its selected request.",
                    code="thread_deferred_response_kind_mismatch",
                )
            if item.denied:
                calls[item.request_id] = ToolFailed(item.denial_message or "The external tool call was denied.")
                continue
            pending = next(request for request in requests.calls if request.tool_call_id == item.request_id)
            metadata = requests.metadata.get(item.request_id)
            if isinstance(metadata, dict) and metadata.get("kind") == "ask_user_question":
                try:
                    calls[item.request_id] = _validate_question_result(pending.args, item.result)
                except (TypeError, ValueError) as exc:
                    raise RunCoordinationError(
                        "A structured question response is invalid.",
                        code="thread_deferred_response_invalid",
                    ) from exc
            else:
                calls[item.request_id] = item.result
        else:
            raise TypeError("Unsupported deferred response item")
    try:
        results = requests.build_results(calls=calls, approvals=approvals)
        return DeferredToolResume(requests=requests, results=results)
    except (TypeError, ValueError) as exc:
        raise RunCoordinationError(
            "The deferred response cannot be represented by the selected requests.",
            code="thread_deferred_response_invalid",
        ) from exc


def _validate_question_result(arguments: object, value: object) -> dict[str, object]:
    request = AskUserQuestionRequest.model_validate(arguments)
    answers = UserQuestionAnswers.model_validate(value)
    expected = {question.question: question for question in request.questions}
    if not set(answers.answers) <= set(expected):
        raise ValueError("answer contains an unknown question")
    if set(expected) - set(answers.answers) and answers.response is None:
        raise ValueError("answer must cover every question or include a general response")
    for text, answer in answers.answers.items():
        question = expected[text]
        values = (answer,) if isinstance(answer, str) else answer
        labels = {option.label for option in question.options}
        selected = tuple(item for item in values if item in labels)
        if selected and len(selected) != len(values):
            raise ValueError("answer cannot mix option labels and free text")
        if selected and not question.multi_select and len(selected) != 1:
            raise ValueError("single-select question requires exactly one option")
    return answers.model_dump(mode="json", exclude_none=True)


def _selection(thread: Thread) -> ThreadCompositionSelection:
    return ThreadCompositionSelection.from_thread(thread)


__all__ = ["RootContinuationSelection", "RootRunExecutor", "RootRunOutcome"]
