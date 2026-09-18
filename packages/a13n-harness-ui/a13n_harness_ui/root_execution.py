"""One fresh root Harness execution and continuation publication."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from typing import Any, Literal
from uuid import uuid4

from a13n_harness import (
    AgentIdentityRef,
    AgentInstanceContext,
    DeferredToolResume,
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
from a13n_logging import get_logger
from a13n_stream_protocol import HarnessAguiObserver
from anyio import CancelScope, to_thread
from opentelemetry.trace import StatusCode
from pydantic_ai import ToolDenied, ToolFailed
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.tools import DeferredToolApprovalResult, DeferredToolRequests, ToolApproved

from a13n_harness_ui.capability_runtime import production_run_bindings
from a13n_harness_ui.composition import (
    AgentReconstructor,
    CompositionAcceptanceService,
    ResolvedRunComposition,
    RunCompositionService,
    ThreadCompositionSelection,
)
from a13n_harness_ui.configuration import LoadedHarnessUiConfiguration
from a13n_harness_ui.conversation import ConversationExcerpt, ExcerptCollector, checkpoint_excerpt
from a13n_harness_ui.diagnostics import exception_feedback
from a13n_harness_ui.display_history import DisplayHistoryCollector, saved_display_history, with_display_history
from a13n_harness_ui.environment_runtime import EnvironmentFinalization, EnvironmentRunService
from a13n_harness_ui.errors import RunCoordinationError, ThreadError
from a13n_harness_ui.live import HarnessUiLiveHub, HarnessUiSummaryHub
from a13n_harness_ui.model_runtime import SubscriptionSource
from a13n_harness_ui.observation import (
    phase,
    record_configuration,
    record_output,
    record_phase_result,
    record_skill_event,
)
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
from a13n_harness_ui.storage.read_models import project_continuation
from a13n_harness_ui.subagent_operator import HarnessUiSubagentOperator
from a13n_harness_ui.surfaces import ApprovalDecision, ExternalToolResult, RunModelOverrides, ThreadDeferredResponse
from a13n_harness_ui.thread_files import ThreadFiles
from a13n_harness_ui.thread_projection import build_thread_inspection
from a13n_harness_ui.thread_service import ThreadService
from a13n_harness_ui.thread_work import ThreadWorkService
from a13n_harness_ui.tool_evidence import ToolEvidenceCollector


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
    ) -> None:
        self._store = store
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

    async def execute(
        self,
        *,
        thread_id: str,
        prompt: RunInputValue | None = None,
        response: ThreadDeferredResponse | None = None,
        mutation: ThreadConfigurationMutation | None = None,
        model_overrides: RunModelOverrides | None = None,
        environment_profile_id: str | None = None,
        on_stream: Callable[[HarnessRunStream[Any], RootInputFiles | None], Awaitable[None]] | None = None,
        on_composition: Callable[[ObjectRef], Awaitable[None]] | None = None,
    ) -> RootRunOutcome:
        with phase("prepare") as preparation_span:
            record_span_metadata(
                preparation_span,
                {
                    "prepare.input_kind": "deferred_response" if response is not None else "prompt",
                    "prepare.configuration_mutation": mutation is not None,
                },
            )
            if (prompt is None) == (response is None):
                raise RunCoordinationError(
                    "A root operation requires exactly one prompt or deferred response.",
                    code="run_input_invalid",
                )
            if prompt is not None:
                prompt = detach_input(prompt)
            preparation_span.set_attribute("a13n.phase.step", "thread")
            thread = await self._threads.get(thread_id)
            if thread.parent_thread_id is not None:
                raise ThreadError("run_thread accepts only root Threads.", code="child_thread_scoped")
            if thread.archived:
                raise ThreadError("An archived Thread cannot run.", code="thread_archived")
            if mutation is not None:
                thread = await self._threads.update_configuration(thread_id=thread_id, mutation=mutation)
            preparation_span.set_attribute("a13n.phase.step", "continuation")
            previous_state, deferred, previous_composition = await self._load_run_state(thread)
            display = DisplayHistoryCollector(previous_state.message_history, saved_display_history(previous_state))
            deferred_resume = _deferred_resume(thread=thread, requests=deferred, response=response)
            if prompt is not None and deferred is not None:
                raise RunCoordinationError(
                    "The selected Thread continuation has unresolved deferred tool requests.",
                    code="thread_deferred_pending",
                )
            # Answering a deferred request (including an automatic timeout) must
            # not silently drop a Run-only Sandbox selection. An explicit
            # Environment mutation still has the ordinary next-admission effect.
            if (
                response is not None
                and environment_profile_id is None
                and not (mutation and "environment_profile_id" in mutation.patch.model_fields_set)
            ):
                assert previous_composition is not None
                captured = await self._store.objects.read_model(previous_composition, ResolvedRunComposition)
                environment_profile_id = captured.environment_profile.profile_id
            preparation_span.set_attribute("a13n.phase.step", "configuration")
            source = await self._required_configuration()
            published = await self._compositions.publish(
                source, _selection(thread, environment_profile_id), model_overrides=model_overrides
            )
            if on_composition is not None:
                await on_composition(published.reference)
            base_continuation_id = thread.continuation.logical_digest if thread.continuation is not None else None

            async def save_checkpoint(state: HarnessState) -> str:
                nonlocal thread
                excerpt = checkpoint_excerpt(thread.excerpt, state.message_history)
                # RootCheckpointCapability joins this operation and its marker
                # before propagating either native or AnyIO cancellation.
                selected = await self._select_state(
                    thread=thread,
                    composition=published.reference,
                    state=state,
                    display=display,
                    excerpt=excerpt,
                    activity_changed=excerpt != thread.excerpt,
                )
                if selected.status != "selected" or selected.reference is None:
                    assert selected.error is not None
                    raise selected.error
                thread = thread.model_copy(update={"continuation": selected.reference, "excerpt": excerpt})
                if self._summary_hub is not None:
                    await self._summary_hub.publish(kind="thread", thread_id=thread_id)
                return selected.reference.logical_digest

            preparation_span.set_attribute("a13n.phase.step", "reconstruction")
            pricing_catalog = await to_thread.run_sync(get_current_pricing_catalog)
            reconstructed = self._agents.reconstruct(
                published.value,
                pricing_catalog=pricing_catalog,
                subagent_operator=self._subagent_operator,
                root_capabilities=(
                    display,
                    RootCheckpointCapability(save_checkpoint),
                    *(
                        ()
                        if self._root_capability_factory is None
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
                if self._thread_files is not None
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
            environment = await self._environments.prepare(published.value)
            instance = AgentInstanceContext(
                identity=AgentIdentityRef(issuer="a13n-harness-ui", subject=thread.thread_id),
                agent_instance_id=f"agent-{uuid4().hex[:20]}",
                actor="a13n-harness-ui.root",
                host_refs={"thread_id": thread.thread_id},
            )
            bindings = RunBindings(
                instance=instance,
                environment=environment.runtime,
                tool_result_directory=environment.tool_result_directory,
                model_resolver=reconstructed.model_resolver,
                working_state_observer=(
                    partial(self._work.observe, thread_id, base_continuation_id=base_continuation_id)
                    if self._work is not None
                    else None
                ),
            )
            bindings = production_run_bindings(bindings, reconstructed.definition_capability_ids)
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
        run_error: BaseException | None = None
        excerpts: ExcerptCollector | None = None
        try:
            stream = reconstructed.executable.stream(
                prompt,
                input_factory=input_factory,
                bindings=bindings,
                previous_state=previous_state,
                deferred_resume=deferred_resume,
            )
            excerpts = ExcerptCollector(thread.excerpt, run_id=stream.run_id)
            tool_evidence = ToolEvidenceCollector(run_id=stream.run_id)
            if on_stream is not None:
                await on_stream(stream, input_files)
            observer = HarnessAguiObserver()
            async with self._bind_subagent_parent(
                thread_id=thread.thread_id,
                run_id=stream.run_id,
                instance=instance,
                composition=published.value,
            ):
                async with stream:
                    async for item in stream:
                        record_skill_event(item)
                        excerpts.observe(item)
                        tool_evidence.observe(item)
                        await self._store.usage.observe(thread_id=thread.thread_id, item=item)
                        try:
                            await self._publish_live(
                                thread_id=thread.thread_id,
                                run_id=stream.run_id,
                                events=observer.observe(item),
                                observer=observer,
                                base_continuation_id=base_continuation_id,
                            )
                        except Exception:
                            pass
                        if isinstance(item, HarnessRunResultEvent):
                            result = item.result
        except BaseException as exc:
            run_error = exc
            if result is None and stream is not None:
                # A suspended candidate includes deferred requests as well as state.
                # Preserve the whole envelope even when cleanup/cancellation prevents delivery.
                result = stream.outcome

        if result is not None:
            record_output(result.output, status=result.status)
        finalization: EnvironmentFinalization | None = None
        finalization_error: Exception | None = None
        continuation = RootContinuationSelection(status="not_available")
        with phase("finalize") as finalization_span:
            with CancelScope(shield=True):
                finalization_span.set_attribute("a13n.phase.step", "environment")
                try:
                    finalization = await environment.finalize(timeout_seconds=self._cleanup_timeout_seconds)
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
                if result is not None:
                    continuation = await self._select_state(
                        thread=thread,
                        composition=published.reference,
                        state=result.state,
                        display=display,
                        deferred=result.deferred,
                        completed_run_id=(
                            stream.run_id
                            if stream is not None and result.status == "completed" and run_error is None
                            else None
                        ),
                        excerpt=thread.excerpt if excerpts is None else excerpts.finish(result),
                        activity_changed=excerpts is not None and excerpts.changed,
                    )
                elif stream is not None:
                    try:
                        state = await stream.export_state()
                    except Exception as exc:
                        continuation = RootContinuationSelection(status="failed", error=exc)
                    else:
                        continuation = await self._select_state(
                            thread=thread,
                            composition=published.reference,
                            state=state,
                            display=display,
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
    ) -> tuple[HarnessState, DeferredToolRequests | None, ObjectRef | None]:
        if thread.continuation is None:
            stored = await self._store.objects.read_model(thread.initial_state, StoredThreadInitialState)
            state = stored.harness_state
            deferred = None
            composition = None
        else:
            stored_continuation = await self._store.objects.read_model(thread.continuation, StoredContinuation)
            state = stored_continuation.harness_state
            deferred = stored_continuation.deferred_requests
            composition = stored_continuation.run_composition
        if state.thread_id != thread.thread_id:
            raise ThreadError(
                "The selected Thread state belongs to another Thread.",
                code="thread_continuation_incompatible",
            )
        return state, deferred, composition

    async def _select_state(
        self,
        *,
        thread: Thread,
        composition: ObjectRef,
        state: HarnessState | None,
        display: DisplayHistoryCollector | None = None,
        deferred: DeferredToolRequests | None = None,
        excerpt: ConversationExcerpt,
        activity_changed: bool,
        completed_run_id: str | None = None,
    ) -> RootContinuationSelection:
        if state is None:
            return RootContinuationSelection(status="not_available")
        published_ref: ObjectRef | None = None
        try:
            continuation = StoredContinuation(
                harness_release=harness_version,
                run_composition=composition,
                harness_state=with_display_history(
                    state, display.capture(state.message_history, completed=completed_run_id is not None)
                )
                if display is not None
                else state,
                excerpt=excerpt,
                deferred_requests=deferred,
                created_at=datetime.now(UTC),
            )
            read_model = await to_thread.run_sync(project_continuation, continuation)
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
        except Exception as exc:
            return RootContinuationSelection(status="failed", reference=published_ref, error=exc)
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
        events: tuple[Any, ...],
        observer: HarnessAguiObserver,
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
            events=events,
            observer=observer,
            base_continuation_id=base_continuation_id,
        )


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
    if supplied_ids != expected_ids:
        raise RunCoordinationError(
            "The deferred response must answer the complete selected request set.",
            code="thread_deferred_response_incomplete",
        )

    approvals: dict[str, bool | DeferredToolApprovalResult] = {}
    calls: dict[str, object] = {}
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


def _selection(thread: Thread, environment_profile_id: str | None = None) -> ThreadCompositionSelection:
    source = thread.configuration.agent_source
    return ThreadCompositionSelection(
        thread_id=thread.thread_id,
        version=thread.configuration.version,
        project_id=thread.configuration.project_id,
        agent_source_kind=source.kind,
        agent_source_id=source.id,
        default_model_id=thread.configuration.default_model_id,
        environment_profile_id=(
            thread.configuration.environment_profile_id if environment_profile_id is None else environment_profile_id
        ),
        harness_plugin_ids=thread.configuration.harness_plugin_ids,
        environment_run_extension_ids=thread.configuration.environment_run_extension_ids,
        mcp_server_ids=thread.configuration.mcp_server_ids,
    )


__all__ = ["RootContinuationSelection", "RootRunExecutor", "RootRunOutcome"]
