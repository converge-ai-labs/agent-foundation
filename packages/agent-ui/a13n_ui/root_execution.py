"""One fresh root Harness execution and continuation publication."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
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
from a13n_harness.pricing import get_current_pricing_catalog
from a13n_stream_protocol import HarnessAguiObserver
from anyio import CancelScope, to_thread
from pydantic_ai import ToolDenied
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.tools import DeferredToolApprovalResult, DeferredToolRequests, ToolApproved

from a13n_ui.capability_runtime import production_run_capabilities
from a13n_ui.composition import (
    AgentReconstructor,
    CompositionAcceptanceService,
    ResolvedRunComposition,
    RunCompositionService,
    ThreadCompositionSelection,
)
from a13n_ui.configuration import LoadedAgentUiConfiguration
from a13n_ui.environment_runtime import EnvironmentFinalization, EnvironmentRunService
from a13n_ui.errors import RunCoordinationError, ThreadError
from a13n_ui.live import AgentUiLiveHub
from a13n_ui.model_runtime import SubscriptionSource
from a13n_ui.storage import (
    LocalStore,
    ObjectKind,
    ObjectRef,
    StoredContinuation,
    StoredThreadInitialState,
    Thread,
    ThreadConfigurationMutation,
)
from a13n_ui.subagent_operator import AgentUiSubagentOperator
from a13n_ui.surfaces import ApprovalDecision, ExternalToolResult, ThreadDeferredResponse
from a13n_ui.thread_service import ThreadService


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
        live_hub: AgentUiLiveHub | None = None,
        cleanup_timeout_seconds: float = 30.0,
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
        self._cleanup_timeout_seconds = cleanup_timeout_seconds
        self._root_capability_factory: Callable[[str], AbstractCapability[AgentContext]] | None = None

    def set_root_capability_factory(
        self,
        factory: Callable[[str], AbstractCapability[AgentContext]],
    ) -> None:
        if self._root_capability_factory is not None:
            raise RuntimeError("Root Thread Capability factory is already configured")
        self._root_capability_factory = factory

    async def execute(
        self,
        *,
        thread_id: str,
        prompt: str | None = None,
        response: ThreadDeferredResponse | None = None,
        mutation: ThreadConfigurationMutation | None = None,
        on_stream: Callable[[HarnessRunStream[Any]], Awaitable[None]] | None = None,
    ) -> RootRunOutcome:
        if (prompt is None) == (response is None):
            raise RunCoordinationError(
                "A root operation requires exactly one prompt or deferred response.",
                code="run_input_invalid",
            )
        if prompt is not None and not prompt.strip():
            raise RunCoordinationError("A root message must not be blank.", code="run_input_invalid")
        thread = await self._threads.get(thread_id)
        if thread.parent_thread_id is not None:
            raise ThreadError("run_thread accepts only root Threads.", code="child_thread_scoped")
        if thread.archived:
            raise ThreadError("An archived Thread cannot run.", code="thread_archived")
        if mutation is not None:
            thread = await self._threads.update_configuration(thread_id=thread_id, mutation=mutation)
        source = await self._required_configuration()
        published = await self._compositions.publish(source, _selection(thread))
        previous_state, deferred = await self._load_run_state(thread)
        deferred_resume = _deferred_resume(thread=thread, requests=deferred, response=response)
        if prompt is not None and deferred is not None:
            raise RunCoordinationError(
                "The selected Thread continuation has unresolved deferred tool requests.",
                code="thread_deferred_pending",
            )
        pricing_catalog = await to_thread.run_sync(get_current_pricing_catalog)
        reconstructed = self._agents.reconstruct(
            published.value,
            pricing_catalog=pricing_catalog,
            subagent_operator=self._subagent_operator,
            root_capabilities=(
                () if self._root_capability_factory is None else (self._root_capability_factory(thread.thread_id),)
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
                deferred_resume=deferred_resume,
            )
            if on_stream is not None:
                await on_stream(stream)
            observer = HarnessAguiObserver()
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

    async def _load_run_state(self, thread: Thread) -> tuple[HarnessState, DeferredToolRequests | None]:
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
            root_thread_id=thread_id,
            parent_thread_id=None,
            thread_id=thread_id,
            run_id=run_id,
            events=events,
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
                calls[item.request_id] = ToolDenied(item.denial_message or "The external tool call was denied.")
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


__all__ = ["RootContinuationSelection", "RootRunExecutor", "RootRunOutcome"]
