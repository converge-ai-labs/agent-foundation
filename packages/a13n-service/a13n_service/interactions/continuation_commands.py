"""Continue or retry the exact accepted execution of an existing Run."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from functools import partial

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.application_errors import ErrorCategory
from a13n_service.assets import Asset
from a13n_service.digests import digest_request
from a13n_service.iam import (
    AuthenticatedActor,
    AuthorizationError,
    WorkspaceAction,
    authorize_agent,
    authorize_persisted_agent_principal_actions,
)
from a13n_service.interactions.acceptance import RunAcceptanceError, RunAcceptanceReceipt, RunAcceptanceService
from a13n_service.interactions.control_domain import (
    WaitingRunContinueInput,
    WaitingRunFeedback,
    WaitingRunFeedbackRequest,
    normalize_feedback,
    normalize_waiting_continue,
)
from a13n_service.interactions.domain import (
    Run,
    RunInputKind,
    RunLineageKind,
    RunStatus,
    Thread,
    accepted_run,
    new_run_id,
)
from a13n_service.interactions.environment_selection import (
    RetainedRunEnvironment,
)
from a13n_service.interactions.inheritance import inherited_run_fields
from a13n_service.interactions.initialization import (
    RunStateSeed,
    initialize_retry_state,
    initialize_waiting_continuation_state,
)
from a13n_service.interactions.models import RunRecord, SessionRecord, ThreadRecord
from a13n_service.interactions.objects import RunPayloadStore, RunStateStore, StoredRunState
from a13n_service.interactions.origin import SubmissionOrigin
from a13n_service.interactions.protocol_context import ProtocolInputContext
from a13n_service.interactions.state import RunPayloadEnvelope
from a13n_service.storage import short_session
from a13n_service.temporal import Clock, utc_now

from .command_evidence import (
    RunCommandEvidence,
    fingerprint_request,
    require_idempotency_key,
)
from .command_preparation import CommandInput
from .command_values import (
    RetryRunCommand,
    WaitingContinueRunCommand,
)
from .errors import InteractionCommandError, command_not_found
from .input import input_text

_USER_INPUT_ORIGIN = SubmissionOrigin()


class ContinuationCommands:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        acceptance: RunAcceptanceService,
        states: RunStateStore,
        payloads: RunPayloadStore,
        inputs: CommandInput,
        *,
        clock: Clock = utc_now,
    ) -> None:
        self._sessions = sessions
        self._acceptance = acceptance
        self._states = states
        self._payloads = payloads
        self._inputs = inputs
        self._clock = clock

    async def retry(
        self,
        *,
        actor: AuthenticatedActor,
        run_id: str,
        idempotency_key: str,
        request: RetryRunCommand,
    ) -> RunAcceptanceReceipt:
        require_idempotency_key(idempotency_key)
        evidence = RunCommandEvidence(
            self._sessions,
            actor=actor,
            operation="run.retry",
            scope_id=run_id,
            supplied_key=idempotency_key,
            fingerprint=fingerprint_request(request),
            clock=self._clock,
        )
        replay = await evidence.replay()
        if replay is not None:
            return replay

        source, thread, parent = await self._load_retry_source(actor=actor, run_id=run_id)
        source_state = await self._states.read_run(source)
        parent_state = (await self._states.read_run(parent)).envelope if parent is not None else None
        new_run_id_value = new_run_id()
        copied_input_object = None
        if source.input_object is not None:
            source_payload = await self._payloads.verify_reference(
                source.organization_id,
                source.id,
                "input",
                source.input_object,
            )
            copied_input_object = await self._payloads.create(
                source.organization_id,
                RunPayloadEnvelope(
                    run_id=new_run_id_value,
                    payload_kind="input",
                    payload_schema_version=source_payload.payload_schema_version,
                    payload=source_payload.payload,
                ),
            )
        state = initialize_retry_state(
            RunStateSeed(
                run_id=new_run_id_value,
                agent_id=source.agent_id,
                agent_revision_id=source.agent_revision_id,
                effective_agent_config=source_state.envelope.effective_agent_config,
                prepared_plugins=source_state.envelope.prepared_plugins,
                usage_limits=source_state.envelope.usage_limits,
                protocol_context=source_state.envelope.protocol_context,
                secret_bindings=source_state.envelope.secret_bindings,
            ),
            thread_id=source.thread_id,
            source_lineage_kind=source.lineage_kind,
            source_input_kind=source.input_kind,
            parent=parent_state,
        )
        now = self._clock()
        retry_run = accepted_run(
            now=now,
            id=new_run_id_value,
            retry_of_run_id=source.id,
            idempotency_key=None,
            request_fingerprint=evidence.fingerprint,
            organization_id=source.organization_id,
            **inherited_run_fields(source),
            session_id=source.session_id,
            thread_id=source.thread_id,
            parent_run_id=source.parent_run_id,
            lineage_kind=source.lineage_kind,
            trigger_type=source.trigger_type,
            trigger_entity_type=source.trigger_entity_type,
            trigger_entity_id=source.trigger_entity_id,
            parent_agent_instance_id=source.parent_agent_instance_id,
            delegation_id=source.delegation_id,
            parent_tool_call_id=source.parent_tool_call_id,
            environment_id=source.environment_id,
            environment_access=source.environment_access,
            priority=source.priority,
            queue_name=source.queue_name,
            execution_budget=source.execution_budget,
            input_kind=source.input_kind,
            input_text=source.input_text,
            input=source.input if source.input_object is None else None,
            input_object=copied_input_object,
        )

        async def validate_final(database: AsyncSession) -> None:
            try:
                await authorize_agent(
                    database,
                    actor=actor,
                    workspace_id=actor.workspace_id,
                    agent_id=source.agent_id,
                    action=WorkspaceAction.run_retry,
                )
                await authorize_persisted_agent_principal_actions(
                    database,
                    principal=source.authority_principal,
                    organization_id=source.organization_id,
                    workspace_id=actor.workspace_id,
                    agent_id=source.agent_id,
                    actions=frozenset({WorkspaceAction.agent_invoke}),
                )
            except AuthorizationError as error:
                raise command_not_found() from error

        try:
            return await self._acceptance.advance_thread(
                environment=RetainedRunEnvironment(source.id, source.thread_id),
                run=retry_run,
                state=state,
                expected_thread_version=request.expected_thread_version,
                expected_current_run_id=source.id,
                expected_head_run_id=thread.head_run_id,
                next_head_run_id=thread.head_run_id,
                hook_subscription=request.hook_subscription,
                hook_source_run_id=source.id if "hook_subscription" not in request.model_fields_set else None,
                hook_actor=actor.principal,
                final_validator=validate_final,
                transaction_hook=partial(evidence.commit, now=self._clock()),
            )
        except RunAcceptanceError as error:
            return await evidence.reconcile(error)

    async def feedback(
        self,
        *,
        actor: AuthenticatedActor,
        run_id: str,
        idempotency_key: str,
        request: WaitingRunFeedbackRequest,
        transaction_hook: Callable[[AsyncSession, RunAcceptanceReceipt], Awaitable[None]] | None = None,
        protocol_context: ProtocolInputContext | None = None,
    ) -> RunAcceptanceReceipt:
        require_idempotency_key(idempotency_key)
        source, thread = await self._load_feedback_source(actor=actor, run_id=run_id)
        source_state = await self._states.read_run(source)
        if source.sealed_state is None or source.pending is None:
            raise InteractionCommandError(
                "run_waiting_state_invalid",
                "The selected waiting Run has no complete pending state.",
                category=ErrorCategory.conflict,
            )
        try:
            normalized = normalize_feedback(
                waiting_run_id=source.id,
                sealed_state_digest_sha256=request.sealed_state_digest_sha256,
                pending=source.pending,
                submitted=request.resolutions,
            )
        except ValueError as error:
            raise InteractionCommandError(
                "run_feedback_invalid",
                str(error),
                category=ErrorCategory.invalid_request,
            ) from error
        request_fingerprint = digest_request(
            {
                "expected_thread_version": request.expected_thread_version,
                "feedback": normalized.model_dump(mode="json", by_alias=True),
                **(
                    {"protocol_context": protocol_context.model_dump(mode="json")}
                    if protocol_context is not None
                    else {}
                ),
                **request.model_dump(mode="json", include={"hook_subscription"}),
            }
        )
        evidence = RunCommandEvidence(
            self._sessions,
            actor=actor,
            operation="run.feedback",
            scope_id=run_id,
            supplied_key=idempotency_key,
            fingerprint=request_fingerprint,
            binding=transaction_hook,
            clock=self._clock,
        )
        replay = await evidence.replay()
        if replay is not None:
            return replay
        try:
            return await self._accept_waiting_successor(
                actor=actor,
                source=source,
                thread=thread,
                source_state=source_state,
                request=request,
                normalized=normalized,
                request_fingerprint=evidence.fingerprint,
                transaction_hook=partial(evidence.commit, now=self._clock()),
                protocol_context=protocol_context,
            )
        except RunAcceptanceError as error:
            return await evidence.reconcile(error)

    async def continue_waiting(
        self,
        *,
        actor: AuthenticatedActor,
        run_id: str,
        idempotency_key: str,
        request: WaitingContinueRunCommand,
        transaction_hook: Callable[[AsyncSession, RunAcceptanceReceipt], Awaitable[None]] | None = None,
        prepared_assets: Mapping[str, Asset] | None = None,
    ) -> RunAcceptanceReceipt:
        require_idempotency_key(idempotency_key)
        source, thread, source_state, normalized = await self._prepare_waiting_continue(
            actor=actor, run_id=run_id, request=request, prepared_assets=prepared_assets
        )
        request_fingerprint = digest_request(
            {
                "expected_thread_version": request.expected_thread_version,
                "waiting_continue": normalized.model_dump(mode="json", by_alias=True),
                **request.model_dump(mode="json", include={"hook_subscription"}),
            }
        )
        evidence = RunCommandEvidence(
            self._sessions,
            actor=actor,
            operation="run.waiting_continue",
            scope_id=run_id,
            supplied_key=idempotency_key,
            fingerprint=request_fingerprint,
            binding=transaction_hook,
            clock=self._clock,
        )
        replay = await evidence.replay()
        if replay is not None:
            return replay
        try:
            return await self._accept_waiting_successor(
                actor=actor,
                source=source,
                thread=thread,
                source_state=source_state,
                request=request,
                normalized=normalized,
                request_fingerprint=evidence.fingerprint,
                transaction_hook=partial(evidence.commit, now=self._clock()),
                protocol_context=request.protocol_context,
            )
        except RunAcceptanceError as error:
            return await evidence.reconcile(error)

    async def accept_waiting_continue(
        self,
        *,
        actor: AuthenticatedActor,
        run_id: str,
        request_fingerprint: str,
        request: WaitingContinueRunCommand,
        transaction_hook: Callable[[AsyncSession, RunAcceptanceReceipt], Awaitable[None]] | None = None,
        prepared_assets: Mapping[str, Asset] | None = None,
    ) -> RunAcceptanceReceipt:
        """Accept waiting intent under the calling entry point's command identity."""
        source, thread, source_state, normalized = await self._prepare_waiting_continue(
            actor=actor, run_id=run_id, request=request, prepared_assets=prepared_assets
        )
        return await self._accept_waiting_successor(
            actor=actor,
            source=source,
            thread=thread,
            source_state=source_state,
            request=request,
            normalized=normalized,
            request_fingerprint=request_fingerprint,
            transaction_hook=transaction_hook,
            protocol_context=request.protocol_context,
        )

    async def _prepare_waiting_continue(
        self,
        *,
        actor: AuthenticatedActor,
        run_id: str,
        request: WaitingContinueRunCommand,
        prepared_assets: Mapping[str, Asset] | None,
    ) -> tuple[Run, Thread, StoredRunState, WaitingRunContinueInput]:
        source, thread = await self._load_feedback_source(
            actor=actor,
            run_id=run_id,
            actions=frozenset({WorkspaceAction.run_continue, WorkspaceAction.run_feedback}),
        )
        source_state = await self._states.read_run(source)
        if source.sealed_state is None or source.pending is None:
            raise InteractionCommandError(
                "run_waiting_state_invalid",
                "The selected waiting Run has no complete pending state.",
                category=ErrorCategory.conflict,
            )
        accepted_input = await self._inputs.accept_effective(
            actor=actor,
            workspace_id=actor.workspace_id,
            submitted=request.input,
            effective=source_state.envelope.effective_agent_config,
            environment_access=source.environment_access,
            prepared_assets=prepared_assets,
        )
        normalized = normalize_waiting_continue(
            waiting_run_id=source.id,
            sealed_state_digest_sha256=request.sealed_state_digest_sha256,
            pending=source.pending,
            input=accepted_input,
        )
        return source, thread, source_state, normalized

    async def _accept_waiting_successor(
        self,
        *,
        actor: AuthenticatedActor,
        source: Run,
        thread: Thread,
        source_state: StoredRunState,
        request: WaitingRunFeedbackRequest | WaitingContinueRunCommand,
        normalized: WaitingRunFeedback,
        request_fingerprint: str,
        transaction_hook: Callable[[AsyncSession, RunAcceptanceReceipt], Awaitable[None]] | None,
        protocol_context: ProtocolInputContext | None,
    ) -> RunAcceptanceReceipt:
        """Preserve the waiting execution while accepting its normalized resolution."""
        accepted_input = normalized.input if isinstance(normalized, WaitingRunContinueInput) else None
        actions = (
            (WorkspaceAction.run_continue, WorkspaceAction.run_feedback)
            if accepted_input is not None
            else (WorkspaceAction.run_feedback,)
        )
        if thread.current_run_id != source.id or thread.head_run_id != source.id:
            raise InteractionCommandError(
                "run_not_waiting_continue_eligible" if accepted_input is not None else "run_not_feedback_eligible",
                "The selected waiting Run is no longer the Thread's current head.",
                category=ErrorCategory.conflict,
            )
        assert source.sealed_state is not None
        if request.sealed_state_digest_sha256 != source.sealed_state.digest_sha256:
            raise InteractionCommandError(
                "run_waiting_state_conflict",
                "The waiting Run state changed before continuation acceptance."
                if accepted_input is not None
                else "The waiting Run state changed before feedback acceptance.",
                category=ErrorCategory.conflict,
            )

        successor_id = new_run_id()
        state = initialize_waiting_continuation_state(
            RunStateSeed(
                run_id=successor_id,
                agent_id=source.agent_id,
                agent_revision_id=source.agent_revision_id,
                effective_agent_config=source_state.envelope.effective_agent_config,
                prepared_plugins=source_state.envelope.prepared_plugins,
                secret_bindings=accepted_input.secret_bindings
                if accepted_input is not None
                else source_state.envelope.secret_bindings,
                protocol_context=protocol_context
                if protocol_context is not None
                else source_state.envelope.protocol_context,
            ),
            source_state.envelope,
        )
        successor = accepted_run(
            now=self._clock(),
            id=successor_id,
            organization_id=source.organization_id,
            **inherited_run_fields(source),
            session_id=source.session_id,
            thread_id=source.thread_id,
            parent_run_id=source.id,
            retry_of_run_id=None,
            lineage_kind=RunLineageKind.continue_,
            trigger_type="user_input" if accepted_input is not None else "feedback",
            priority=source.priority,
            queue_name=source.queue_name,
            execution_budget=source.execution_budget,
            idempotency_key=None,
            request_fingerprint=request_fingerprint,
            input_kind=RunInputKind.waiting_continue if accepted_input is not None else RunInputKind.waiting_feedback,
            input=normalized.model_dump(mode="json", by_alias=True),
            input_text=input_text(accepted_input) if accepted_input is not None else None,
        )

        async def validate_final(database: AsyncSession) -> None:
            try:
                for action in actions:
                    await authorize_agent(
                        database,
                        actor=actor,
                        workspace_id=actor.workspace_id,
                        agent_id=source.agent_id,
                        action=action,
                    )
                await authorize_persisted_agent_principal_actions(
                    database,
                    principal=source.authority_principal,
                    organization_id=source.organization_id,
                    workspace_id=actor.workspace_id,
                    agent_id=source.agent_id,
                    actions=frozenset({WorkspaceAction.agent_invoke}),
                )
            except AuthorizationError as error:
                raise command_not_found() from error

        return await self._acceptance.advance_thread(
            environment=RetainedRunEnvironment(source.id, source.thread_id),
            run=successor,
            state=state,
            expected_thread_version=request.expected_thread_version,
            expected_current_run_id=source.id,
            expected_head_run_id=source.id,
            next_head_run_id=source.id,
            hook_subscription=request.hook_subscription,
            hook_source_run_id=source.id if "hook_subscription" not in request.model_fields_set else None,
            hook_actor=actor.principal,
            final_validator=validate_final,
            transaction_hook=transaction_hook,
        )

    async def _load_retry_source(self, *, actor: AuthenticatedActor, run_id: str) -> tuple[Run, Thread, Run | None]:
        async with short_session(self._sessions) as database:
            row = (
                await database.execute(
                    select(RunRecord, ThreadRecord)
                    .join(
                        SessionRecord,
                        and_(
                            SessionRecord.organization_id == RunRecord.organization_id,
                            SessionRecord.id == RunRecord.session_id,
                        ),
                    )
                    .join(
                        ThreadRecord,
                        and_(
                            ThreadRecord.organization_id == RunRecord.organization_id,
                            ThreadRecord.id == RunRecord.thread_id,
                        ),
                    )
                    .where(
                        RunRecord.id == run_id,
                        SessionRecord.workspace_id == actor.workspace_id,
                    )
                )
            ).one_or_none()
            if row is None:
                raise command_not_found()
            source_record, thread_record = row
            try:
                await authorize_agent(
                    database,
                    actor=actor,
                    workspace_id=actor.workspace_id,
                    agent_id=source_record.agent_id,
                    action=WorkspaceAction.run_retry,
                )
            except AuthorizationError as error:
                raise command_not_found() from error
            if thread_record.current_run_id != source_record.id or source_record.status not in {
                RunStatus.failed.value,
                RunStatus.cancelled.value,
            }:
                raise InteractionCommandError(
                    "run_not_retryable",
                    "The selected Run is not the Thread's current failed or cancelled Run.",
                    category=ErrorCategory.conflict,
                )
            parent = None
            if source_record.parent_run_id is not None:
                parent_record = await database.scalar(
                    select(RunRecord).where(
                        RunRecord.organization_id == source_record.organization_id,
                        RunRecord.id == source_record.parent_run_id,
                    )
                )
                if parent_record is None:
                    raise command_not_found()
                parent = parent_record.to_resource()
            return source_record.to_resource(), thread_record.to_resource(), parent

    async def _load_feedback_source(
        self,
        *,
        actor: AuthenticatedActor,
        run_id: str,
        actions: frozenset[WorkspaceAction] = frozenset({WorkspaceAction.run_feedback}),
    ) -> tuple[Run, Thread]:
        async with short_session(self._sessions) as database:
            row = (
                await database.execute(
                    select(RunRecord, ThreadRecord)
                    .join(
                        SessionRecord,
                        and_(
                            SessionRecord.organization_id == RunRecord.organization_id,
                            SessionRecord.id == RunRecord.session_id,
                        ),
                    )
                    .join(
                        ThreadRecord,
                        and_(
                            ThreadRecord.organization_id == RunRecord.organization_id,
                            ThreadRecord.id == RunRecord.thread_id,
                        ),
                    )
                    .where(
                        RunRecord.id == run_id,
                        SessionRecord.workspace_id == actor.workspace_id,
                    )
                )
            ).one_or_none()
            if row is None:
                raise command_not_found()
            source_record, thread_record = row
            try:
                for action in actions:
                    await authorize_agent(
                        database,
                        actor=actor,
                        workspace_id=actor.workspace_id,
                        agent_id=source_record.agent_id,
                        action=action,
                    )
            except AuthorizationError as error:
                raise command_not_found() from error
            source = source_record.to_resource()
            if source.status is not RunStatus.waiting:
                raise InteractionCommandError(
                    "run_not_feedback_eligible",
                    "The selected Run is not waiting for feedback.",
                    category=ErrorCategory.conflict,
                )
            return source, thread_record.to_resource()
