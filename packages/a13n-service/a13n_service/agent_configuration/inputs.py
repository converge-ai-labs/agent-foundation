"""Configuration input admission through the canonical Run acceptance service."""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial

from anyio import to_thread
from pydantic import Field
from pydantic_ai.usage import UsageLimits
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.agents.domain import StrictModel
from a13n_service.agents.invocation_resolution import AgentInvocationResolver
from a13n_service.digests import digest_request
from a13n_service.iam import AuthenticatedActor, AuthorizationError
from a13n_service.interactions.acceptance import RunAcceptanceError, RunAcceptanceReceipt, RunAcceptanceService
from a13n_service.interactions.command_evidence import RunCommandEvidence, fingerprint_request
from a13n_service.interactions.command_preparation import CommandInput, PreparedCommandInput, validate_invocation
from a13n_service.interactions.domain import ExecutionBudget, Run, RunLineageKind, RunUsageLimit, Thread, new_run_id
from a13n_service.interactions.environment_selection import EnvironmentDefault, requested_environment
from a13n_service.interactions.initialization import (
    NewRunPolicy,
    RunStateSeed,
    initialize_completed_continuation_state,
    initialize_empty_thread_state,
    initialize_fork_state,
)
from a13n_service.interactions.input import AgentInput
from a13n_service.interactions.models import RunRecord, ThreadRecord
from a13n_service.interactions.objects import RunStateStore
from a13n_service.interactions.origin import SubmissionOrigin
from a13n_service.storage import short_session, transaction
from a13n_service.temporal import Clock, utc_now

from .authorization import authorize_session
from .context import ConfigurationRunContext
from .definition import AssistantDefinition
from .domain import ConfigurationDraft
from .knowledge import KnowledgeFiles
from .models import ConfigurationDraftRecord
from .persistence import failure, not_found, require_open
from .readiness import ConfigurationReadiness
from .system_agent import SystemConfigurationAgent, assistant_config


class ConfigurationInputRequest(StrictModel):
    expected_thread_version: int = Field(ge=1)
    input: AgentInput


@dataclass(frozen=True, slots=True)
class _Selection:
    thread: Thread
    source: Run | None
    draft: ConfigurationDraft


class ConfigurationInputs:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        invocations: AgentInvocationResolver,
        acceptance: RunAcceptanceService,
        states: RunStateStore,
        inputs: CommandInput,
        readiness: ConfigurationReadiness,
        system: SystemConfigurationAgent,
        definition: AssistantDefinition,
        knowledge: KnowledgeFiles,
        *,
        clock: Clock = utc_now,
        execution_max_attempts: int = 3,
        max_handoffs: int = 2,
        queue_name: str = "default",
        priority: int = 0,
    ) -> None:
        self._sessions, self._invocations, self._acceptance, self._states, self._inputs = (
            sessions,
            invocations,
            acceptance,
            states,
            inputs,
        )
        self._readiness, self._system, self._definition, self._knowledge, self._clock = (
            readiness,
            system,
            definition,
            knowledge,
            clock,
        )
        self._policy = NewRunPolicy(
            priority=priority,
            queue_name=queue_name,
            execution_budget=ExecutionBudget(
                policy_version="1",
                max_attempts=execution_max_attempts,
                max_handoffs=max_handoffs,
                max_usage=RunUsageLimit(
                    model_requests=definition.request_limit,
                    input_tokens=definition.total_tokens_limit,
                    output_tokens=definition.total_tokens_limit,
                ),
            ),
        )

    async def submit(
        self, *, actor: AuthenticatedActor, thread_id: str, request: ConfigurationInputRequest, idempotency_key: str
    ) -> RunAcceptanceReceipt:
        evidence = RunCommandEvidence(
            self._sessions,
            actor=actor,
            operation="configuration.input",
            scope_id=thread_id,
            supplied_key=idempotency_key,
            fingerprint=fingerprint_request(request),
            binding=None,
            clock=self._clock,
        )
        selected = await self._select(actor, thread_id, request, check_version=False)
        replay = await evidence.replay()
        if replay is not None:
            return replay
        selected = await self._select(actor, thread_id, request, check_version=True)
        ready = await self._readiness.read(actor=actor, target_agent_id=selected.draft.target_agent_id)
        if not ready.ready or ready.selected_model is None:
            raise failure(
                "configuration_model_not_ready",
                "Configure an authorized compatible Model before starting the assistant.",
            )
        await to_thread.run_sync(self._knowledge.validate)
        assistant = await self._system.ensure(actor=actor, session_id=selected.thread.session_id)
        context = ConfigurationRunContext(
            session_id=selected.thread.session_id,
            thread_id=thread_id,
            draft_id=selected.draft.id,
            initial_draft_version=selected.draft.version,
            definition_digest=digest_request(self._definition),
            model_selection_reason=ready.selected_model.selection_reason,
        )
        invocation = await self._invocations.preparation.prepare_configuration(
            actor=actor,
            agent_id=assistant.id,
            config=assistant_config(self._definition, ready.selected_model),
            context=context,
        )
        async with transaction(self._sessions) as session:
            frozen = await self._invocations.freezing.freeze_in_transaction(session, prepared=invocation)
        accepted = await self._inputs.accept(
            actor=actor,
            workspace_id=actor.workspace_id,
            submitted=request.input,
            frozen=frozen,
            environment=None,
        )
        prepared = PreparedCommandInput(invocation=invocation, frozen=frozen, input=accepted)
        run_id = new_run_id()
        seed = RunStateSeed.from_invocation(run_id=run_id, invocation=prepared.frozen, input=prepared.input).model_copy(
            update={
                "usage_limits": UsageLimits(
                    request_limit=self._definition.request_limit, total_tokens_limit=self._definition.total_tokens_limit
                ),
            }
        )
        source = selected.source
        is_fork = source is not None and source.thread_id != thread_id
        if source is None:
            state = initialize_empty_thread_state(seed, thread_id=thread_id)
        else:
            parent = (await self._states.read_run(source)).envelope
            state = (
                initialize_fork_state(seed, parent, thread_id=thread_id)
                if is_fork
                else initialize_completed_continuation_state(seed, parent)
            )
        run = self._policy.create(
            now=self._clock(),
            id=run_id,
            organization_id=selected.draft.organization_id,
            authority_principal=actor.principal,
            session_id=selected.thread.session_id,
            thread_id=thread_id,
            parent_run_id=None if source is None else source.id,
            lineage_kind=RunLineageKind.root
            if source is None
            else (RunLineageKind.fork if is_fork else RunLineageKind.continue_),
            invocation=prepared.frozen,
            input=prepared.input,
            request_fingerprint=evidence.fingerprint,
            origin=SubmissionOrigin(),
            configuration_context=context,
        )

        async def validate_final(session: AsyncSession) -> None:
            await authorize_session(session, actor=actor, session_id=selected.thread.session_id, lock=True)
            thread = await session.scalar(select(ThreadRecord).where(ThreadRecord.id == thread_id).with_for_update())
            if thread is None or thread.version != request.expected_thread_version:
                raise failure("thread_version_conflict", "The configuration Thread changed before input acceptance.")
            draft = await session.get(ConfigurationDraftRecord, selected.draft.id)
            if draft is None:
                raise not_found()
            require_open(draft, expected_version=selected.draft.version)
            await validate_invocation(session, self._invocations, prepared=prepared.invocation, frozen=prepared.frozen)

        try:
            return await self._acceptance.advance_thread(
                run=run,
                state=state,
                expected_thread_version=request.expected_thread_version,
                expected_current_run_id=selected.thread.current_run_id,
                expected_head_run_id=selected.thread.head_run_id,
                next_head_run_id=None if source is None or is_fork else source.id,
                final_validator=validate_final,
                transaction_hook=partial(evidence.commit, now=self._clock()),
                environment=requested_environment(None, default=EnvironmentDefault.thread),
            )
        except RunAcceptanceError as error:
            return await evidence.reconcile(error)

    async def _select(
        self, actor: AuthenticatedActor, thread_id: str, request: ConfigurationInputRequest, *, check_version: bool
    ) -> _Selection:
        async with short_session(self._sessions) as session:
            thread = await session.get(ThreadRecord, thread_id)
            if thread is None:
                raise not_found()
            try:
                conversation = await authorize_session(session, actor=actor, session_id=thread.session_id)
            except AuthorizationError as error:
                raise not_found() from error
            if check_version and thread.version != request.expected_thread_version:
                raise failure("thread_version_conflict", "The configuration Thread changed; refresh its state.")
            current = None if thread.current_run_id is None else await session.get(RunRecord, thread.current_run_id)
            if check_version and current is not None and current.status in {"accepted", "running", "waiting"}:
                raise failure(
                    "thread_busy",
                    "Complete the active work or resolve its pending question before submitting new configuration input.",
                )
            source = None if thread.head_run_id is None else await session.get(RunRecord, thread.head_run_id)
            if source is None and current is None and thread.origin_kind == "fork":
                source = await session.get(RunRecord, thread.origin_run_id)
                if source is None or source.session_id != thread.session_id:
                    raise failure("run_not_forkable", "The configuration Thread's fork source is unavailable.")
            if source is not None and source.status != "completed" and check_version:
                raise failure("run_not_continuable", "Resolve the pending Run through waiting feedback or Continue.")
            draft = await session.get(ConfigurationDraftRecord, conversation.configuration_draft_id)
            if draft is None:
                raise not_found()
            if check_version:
                require_open(draft, expected_version=draft.version)
            return _Selection(
                thread.to_resource(), None if source is None else source.to_resource(), draft.to_resource()
            )
