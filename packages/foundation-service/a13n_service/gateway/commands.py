"""Shared application commands behind the public Gateway protocols."""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Literal

from a13n_harness import SafeFailure
from pydantic import Field
from sqlalchemy import and_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.agents.domain import AgentRunOverride, EffectiveAgentConfig, canonical_digest
from a13n_service.agents.invocation_resolution import AgentInvocationResolver, FrozenAgentInvocation
from a13n_service.assets.service import AssetService
from a13n_service.durable_operations.idempotency import (
    EvidenceScope,
    IdempotencyConflict,
    IdempotencyIdentity,
    InvalidIdempotencyKey,
    digest_visible_ascii_key,
    is_evidence_unique_race,
    load_evidence,
    new_evidence,
)
from a13n_service.endpoint_policy import EndpointPolicy
from a13n_service.hooks.domain import InlineHookSubscriptionInput
from a13n_service.hooks.persistence import load_inline_hook_subscription
from a13n_service.iam import (
    AuthenticatedActor,
    AuthorizationError,
    WorkspaceAction,
    authorize_agent,
    authorize_persisted_agent_principal_actions,
)
from a13n_service.interactions import (
    AgentInput,
    AgentInputAcceptance,
    AgentInputAcceptanceContext,
    AgentInputError,
    InterruptRequest,
    RecoveryBudget,
    RecoveryUsage,
    Run,
    RunAcceptanceError,
    RunAcceptanceReceipt,
    RunAcceptanceService,
    RunInputKind,
    RunLineageKind,
    RunObjectError,
    RunOutcomeError,
    RunOutcomeService,
    RunPayloadEnvelope,
    RunPayloadStore,
    RunStateSeed,
    RunStateStore,
    RunStatus,
    Session,
    SteerReceipt,
    SteerStatus,
    Thread,
    ThreadInboxConflict,
    ThreadInboxStore,
    ThreadOriginKind,
    ThreadRole,
    WaitingRunFeedbackRequest,
    initialize_completed_continuation_state,
    initialize_fork_state,
    initialize_retry_state,
    initialize_start_state,
    initialize_waiting_continuation_state,
    new_run_id,
    new_session_id,
    new_thread_id,
    normalize_feedback,
    normalize_waiting_continue,
)
from a13n_service.interactions.domain import StrictModel
from a13n_service.interactions.models import RunRecord, SessionRecord, ThreadRecord
from a13n_service.public_errors import PublicError
from a13n_service.storage import ObjectStoreError, short_session, transaction
from a13n_service.temporal import Clock, assume_utc, utc_now


class GatewayCommandError(PublicError):
    """A bounded command failure safe for every Gateway adapter."""


class StartRunRequest(StrictModel):
    agent_id: str
    input: AgentInput
    session_id: str | None = None
    agent_revision_id: str | None = None
    expected_current_revision_id: str | None = None
    config_override: AgentRunOverride | None = None
    hook_subscription: InlineHookSubscriptionInput | None = None


class ContinueRunRequest(StrictModel):
    expected_thread_version: int = Field(ge=1)
    input: AgentInput
    agent_id: str | None = None
    agent_revision_id: str | None = None
    expected_current_revision_id: str | None = None
    config_override: AgentRunOverride | None = None
    hook_subscription: InlineHookSubscriptionInput | None = None


class WaitingContinueRunRequest(StrictModel):
    expected_thread_version: int = Field(ge=1)
    sealed_state_digest_sha256: str
    input: AgentInput
    hook_subscription: InlineHookSubscriptionInput | None = None


class RetryRunRequest(StrictModel):
    expected_thread_version: int = Field(ge=1)


class ForkRunRequest(StrictModel):
    input: AgentInput
    agent_id: str | None = None
    agent_revision_id: str | None = None
    expected_current_revision_id: str | None = None
    config_override: AgentRunOverride | None = None
    hook_subscription: InlineHookSubscriptionInput | None = None


class InterruptReceipt(StrictModel):
    schema_version: Literal["1"] = "1"
    run_id: str
    status: Literal["cancelled"] = "cancelled"
    interrupted_at: datetime


class NativeInteractionCommands:
    """Accept Agent work once for reuse by Native, AG-UI, and A2A adapters."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        invocations: AgentInvocationResolver,
        acceptance: RunAcceptanceService,
        states: RunStateStore,
        assets: AssetService,
        endpoint_policy: EndpointPolicy,
        *,
        outcomes: RunOutcomeService | None = None,
        inbox: ThreadInboxStore | None = None,
        payloads: RunPayloadStore | None = None,
        recovery_max_attempts: int = 3,
        max_handoffs: int = 2,
        queue_name: str = "default",
        priority: int = 0,
        clock: Clock = utc_now,
    ) -> None:
        self._sessions = sessions
        self._invocations = invocations
        self._acceptance = acceptance
        self._states = states
        self._assets = assets
        self._endpoint_policy = endpoint_policy
        self._outcomes = outcomes
        self._inbox = inbox
        self._payloads = payloads
        self._recovery_max_attempts = recovery_max_attempts
        self._max_handoffs = max_handoffs
        self._queue_name = queue_name
        self._priority = priority
        self._clock = clock

    async def start(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        idempotency_key: str,
        request: StartRunRequest,
        transaction_hook: Callable[[AsyncSession, RunAcceptanceReceipt], Awaitable[None]] | None = None,
    ) -> RunAcceptanceReceipt:
        if workspace_id != actor.boundary_workspace_id:
            raise _not_found()
        _require_idempotency_key(idempotency_key)
        stored_key = _scoped_idempotency_key(
            actor=actor,
            operation="run.start",
            scope_id=workspace_id,
            supplied=idempotency_key,
        )
        request_fingerprint = canonical_digest(request)
        replay = await self._start_replay(
            actor=actor,
            workspace_id=workspace_id,
            stored_key=stored_key,
            request_fingerprint=request_fingerprint,
            accepted_thread_version=1,
        )
        if replay is not None:
            return replay

        run_id = new_run_id()
        prepared = await self._invocations.preparation.prepare(
            actor=actor,
            agent_id=request.agent_id,
            agent_revision_id=request.agent_revision_id,
            expected_current_revision_id=request.expected_current_revision_id,
            config_override=request.config_override,
            run_id=run_id,
        )
        async with transaction(self._sessions) as database:
            frozen = await self._invocations.freezing.freeze_in_transaction(database, prepared=prepared)
        if frozen.mcp_tool_snapshot is None:
            raise GatewayCommandError(
                "run_connectivity_unavailable",
                "The Run tool snapshot could not be frozen.",
                status_code=409,
            )

        accepted_input = await self._accept_input(
            actor=actor, workspace_id=workspace_id, request=request, frozen=frozen
        )
        session_id = request.session_id or new_session_id()
        session = None
        if request.session_id is None:
            now = self._clock()
            session = Session(
                id=session_id,
                tenant_id=prepared.organization_id,
                workspace_id=workspace_id,
                created_at=now,
                updated_at=now,
            )
        else:
            await self._require_session(
                tenant_id=prepared.organization_id,
                workspace_id=workspace_id,
                session_id=session_id,
            )

        thread_id = new_thread_id()
        now = self._clock()
        state = initialize_start_state(
            RunStateSeed(
                run_id=run_id,
                agent_id=frozen.agent_id,
                agent_revision_id=frozen.agent_revision_id,
                effective_agent_config=frozen.effective_config,
            ),
            thread_id=thread_id,
        )
        run = Run(
            id=run_id,
            version=1,
            tenant_id=prepared.organization_id,
            authority_principal=actor.principal,
            session_id=session_id,
            thread_id=thread_id,
            parent_run_id=None,
            retry_of_run_id=None,
            lineage_kind=RunLineageKind.root,
            trigger_type="user_input",
            agent_id=frozen.agent_id,
            agent_revision_id=frozen.agent_revision_id,
            effective_agent_config_digest=frozen.effective_config.content_digest,
            encrypted_config_payload=None,
            runtime_lock_digest=frozen.effective_config.runtime_lock_digest,
            model_execution_observation=frozen.effective_config.resolved_model.execution.observation(),
            connector_connection_selections=tuple(
                item.model_dump(mode="json") for item in frozen.connector_connection_selections
            ),
            mcp_connection_selections=tuple(item.model_dump(mode="json") for item in frozen.mcp_connection_selections),
            ingress_context=None,
            mcp_tool_snapshot=frozen.mcp_tool_snapshot,
            priority=self._priority,
            queue_name=self._queue_name,
            available_at=now,
            current_run_attempt_id=None,
            next_attempt_fence=1,
            recovery_budget=RecoveryBudget(
                policy_version="1",
                max_recovery_attempts=self._recovery_max_attempts,
                max_handoffs=self._max_handoffs,
            ),
            attempts_started=0,
            recovery_attempts_started=0,
            handoffs_completed=0,
            usage_charged=RecoveryUsage(),
            idempotency_key=stored_key,
            request_fingerprint=request_fingerprint,
            status=RunStatus.accepted,
            wait_reason=None,
            input_kind=RunInputKind.agent_input,
            input=accepted_input.model_dump(mode="json", by_alias=True, exclude_none=True),
            input_text=_input_text(accepted_input),
            created_at=now,
            updated_at=now,
        )
        thread = Thread(
            id=thread_id,
            version=1,
            queue_version=0,
            tenant_id=prepared.organization_id,
            session_id=session_id,
            role=ThreadRole.root,
            origin_kind=ThreadOriginKind.new,
            origin_thread_id=None,
            origin_run_id=None,
            head_run_id=None,
            current_run_id=run_id,
            created_at=now,
            updated_at=now,
        )

        async def validate_final(database: AsyncSession) -> None:
            final = await self._invocations.freezing.freeze_in_transaction(database, prepared=prepared)
            if final != frozen:
                raise GatewayCommandError(
                    "run_invocation_changed",
                    "The selected Agent invocation changed before Run acceptance.",
                    status_code=409,
                )

        try:
            return await self._acceptance.accept_new_thread(
                session=session,
                thread=thread,
                run=run,
                state=state,
                hook_subscription=request.hook_subscription,
                final_validator=validate_final,
                transaction_hook=transaction_hook,
            )
        except RunAcceptanceError as error:
            raise _map_acceptance_error(error) from error

    async def continue_from(
        self,
        *,
        actor: AuthenticatedActor,
        source_run_id: str,
        idempotency_key: str,
        request: ContinueRunRequest,
        transaction_hook: Callable[[AsyncSession, RunAcceptanceReceipt], Awaitable[None]] | None = None,
    ) -> RunAcceptanceReceipt:
        _require_idempotency_key(idempotency_key)
        stored_key = _scoped_idempotency_key(
            actor=actor,
            operation="run.continue_from",
            scope_id=source_run_id,
            supplied=idempotency_key,
        )
        request_fingerprint = canonical_digest(request)
        replay = await self._start_replay(
            actor=actor,
            workspace_id=actor.boundary_workspace_id,
            stored_key=stored_key,
            request_fingerprint=request_fingerprint,
            accepted_thread_version=request.expected_thread_version + 1,
        )
        if replay is not None:
            return replay

        source, thread = await self._load_continue_source(actor=actor, source_run_id=source_run_id)
        source_state = await self._states.read(
            source.tenant_id,
            source.id,
            expected_thread_id=source.thread_id,
        )
        run_id = new_run_id()
        prepared = await self._invocations.preparation.prepare(
            actor=actor,
            agent_id=request.agent_id or source.agent_id,
            agent_revision_id=request.agent_revision_id,
            expected_current_revision_id=request.expected_current_revision_id,
            config_override=request.config_override,
            run_id=run_id,
        )
        async with transaction(self._sessions) as database:
            frozen = await self._invocations.freezing.freeze_in_transaction(database, prepared=prepared)
        if frozen.mcp_tool_snapshot is None:
            raise GatewayCommandError(
                "run_connectivity_unavailable",
                "The Run tool snapshot could not be frozen.",
                status_code=409,
            )
        accepted_input = await self._accept_input_value(
            actor=actor,
            workspace_id=actor.boundary_workspace_id,
            submitted=request.input,
            frozen=frozen,
        )
        now = self._clock()
        state = initialize_completed_continuation_state(
            RunStateSeed(
                run_id=run_id,
                agent_id=frozen.agent_id,
                agent_revision_id=frozen.agent_revision_id,
                effective_agent_config=frozen.effective_config,
            ),
            source_state.envelope,
        )
        run = Run(
            id=run_id,
            version=1,
            tenant_id=source.tenant_id,
            authority_principal=actor.principal,
            session_id=source.session_id,
            thread_id=source.thread_id,
            parent_run_id=source.id,
            retry_of_run_id=None,
            lineage_kind=RunLineageKind.continue_,
            trigger_type="user_input",
            agent_id=frozen.agent_id,
            agent_revision_id=frozen.agent_revision_id,
            effective_agent_config_digest=frozen.effective_config.content_digest,
            encrypted_config_payload=None,
            runtime_lock_digest=frozen.effective_config.runtime_lock_digest,
            model_execution_observation=frozen.effective_config.resolved_model.execution.observation(),
            connector_connection_selections=tuple(
                item.model_dump(mode="json") for item in frozen.connector_connection_selections
            ),
            mcp_connection_selections=tuple(item.model_dump(mode="json") for item in frozen.mcp_connection_selections),
            ingress_context=None,
            mcp_tool_snapshot=frozen.mcp_tool_snapshot,
            priority=self._priority,
            queue_name=self._queue_name,
            available_at=now,
            current_run_attempt_id=None,
            next_attempt_fence=1,
            recovery_budget=RecoveryBudget(
                policy_version="1",
                max_recovery_attempts=self._recovery_max_attempts,
                max_handoffs=self._max_handoffs,
            ),
            attempts_started=0,
            recovery_attempts_started=0,
            handoffs_completed=0,
            usage_charged=RecoveryUsage(),
            idempotency_key=stored_key,
            request_fingerprint=request_fingerprint,
            status=RunStatus.accepted,
            wait_reason=None,
            input_kind=RunInputKind.agent_input,
            input=accepted_input.model_dump(mode="json", by_alias=True, exclude_none=True),
            input_text=_input_text(accepted_input),
            created_at=now,
            updated_at=now,
        )

        async def validate_final(database: AsyncSession) -> None:
            try:
                await authorize_agent(
                    database,
                    actor=actor,
                    workspace_id=actor.boundary_workspace_id,
                    agent_id=source.agent_id,
                    action=WorkspaceAction.run_continue,
                )
            except AuthorizationError as error:
                raise _not_found() from error
            final = await self._invocations.freezing.freeze_in_transaction(database, prepared=prepared)
            if final != frozen:
                raise GatewayCommandError(
                    "run_invocation_changed",
                    "The selected Agent invocation changed before Run acceptance.",
                    status_code=409,
                )

        try:
            return await self._acceptance.advance_thread(
                run=run,
                state=state,
                expected_thread_version=request.expected_thread_version,
                expected_current_run_id=thread.current_run_id,
                expected_head_run_id=thread.head_run_id,
                next_head_run_id=source.id,
                hook_subscription=request.hook_subscription,
                final_validator=validate_final,
                transaction_hook=transaction_hook,
            )
        except RunAcceptanceError as error:
            raise _map_acceptance_error(error) from error

    async def fork(
        self,
        *,
        actor: AuthenticatedActor,
        run_id: str,
        idempotency_key: str,
        request: ForkRunRequest,
    ) -> RunAcceptanceReceipt:
        _require_idempotency_key(idempotency_key)
        stored_key = _scoped_idempotency_key(
            actor=actor,
            operation="run.fork",
            scope_id=run_id,
            supplied=idempotency_key,
        )
        request_fingerprint = canonical_digest(request)
        replay = await self._start_replay(
            actor=actor,
            workspace_id=actor.boundary_workspace_id,
            stored_key=stored_key,
            request_fingerprint=request_fingerprint,
            accepted_thread_version=1,
        )
        if replay is not None:
            return replay

        source = await self._load_fork_source(actor=actor, run_id=run_id)
        source_state = await self._states.read(
            source.tenant_id,
            source.id,
            expected_thread_id=source.thread_id,
        )
        new_run_id_value = new_run_id()
        new_thread_id_value = new_thread_id()
        reuse_exact_source = (
            request.agent_id is None
            and request.agent_revision_id is None
            and request.expected_current_revision_id is None
            and request.config_override is None
        )
        prepared = await self._invocations.preparation.prepare(
            actor=actor,
            agent_id=request.agent_id or source.agent_id,
            agent_revision_id=(
                source.agent_revision_id
                if request.agent_id is None and request.agent_revision_id is None
                else request.agent_revision_id
            ),
            expected_current_revision_id=request.expected_current_revision_id,
            config_override=request.config_override,
            run_id=new_run_id_value,
        )
        async with transaction(self._sessions) as database:
            frozen = await self._invocations.freezing.freeze_in_transaction(database, prepared=prepared)
        if frozen.mcp_tool_snapshot is None:
            raise GatewayCommandError(
                "run_connectivity_unavailable",
                "The Run tool snapshot could not be frozen.",
                status_code=409,
            )
        if reuse_exact_source and frozen.effective_config != source_state.envelope.effective_agent_config:
            raise GatewayCommandError(
                "run_fork_source_changed",
                "The source Run's exact executable configuration is no longer available.",
                status_code=409,
            )
        accepted_input = await self._accept_input_value(
            actor=actor,
            workspace_id=actor.boundary_workspace_id,
            submitted=request.input,
            frozen=frozen,
        )
        state = initialize_fork_state(
            RunStateSeed(
                run_id=new_run_id_value,
                agent_id=frozen.agent_id,
                agent_revision_id=frozen.agent_revision_id,
                effective_agent_config=frozen.effective_config,
            ),
            source_state.envelope,
            thread_id=new_thread_id_value,
        )
        now = self._clock()
        forked_run = Run(
            id=new_run_id_value,
            version=1,
            tenant_id=source.tenant_id,
            authority_principal=actor.principal,
            session_id=source.session_id,
            thread_id=new_thread_id_value,
            parent_run_id=source.id,
            retry_of_run_id=None,
            lineage_kind=RunLineageKind.fork,
            trigger_type="user_input",
            agent_id=frozen.agent_id,
            agent_revision_id=frozen.agent_revision_id,
            effective_agent_config_digest=frozen.effective_config.content_digest,
            encrypted_config_payload=None,
            runtime_lock_digest=frozen.effective_config.runtime_lock_digest,
            model_execution_observation=frozen.effective_config.resolved_model.execution.observation(),
            connector_connection_selections=tuple(
                item.model_dump(mode="json") for item in frozen.connector_connection_selections
            ),
            mcp_connection_selections=tuple(item.model_dump(mode="json") for item in frozen.mcp_connection_selections),
            ingress_context=None,
            mcp_tool_snapshot=frozen.mcp_tool_snapshot,
            priority=self._priority,
            queue_name=self._queue_name,
            available_at=now,
            current_run_attempt_id=None,
            next_attempt_fence=1,
            recovery_budget=RecoveryBudget(
                policy_version="1",
                max_recovery_attempts=self._recovery_max_attempts,
                max_handoffs=self._max_handoffs,
            ),
            attempts_started=0,
            recovery_attempts_started=0,
            handoffs_completed=0,
            usage_charged=RecoveryUsage(),
            idempotency_key=stored_key,
            request_fingerprint=request_fingerprint,
            status=RunStatus.accepted,
            wait_reason=None,
            input_kind=RunInputKind.agent_input,
            input=accepted_input.model_dump(mode="json", by_alias=True, exclude_none=True),
            input_text=_input_text(accepted_input),
            created_at=now,
            updated_at=now,
        )
        thread = Thread(
            id=new_thread_id_value,
            version=1,
            queue_version=0,
            tenant_id=source.tenant_id,
            session_id=source.session_id,
            role=ThreadRole.child,
            origin_kind=ThreadOriginKind.fork,
            origin_thread_id=source.thread_id,
            origin_run_id=source.id,
            head_run_id=None,
            current_run_id=new_run_id_value,
            created_at=now,
            updated_at=now,
        )

        async def validate_final(database: AsyncSession) -> None:
            try:
                await authorize_agent(
                    database,
                    actor=actor,
                    workspace_id=actor.boundary_workspace_id,
                    agent_id=source.agent_id,
                    action=WorkspaceAction.run_fork,
                )
            except AuthorizationError as error:
                raise _not_found() from error
            final = await self._invocations.freezing.freeze_in_transaction(database, prepared=prepared)
            if final != frozen:
                raise GatewayCommandError(
                    "run_invocation_changed",
                    "The selected Agent invocation changed before Run acceptance.",
                    status_code=409,
                )

        try:
            return await self._acceptance.accept_new_thread(
                session=None,
                thread=thread,
                run=forked_run,
                state=state,
                hook_subscription=request.hook_subscription,
                final_validator=validate_final,
            )
        except RunAcceptanceError as error:
            raise _map_acceptance_error(error) from error

    async def retry(
        self,
        *,
        actor: AuthenticatedActor,
        run_id: str,
        idempotency_key: str,
        request: RetryRunRequest,
    ) -> RunAcceptanceReceipt:
        if self._payloads is None:
            raise GatewayCommandError(
                "gateway_command_unavailable",
                "Run retry is unavailable.",
                status_code=503,
            )
        _require_idempotency_key(idempotency_key)
        stored_key = _scoped_idempotency_key(
            actor=actor,
            operation="run.retry",
            scope_id=run_id,
            supplied=idempotency_key,
        )
        request_fingerprint = canonical_digest(request)
        replay = await self._start_replay(
            actor=actor,
            workspace_id=actor.boundary_workspace_id,
            stored_key=stored_key,
            request_fingerprint=request_fingerprint,
            accepted_thread_version=request.expected_thread_version + 1,
        )
        if replay is not None:
            return replay

        source, thread = await self._load_retry_source(actor=actor, run_id=run_id)
        source_state = await self._states.read(
            source.tenant_id,
            source.id,
            expected_thread_id=source.thread_id,
        )
        parent_state = None
        if source.parent_run_id is not None:
            parent_state = (
                await self._states.read(
                    source.tenant_id,
                    source.parent_run_id,
                )
            ).envelope
        new_run_id_value = new_run_id()
        input_fields: dict[str, object]
        if source.input_object is None:
            input_fields = {"input": source.input}
        else:
            source_payload = await self._payloads.verify_reference(
                source.tenant_id,
                source.id,
                "input",
                source.input_object,
            )
            input_fields = {
                "input_object": await self._payloads.create(
                    source.tenant_id,
                    RunPayloadEnvelope(
                        run_id=new_run_id_value,
                        payload_kind="input",
                        payload_schema_version=source_payload.payload_schema_version,
                        payload=source_payload.payload,
                    ),
                )
            }
        state = initialize_retry_state(
            RunStateSeed(
                run_id=new_run_id_value,
                agent_id=source.agent_id,
                agent_revision_id=source.agent_revision_id,
                effective_agent_config=source_state.envelope.effective_agent_config,
            ),
            thread_id=source.thread_id,
            source_lineage_kind=source.lineage_kind,
            source_input_kind=source.input_kind,
            parent=parent_state,
        )
        now = self._clock()
        retry_values = source.model_dump(mode="python", exclude_unset=True)
        for field in (
            "input",
            "input_object",
            "output",
            "output_object",
            "output_text",
            "failure",
            "pending",
            "sealed_state",
            "started_at",
            "waiting_at",
            "completed_at",
            "sealed_at",
        ):
            retry_values.pop(field, None)
        retry_values.update(
            {
                "id": new_run_id_value,
                "version": 1,
                "retry_of_run_id": source.id,
                "available_at": now,
                "current_run_attempt_id": None,
                "next_attempt_fence": 1,
                "attempts_started": 0,
                "recovery_attempts_started": 0,
                "handoffs_completed": 0,
                "usage_charged": RecoveryUsage(),
                "idempotency_key": stored_key,
                "request_fingerprint": request_fingerprint,
                "status": RunStatus.accepted,
                "wait_reason": None,
                "created_at": now,
                "updated_at": now,
                **input_fields,
            }
        )
        retry_run = Run.model_validate(retry_values)

        async def validate_final(database: AsyncSession) -> None:
            try:
                await authorize_agent(
                    database,
                    actor=actor,
                    workspace_id=actor.boundary_workspace_id,
                    agent_id=source.agent_id,
                    action=WorkspaceAction.run_retry,
                )
                await authorize_persisted_agent_principal_actions(
                    database,
                    principal=source.authority_principal,
                    organization_id=source.tenant_id,
                    workspace_id=actor.boundary_workspace_id,
                    agent_id=source.agent_id,
                    actions=frozenset({WorkspaceAction.agent_invoke}),
                )
            except AuthorizationError as error:
                raise _not_found() from error

        try:
            return await self._acceptance.advance_thread(
                run=retry_run,
                state=state,
                expected_thread_version=request.expected_thread_version,
                expected_current_run_id=source.id,
                expected_head_run_id=thread.head_run_id,
                next_head_run_id=thread.head_run_id,
                final_validator=validate_final,
            )
        except RunAcceptanceError as error:
            raise _map_acceptance_error(error) from error

    async def feedback(
        self,
        *,
        actor: AuthenticatedActor,
        run_id: str,
        idempotency_key: str,
        request: WaitingRunFeedbackRequest,
    ) -> RunAcceptanceReceipt:
        _require_idempotency_key(idempotency_key)
        source, thread = await self._load_feedback_source(actor=actor, run_id=run_id)
        source_state = await self._states.read(
            source.tenant_id,
            source.id,
            expected_thread_id=source.thread_id,
        )
        if source.sealed_state is None or source.pending is None:
            raise GatewayCommandError(
                "run_waiting_state_invalid",
                "The selected waiting Run has no complete pending state.",
                status_code=409,
            )
        try:
            normalized = normalize_feedback(
                waiting_run_id=source.id,
                sealed_state_digest_sha256=request.sealed_state_digest_sha256,
                pending=source.pending,
                submitted=request.resolutions,
            )
        except ValueError as error:
            raise GatewayCommandError(
                "run_feedback_invalid",
                str(error),
                status_code=400,
            ) from error
        request_fingerprint = canonical_digest(
            {
                "expected_thread_version": request.expected_thread_version,
                "feedback": normalized.model_dump(mode="json", by_alias=True),
                "hook_subscription": (
                    None
                    if request.hook_subscription is None
                    else request.hook_subscription.model_dump(mode="json", by_alias=True)
                ),
            }
        )
        stored_key = _scoped_idempotency_key(
            actor=actor,
            operation="run.feedback",
            scope_id=run_id,
            supplied=idempotency_key,
        )
        replay = await self._start_replay(
            actor=actor,
            workspace_id=actor.boundary_workspace_id,
            stored_key=stored_key,
            request_fingerprint=request_fingerprint,
            accepted_thread_version=request.expected_thread_version + 1,
        )
        if replay is not None:
            return replay
        if thread.current_run_id != source.id or thread.head_run_id != source.id:
            raise GatewayCommandError(
                "run_not_feedback_eligible",
                "The selected waiting Run is no longer the Thread's current head.",
                status_code=409,
            )
        if request.sealed_state_digest_sha256 != source.sealed_state.digest_sha256:
            raise GatewayCommandError(
                "run_waiting_state_conflict",
                "The waiting Run state changed before feedback acceptance.",
                status_code=409,
            )

        new_run_id_value = new_run_id()
        state = initialize_waiting_continuation_state(
            RunStateSeed(
                run_id=new_run_id_value,
                agent_id=source.agent_id,
                agent_revision_id=source.agent_revision_id,
                effective_agent_config=source_state.envelope.effective_agent_config,
            ),
            source_state.envelope,
        )
        now = self._clock()
        feedback_run = Run(
            id=new_run_id_value,
            version=1,
            tenant_id=source.tenant_id,
            authority_principal=source.authority_principal,
            session_id=source.session_id,
            thread_id=source.thread_id,
            parent_run_id=source.id,
            retry_of_run_id=None,
            lineage_kind=RunLineageKind.continue_,
            trigger_type="feedback",
            agent_id=source.agent_id,
            agent_revision_id=source.agent_revision_id,
            effective_agent_config_digest=source.effective_agent_config_digest,
            encrypted_config_payload=source.encrypted_config_payload,
            runtime_lock_digest=source.runtime_lock_digest,
            model_execution_observation=source.model_execution_observation,
            connector_connection_selections=source.connector_connection_selections,
            mcp_connection_selections=source.mcp_connection_selections,
            ingress_context=source.ingress_context,
            mcp_tool_snapshot=source.mcp_tool_snapshot,
            priority=source.priority,
            queue_name=source.queue_name,
            available_at=now,
            current_run_attempt_id=None,
            next_attempt_fence=1,
            recovery_budget=source.recovery_budget,
            attempts_started=0,
            recovery_attempts_started=0,
            handoffs_completed=0,
            usage_charged=RecoveryUsage(),
            idempotency_key=stored_key,
            request_fingerprint=request_fingerprint,
            status=RunStatus.accepted,
            wait_reason=None,
            input_kind=RunInputKind.waiting_feedback,
            input=normalized.model_dump(mode="json", by_alias=True),
            input_text=None,
            created_at=now,
            updated_at=now,
        )

        async def validate_final(database: AsyncSession) -> None:
            try:
                await authorize_agent(
                    database,
                    actor=actor,
                    workspace_id=actor.boundary_workspace_id,
                    agent_id=source.agent_id,
                    action=WorkspaceAction.run_feedback,
                )
                await authorize_persisted_agent_principal_actions(
                    database,
                    principal=source.authority_principal,
                    organization_id=source.tenant_id,
                    workspace_id=actor.boundary_workspace_id,
                    agent_id=source.agent_id,
                    actions=frozenset({WorkspaceAction.agent_invoke}),
                )
            except AuthorizationError as error:
                raise _not_found() from error

        try:
            return await self._acceptance.advance_thread(
                run=feedback_run,
                state=state,
                expected_thread_version=request.expected_thread_version,
                expected_current_run_id=source.id,
                expected_head_run_id=source.id,
                next_head_run_id=source.id,
                hook_subscription=request.hook_subscription,
                final_validator=validate_final,
            )
        except RunAcceptanceError as error:
            raise _map_acceptance_error(error) from error

    async def continue_waiting(
        self,
        *,
        actor: AuthenticatedActor,
        run_id: str,
        idempotency_key: str,
        request: WaitingContinueRunRequest,
        transaction_hook: Callable[[AsyncSession, RunAcceptanceReceipt], Awaitable[None]] | None = None,
    ) -> RunAcceptanceReceipt:
        _require_idempotency_key(idempotency_key)
        source, thread = await self._load_feedback_source(
            actor=actor,
            run_id=run_id,
            actions=frozenset({WorkspaceAction.run_continue, WorkspaceAction.run_feedback}),
        )
        source_state = await self._states.read(
            source.tenant_id,
            source.id,
            expected_thread_id=source.thread_id,
        )
        if source.sealed_state is None or source.pending is None:
            raise GatewayCommandError(
                "run_waiting_state_invalid",
                "The selected waiting Run has no complete pending state.",
                status_code=409,
            )
        accepted_input = await self._accept_input_for_effective(
            actor=actor,
            workspace_id=actor.boundary_workspace_id,
            submitted=request.input,
            effective=source_state.envelope.effective_agent_config,
        )
        normalized = normalize_waiting_continue(
            waiting_run_id=source.id,
            sealed_state_digest_sha256=request.sealed_state_digest_sha256,
            pending=source.pending,
            input=accepted_input,
        )
        request_fingerprint = canonical_digest(
            {
                "expected_thread_version": request.expected_thread_version,
                "waiting_continue": normalized.model_dump(mode="json", by_alias=True),
                "hook_subscription": (
                    None
                    if request.hook_subscription is None
                    else request.hook_subscription.model_dump(mode="json", by_alias=True)
                ),
            }
        )
        stored_key = _scoped_idempotency_key(
            actor=actor,
            operation="run.waiting_continue",
            scope_id=run_id,
            supplied=idempotency_key,
        )
        replay = await self._start_replay(
            actor=actor,
            workspace_id=actor.boundary_workspace_id,
            stored_key=stored_key,
            request_fingerprint=request_fingerprint,
            accepted_thread_version=request.expected_thread_version + 1,
        )
        if replay is not None:
            return replay
        if thread.current_run_id != source.id or thread.head_run_id != source.id:
            raise GatewayCommandError(
                "run_not_waiting_continue_eligible",
                "The selected waiting Run is no longer the Thread's current head.",
                status_code=409,
            )
        if request.sealed_state_digest_sha256 != source.sealed_state.digest_sha256:
            raise GatewayCommandError(
                "run_waiting_state_conflict",
                "The waiting Run state changed before continuation acceptance.",
                status_code=409,
            )

        successor_id = new_run_id()
        state = initialize_waiting_continuation_state(
            RunStateSeed(
                run_id=successor_id,
                agent_id=source.agent_id,
                agent_revision_id=source.agent_revision_id,
                effective_agent_config=source_state.envelope.effective_agent_config,
            ),
            source_state.envelope,
        )
        now = self._clock()
        successor = Run(
            id=successor_id,
            version=1,
            tenant_id=source.tenant_id,
            authority_principal=source.authority_principal,
            session_id=source.session_id,
            thread_id=source.thread_id,
            parent_run_id=source.id,
            retry_of_run_id=None,
            lineage_kind=RunLineageKind.continue_,
            trigger_type="user_input",
            agent_id=source.agent_id,
            agent_revision_id=source.agent_revision_id,
            effective_agent_config_digest=source.effective_agent_config_digest,
            encrypted_config_payload=source.encrypted_config_payload,
            runtime_lock_digest=source.runtime_lock_digest,
            model_execution_observation=source.model_execution_observation,
            connector_connection_selections=source.connector_connection_selections,
            mcp_connection_selections=source.mcp_connection_selections,
            ingress_context=source.ingress_context,
            mcp_tool_snapshot=source.mcp_tool_snapshot,
            priority=source.priority,
            queue_name=source.queue_name,
            available_at=now,
            current_run_attempt_id=None,
            next_attempt_fence=1,
            recovery_budget=source.recovery_budget,
            attempts_started=0,
            recovery_attempts_started=0,
            handoffs_completed=0,
            usage_charged=RecoveryUsage(),
            idempotency_key=stored_key,
            request_fingerprint=request_fingerprint,
            status=RunStatus.accepted,
            wait_reason=None,
            input_kind=RunInputKind.waiting_continue,
            input=normalized.model_dump(mode="json", by_alias=True),
            input_text=_input_text(accepted_input),
            created_at=now,
            updated_at=now,
        )

        async def validate_final(database: AsyncSession) -> None:
            try:
                for action in (WorkspaceAction.run_continue, WorkspaceAction.run_feedback):
                    await authorize_agent(
                        database,
                        actor=actor,
                        workspace_id=actor.boundary_workspace_id,
                        agent_id=source.agent_id,
                        action=action,
                    )
                await authorize_persisted_agent_principal_actions(
                    database,
                    principal=source.authority_principal,
                    organization_id=source.tenant_id,
                    workspace_id=actor.boundary_workspace_id,
                    agent_id=source.agent_id,
                    actions=frozenset({WorkspaceAction.agent_invoke}),
                )
            except AuthorizationError as error:
                raise _not_found() from error

        try:
            return await self._acceptance.advance_thread(
                run=successor,
                state=state,
                expected_thread_version=request.expected_thread_version,
                expected_current_run_id=source.id,
                expected_head_run_id=source.id,
                next_head_run_id=source.id,
                hook_subscription=request.hook_subscription,
                final_validator=validate_final,
                transaction_hook=transaction_hook,
            )
        except RunAcceptanceError as error:
            raise _map_acceptance_error(error) from error

    async def interrupt(
        self,
        *,
        actor: AuthenticatedActor,
        run_id: str,
        idempotency_key: str,
        request: InterruptRequest,
    ) -> InterruptReceipt:
        if self._outcomes is None:
            raise GatewayCommandError(
                "gateway_command_unavailable",
                "Run interruption is unavailable.",
                status_code=503,
            )
        identity = _command_identity(idempotency_key, request)
        scope = EvidenceScope(
            workspace_id=actor.boundary_workspace_id,
            actor_type=actor.principal.principal_type.value,
            actor_id=actor.principal.principal_id,
            operation="run.interrupt",
            scope_id=run_id,
        )
        replay = await self._interrupt_replay(actor=actor, run_id=run_id, scope=scope, identity=identity)
        if replay is not None:
            return replay
        source, thread = await self._load_interrupt_source(actor=actor, run_id=run_id)
        if source.version != request.expected_run_version or thread.version != request.expected_thread_version:
            raise GatewayCommandError(
                "run_precondition_changed",
                "The Run or Thread version changed before interruption.",
                status_code=409,
            )
        now = assume_utc(self._clock())

        async def validate_final(database: AsyncSession) -> None:
            try:
                await authorize_agent(
                    database,
                    actor=actor,
                    workspace_id=actor.boundary_workspace_id,
                    agent_id=source.agent_id,
                    action=WorkspaceAction.run_interrupt,
                )
            except AuthorizationError as error:
                raise _not_found() from error

        async def record_evidence(database: AsyncSession) -> None:
            try:
                existing = await load_evidence(database, scope=scope, identity=identity, now=now)
            except IdempotencyConflict as error:
                raise _idempotency_conflict() from error
            if existing is None:
                database.add(
                    new_evidence(
                        organization_id=source.tenant_id,
                        scope=scope,
                        identity=identity,
                        result_kind="run_interrupt",
                        result_ref=run_id,
                        now=now,
                    )
                )

        try:
            await self._outcomes.cancel(
                tenant_id=source.tenant_id,
                run_id=run_id,
                expected_run_version=request.expected_run_version,
                expected_thread_version=request.expected_thread_version,
                failure=SafeFailure(
                    code="run_interrupted",
                    message="The Run was interrupted by the client.",
                    retry_hint="new_run",
                ),
                final_validator=validate_final,
                transaction_hook=record_evidence,
            )
        except IntegrityError as error:
            if not is_evidence_unique_race(error):
                raise GatewayCommandError(
                    "run_interrupt_conflict",
                    "Run interruption lost a concurrent mutation.",
                    status_code=409,
                ) from error
            replay = await self._interrupt_replay(actor=actor, run_id=run_id, scope=scope, identity=identity)
            if replay is not None:
                return replay
            raise GatewayCommandError(
                "run_interrupt_conflict",
                "Run interruption lost a concurrent mutation.",
                status_code=409,
            ) from error
        except RunOutcomeError as error:
            replay = await self._interrupt_replay(actor=actor, run_id=run_id, scope=scope, identity=identity)
            if replay is not None:
                return replay
            raise GatewayCommandError(
                "run_interrupt_conflict",
                "The Run can no longer be interrupted.",
                status_code=409,
            ) from error
        return InterruptReceipt(run_id=run_id, interrupted_at=now)

    async def steer(
        self,
        *,
        actor: AuthenticatedActor,
        run_id: str,
        idempotency_key: str,
        input: AgentInput,
    ) -> SteerReceipt:
        if self._inbox is None:
            raise GatewayCommandError(
                "gateway_command_unavailable",
                "Run steering is unavailable.",
                status_code=503,
            )
        identity = _command_identity(idempotency_key, input)
        scope = EvidenceScope(
            workspace_id=actor.boundary_workspace_id,
            actor_type=actor.principal.principal_type.value,
            actor_id=actor.principal.principal_id,
            operation="run.steer",
            scope_id=run_id,
        )
        replay = await self._steer_replay(actor=actor, run_id=run_id, scope=scope, identity=identity)
        if replay is not None:
            return replay
        source, _thread = await self._load_steer_source(actor=actor, run_id=run_id)
        try:
            state = await self._states.read(source.tenant_id, source.id, expected_thread_id=source.thread_id)
        except (ObjectStoreError, RunObjectError) as error:
            raise GatewayCommandError(
                "run_state_unavailable",
                "The selected Run state is unavailable.",
                status_code=409,
            ) from error
        accepted = await self._accept_input_for_effective(
            actor=actor,
            workspace_id=actor.boundary_workspace_id,
            submitted=input,
            effective=state.envelope.effective_agent_config,
        )
        now = assume_utc(self._clock())

        async def validate_final(database: AsyncSession) -> None:
            try:
                await authorize_agent(
                    database,
                    actor=actor,
                    workspace_id=actor.boundary_workspace_id,
                    agent_id=source.agent_id,
                    action=WorkspaceAction.run_steer,
                )
            except AuthorizationError as error:
                raise _not_found() from error

        async def record_evidence(database: AsyncSession, receipt: SteerReceipt) -> None:
            try:
                existing = await load_evidence(database, scope=scope, identity=identity, now=now)
            except IdempotencyConflict as error:
                raise _idempotency_conflict() from error
            if existing is None:
                database.add(
                    new_evidence(
                        organization_id=source.tenant_id,
                        scope=scope,
                        identity=identity,
                        result_kind="run_steer",
                        result_ref=receipt.steer_id,
                        now=now,
                    )
                )

        try:
            return await self._inbox.append_steer(
                tenant_id=source.tenant_id,
                run_id=source.id,
                input=accepted,
                final_validator=validate_final,
                transaction_hook=record_evidence,
            )
        except IntegrityError as error:
            if is_evidence_unique_race(error):
                replay = await self._steer_replay(actor=actor, run_id=run_id, scope=scope, identity=identity)
                if replay is not None:
                    return replay
            raise GatewayCommandError(
                "run_steer_conflict",
                "Run steering lost a concurrent mutation.",
                status_code=409,
            ) from error
        except ThreadInboxConflict as error:
            raise GatewayCommandError(
                "run_steer_conflict",
                "The selected Run can no longer accept steer input.",
                status_code=409,
            ) from error

    async def get_steer(
        self,
        *,
        actor: AuthenticatedActor,
        run_id: str,
        steer_id: str,
    ) -> SteerStatus:
        if self._inbox is None:
            raise GatewayCommandError(
                "gateway_command_unavailable",
                "Run steering is unavailable.",
                status_code=503,
            )
        source, _thread = await self._load_steer_source(actor=actor, run_id=run_id, read_only=True)
        try:
            return await self._inbox.get_steer(tenant_id=source.tenant_id, run_id=run_id, steer_id=steer_id)
        except ThreadInboxConflict as error:
            raise _not_found() from error

    async def _steer_replay(
        self,
        *,
        actor: AuthenticatedActor,
        run_id: str,
        scope: EvidenceScope,
        identity: IdempotencyIdentity,
    ) -> SteerReceipt | None:
        assert self._inbox is not None
        now = assume_utc(self._clock())
        async with transaction(self._sessions) as database:
            row = (
                await database.execute(
                    select(RunRecord, SessionRecord)
                    .join(
                        SessionRecord,
                        and_(
                            SessionRecord.tenant_id == RunRecord.tenant_id,
                            SessionRecord.id == RunRecord.session_id,
                        ),
                    )
                    .where(
                        RunRecord.id == run_id,
                        SessionRecord.workspace_id == actor.boundary_workspace_id,
                    )
                )
            ).one_or_none()
            if row is None:
                raise _not_found()
            run, _session = row
            try:
                await authorize_agent(
                    database,
                    actor=actor,
                    workspace_id=actor.boundary_workspace_id,
                    agent_id=run.agent_id,
                    action=WorkspaceAction.run_steer,
                )
                evidence = await load_evidence(database, scope=scope, identity=identity, now=now)
            except AuthorizationError as error:
                raise _not_found() from error
            except IdempotencyConflict as error:
                raise _idempotency_conflict() from error
            if evidence is None:
                return None
            if evidence.result_kind != "run_steer":
                raise GatewayCommandError(
                    "idempotency_evidence_invalid",
                    "The Run steer replay evidence is invalid.",
                    status_code=503,
                )
            steer_id = evidence.result_ref
            tenant_id = run.tenant_id
        try:
            status = await self._inbox.get_steer(tenant_id=tenant_id, run_id=run_id, steer_id=steer_id)
        except ThreadInboxConflict as error:
            raise GatewayCommandError(
                "idempotency_evidence_invalid",
                "The Run steer replay evidence is invalid.",
                status_code=503,
            ) from error
        return SteerReceipt(
            session_id=status.session_id,
            thread_id=status.thread_id,
            run_id=status.accepted_against_run_id,
            steer_id=status.steer_id,
            delivery_sequence=status.delivery_sequence,
            accepted_at=status.created_at,
        )

    async def _load_steer_source(
        self,
        *,
        actor: AuthenticatedActor,
        run_id: str,
        read_only: bool = False,
    ):
        async with short_session(self._sessions) as database:
            row = (
                await database.execute(
                    select(RunRecord, ThreadRecord)
                    .join(
                        SessionRecord,
                        and_(
                            SessionRecord.tenant_id == RunRecord.tenant_id,
                            SessionRecord.id == RunRecord.session_id,
                        ),
                    )
                    .join(
                        ThreadRecord,
                        and_(
                            ThreadRecord.tenant_id == RunRecord.tenant_id,
                            ThreadRecord.id == RunRecord.thread_id,
                        ),
                    )
                    .where(
                        RunRecord.id == run_id,
                        SessionRecord.workspace_id == actor.boundary_workspace_id,
                    )
                )
            ).one_or_none()
            if row is None:
                raise _not_found()
            source, thread = row
            try:
                await authorize_agent(
                    database,
                    actor=actor,
                    workspace_id=actor.boundary_workspace_id,
                    agent_id=source.agent_id,
                    action=WorkspaceAction.run_read if read_only else WorkspaceAction.run_steer,
                )
            except AuthorizationError as error:
                raise _not_found() from error
            if not read_only and (
                thread.current_run_id != source.id
                or source.status
                not in {
                    RunStatus.accepted.value,
                    RunStatus.running.value,
                    RunStatus.waiting.value,
                }
            ):
                raise GatewayCommandError(
                    "run_not_steerable",
                    "The selected Run cannot accept steer input.",
                    status_code=409,
                )
            return source.to_resource(), thread.to_resource()

    async def _interrupt_replay(
        self,
        *,
        actor: AuthenticatedActor,
        run_id: str,
        scope: EvidenceScope,
        identity: IdempotencyIdentity,
    ) -> InterruptReceipt | None:
        now = assume_utc(self._clock())
        async with transaction(self._sessions) as database:
            row = (
                await database.execute(
                    select(RunRecord, SessionRecord)
                    .join(
                        SessionRecord,
                        and_(
                            SessionRecord.tenant_id == RunRecord.tenant_id,
                            SessionRecord.id == RunRecord.session_id,
                        ),
                    )
                    .where(
                        RunRecord.id == run_id,
                        SessionRecord.workspace_id == actor.boundary_workspace_id,
                    )
                )
            ).one_or_none()
            if row is None:
                raise _not_found()
            run, _session = row
            try:
                await authorize_agent(
                    database,
                    actor=actor,
                    workspace_id=actor.boundary_workspace_id,
                    agent_id=run.agent_id,
                    action=WorkspaceAction.run_interrupt,
                )
                evidence = await load_evidence(database, scope=scope, identity=identity, now=now)
            except AuthorizationError as error:
                raise _not_found() from error
            except IdempotencyConflict as error:
                raise _idempotency_conflict() from error
            if evidence is None:
                return None
            if evidence.result_kind != "run_interrupt" or evidence.result_ref != run.id or run.sealed_at is None:
                raise GatewayCommandError(
                    "idempotency_evidence_invalid",
                    "The Run interruption replay evidence is invalid.",
                    status_code=503,
                )
            return InterruptReceipt(run_id=run.id, interrupted_at=assume_utc(run.sealed_at))

    async def _load_interrupt_source(
        self,
        *,
        actor: AuthenticatedActor,
        run_id: str,
    ):
        async with short_session(self._sessions) as database:
            row = (
                await database.execute(
                    select(RunRecord, ThreadRecord)
                    .join(
                        SessionRecord,
                        and_(
                            SessionRecord.tenant_id == RunRecord.tenant_id,
                            SessionRecord.id == RunRecord.session_id,
                        ),
                    )
                    .join(
                        ThreadRecord,
                        and_(
                            ThreadRecord.tenant_id == RunRecord.tenant_id,
                            ThreadRecord.id == RunRecord.thread_id,
                        ),
                    )
                    .where(
                        RunRecord.id == run_id,
                        SessionRecord.workspace_id == actor.boundary_workspace_id,
                    )
                )
            ).one_or_none()
            if row is None:
                raise _not_found()
            source, thread = row
            try:
                await authorize_agent(
                    database,
                    actor=actor,
                    workspace_id=actor.boundary_workspace_id,
                    agent_id=source.agent_id,
                    action=WorkspaceAction.run_interrupt,
                )
            except AuthorizationError as error:
                raise _not_found() from error
            if source.status not in {RunStatus.accepted.value, RunStatus.running.value}:
                raise GatewayCommandError(
                    "run_not_interruptible",
                    "The selected Run is not active.",
                    status_code=409,
                )
            return source.to_resource(), thread.to_resource()

    async def _accept_input(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        request: StartRunRequest,
        frozen: FrozenAgentInvocation,
    ):
        return await self._accept_input_value(
            actor=actor,
            workspace_id=workspace_id,
            submitted=request.input,
            frozen=frozen,
        )

    async def _accept_input_value(self, *, actor, workspace_id, submitted, frozen):
        return await self._accept_input_for_effective(
            actor=actor,
            workspace_id=workspace_id,
            submitted=submitted,
            effective=frozen.effective_config,
        )

    async def _accept_input_for_effective(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        submitted: AgentInput,
        effective: EffectiveAgentConfig,
    ):
        async def authorize_asset(asset_id: str):
            return await self._assets.require_for_use(actor=actor, asset_id=asset_id)

        environment = effective.resolved_environment
        acceptance = AgentInputAcceptance(self._endpoint_policy, authorize_asset)
        try:
            return await acceptance.accept(
                submitted,
                AgentInputAcceptanceContext(
                    workspace_id=workspace_id,
                    model_characteristics=effective.resolved_model.characteristics,
                    max_input_bytes=effective.protocol.limits.max_input_bytes,
                    structured_content_schema=effective.protocol.input_data_schema,
                    environment_writable=environment is not None and environment.access != "read_only",
                    environment_bindings=(frozenset({"workspace"}) if environment is not None else frozenset()),
                ),
            )
        except AgentInputError as error:
            raise GatewayCommandError(error.code, str(error), status_code=400) from error

    async def _load_continue_source(self, *, actor: AuthenticatedActor, source_run_id: str):
        async with short_session(self._sessions) as database:
            row = (
                await database.execute(
                    select(RunRecord, ThreadRecord)
                    .join(
                        SessionRecord,
                        and_(
                            SessionRecord.tenant_id == RunRecord.tenant_id,
                            SessionRecord.id == RunRecord.session_id,
                        ),
                    )
                    .join(
                        ThreadRecord,
                        and_(
                            ThreadRecord.tenant_id == RunRecord.tenant_id,
                            ThreadRecord.id == RunRecord.thread_id,
                        ),
                    )
                    .where(
                        RunRecord.id == source_run_id,
                        SessionRecord.workspace_id == actor.boundary_workspace_id,
                    )
                )
            ).one_or_none()
            if row is None:
                raise _not_found()
            source_record, thread_record = row
            try:
                await authorize_agent(
                    database,
                    actor=actor,
                    workspace_id=actor.boundary_workspace_id,
                    agent_id=source_record.agent_id,
                    action=WorkspaceAction.run_continue,
                )
            except AuthorizationError as error:
                raise _not_found() from error
            source = source_record.to_resource()
            if source.status is not RunStatus.completed:
                raise GatewayCommandError(
                    "run_not_continuable",
                    "The selected Run is not a completed continuation source.",
                    status_code=409,
                )
            return source, thread_record.to_resource()

    async def _load_retry_source(self, *, actor: AuthenticatedActor, run_id: str):
        async with short_session(self._sessions) as database:
            row = (
                await database.execute(
                    select(RunRecord, ThreadRecord)
                    .join(
                        SessionRecord,
                        and_(
                            SessionRecord.tenant_id == RunRecord.tenant_id,
                            SessionRecord.id == RunRecord.session_id,
                        ),
                    )
                    .join(
                        ThreadRecord,
                        and_(
                            ThreadRecord.tenant_id == RunRecord.tenant_id,
                            ThreadRecord.id == RunRecord.thread_id,
                        ),
                    )
                    .where(
                        RunRecord.id == run_id,
                        SessionRecord.workspace_id == actor.boundary_workspace_id,
                    )
                )
            ).one_or_none()
            if row is None:
                raise _not_found()
            source_record, thread_record = row
            try:
                await authorize_agent(
                    database,
                    actor=actor,
                    workspace_id=actor.boundary_workspace_id,
                    agent_id=source_record.agent_id,
                    action=WorkspaceAction.run_retry,
                )
            except AuthorizationError as error:
                raise _not_found() from error
            if thread_record.current_run_id != source_record.id or source_record.status not in {
                RunStatus.failed.value,
                RunStatus.cancelled.value,
            }:
                raise GatewayCommandError(
                    "run_not_retryable",
                    "The selected Run is not the Thread's current failed or cancelled Run.",
                    status_code=409,
                )
            return source_record.to_resource(), thread_record.to_resource()

    async def _load_fork_source(self, *, actor: AuthenticatedActor, run_id: str) -> Run:
        async with short_session(self._sessions) as database:
            source_record = (
                await database.execute(
                    select(RunRecord)
                    .join(
                        SessionRecord,
                        and_(
                            SessionRecord.tenant_id == RunRecord.tenant_id,
                            SessionRecord.id == RunRecord.session_id,
                        ),
                    )
                    .where(
                        RunRecord.id == run_id,
                        SessionRecord.workspace_id == actor.boundary_workspace_id,
                    )
                )
            ).scalar_one_or_none()
            if source_record is None:
                raise _not_found()
            try:
                await authorize_agent(
                    database,
                    actor=actor,
                    workspace_id=actor.boundary_workspace_id,
                    agent_id=source_record.agent_id,
                    action=WorkspaceAction.run_fork,
                )
            except AuthorizationError as error:
                raise _not_found() from error
            source = source_record.to_resource()
            if source.status is not RunStatus.completed:
                raise GatewayCommandError(
                    "run_not_forkable",
                    "The selected Run is not a completed fork source.",
                    status_code=409,
                )
            return source

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
                            SessionRecord.tenant_id == RunRecord.tenant_id,
                            SessionRecord.id == RunRecord.session_id,
                        ),
                    )
                    .join(
                        ThreadRecord,
                        and_(
                            ThreadRecord.tenant_id == RunRecord.tenant_id,
                            ThreadRecord.id == RunRecord.thread_id,
                        ),
                    )
                    .where(
                        RunRecord.id == run_id,
                        SessionRecord.workspace_id == actor.boundary_workspace_id,
                    )
                )
            ).one_or_none()
            if row is None:
                raise _not_found()
            source_record, thread_record = row
            try:
                for action in actions:
                    await authorize_agent(
                        database,
                        actor=actor,
                        workspace_id=actor.boundary_workspace_id,
                        agent_id=source_record.agent_id,
                        action=action,
                    )
            except AuthorizationError as error:
                raise _not_found() from error
            source = source_record.to_resource()
            if source.status is not RunStatus.waiting:
                raise GatewayCommandError(
                    "run_not_feedback_eligible",
                    "The selected Run is not waiting for feedback.",
                    status_code=409,
                )
            return source, thread_record.to_resource()

    async def _require_session(self, *, tenant_id: str, workspace_id: str, session_id: str) -> None:
        async with short_session(self._sessions) as database:
            record = await database.scalar(
                select(SessionRecord).where(
                    SessionRecord.id == session_id,
                    SessionRecord.tenant_id == tenant_id,
                    SessionRecord.workspace_id == workspace_id,
                )
            )
        if record is None:
            raise _not_found()

    async def _start_replay(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        stored_key: str,
        request_fingerprint: str,
        accepted_thread_version: int,
    ) -> RunAcceptanceReceipt | None:
        async with short_session(self._sessions) as database:
            row = (
                await database.execute(
                    select(RunRecord, ThreadRecord)
                    .join(
                        SessionRecord,
                        and_(
                            SessionRecord.tenant_id == RunRecord.tenant_id,
                            SessionRecord.id == RunRecord.session_id,
                        ),
                    )
                    .join(
                        ThreadRecord,
                        and_(
                            ThreadRecord.tenant_id == RunRecord.tenant_id,
                            ThreadRecord.id == RunRecord.thread_id,
                        ),
                    )
                    .where(
                        SessionRecord.workspace_id == workspace_id,
                        RunRecord.authority_principal_type == actor.principal.principal_type.value,
                        RunRecord.authority_principal_id == actor.principal.principal_id,
                        RunRecord.idempotency_key == stored_key,
                    )
                )
            ).one_or_none()
            if row is None:
                return None
            run, _thread = row
            if run.request_fingerprint != request_fingerprint:
                raise GatewayCommandError(
                    "idempotency_conflict",
                    "The Idempotency-Key was already used with different request content.",
                    status_code=409,
                )
            hook = await load_inline_hook_subscription(
                database,
                organization_id=run.tenant_id,
                run_id=run.id,
            )
            return RunAcceptanceReceipt(
                session_id=run.session_id,
                thread_id=run.thread_id,
                thread_version=accepted_thread_version,
                run_id=run.id,
                run_version=1,
                hook_subscription_id=None if hook is None else hook[0].id,
            )


def _require_idempotency_key(value: str) -> None:
    if not 1 <= len(value.encode("utf-8")) <= 512 or any(not 0x21 <= ord(character) <= 0x7E for character in value):
        raise GatewayCommandError(
            "invalid_request",
            "Idempotency-Key must contain 1 through 512 visible ASCII bytes.",
            status_code=400,
        )


def _command_identity(idempotency_key: str, request: StrictModel) -> IdempotencyIdentity:
    try:
        key_digest = digest_visible_ascii_key(idempotency_key)
    except InvalidIdempotencyKey as error:
        raise GatewayCommandError(
            "invalid_request",
            "Idempotency-Key must contain 1 through 512 visible ASCII bytes.",
            status_code=400,
        ) from error
    return IdempotencyIdentity(key_digest=key_digest, request_digest=canonical_digest(request))


def _scoped_idempotency_key(*, actor: AuthenticatedActor, operation: str, scope_id: str, supplied: str) -> str:
    material = "\x1f".join(
        (
            operation,
            scope_id,
            actor.principal.principal_type.value,
            actor.principal.principal_id,
            supplied,
        )
    ).encode("utf-8")
    return f"idem_{hashlib.sha256(material).hexdigest()}"


def _input_text(accepted) -> str | None:
    if len(accepted.content) != 1 or accepted.structured_content is not None:
        return None
    block = accepted.content[0]
    return block.text if block.type == "text" else None


def _map_acceptance_error(error: RunAcceptanceError) -> GatewayCommandError:
    status_code = 404 if error.code.endswith("_not_found") else 409
    return GatewayCommandError(error.code, str(error), status_code=status_code)


def _not_found() -> GatewayCommandError:
    return GatewayCommandError("resource_not_found", "The requested resource was not found.", status_code=404)


def _idempotency_conflict() -> GatewayCommandError:
    return GatewayCommandError(
        "idempotency_conflict",
        "The Idempotency-Key was already used with different request content.",
        status_code=409,
    )


__all__ = [
    "ContinueRunRequest",
    "ForkRunRequest",
    "GatewayCommandError",
    "InterruptReceipt",
    "NativeInteractionCommands",
    "RetryRunRequest",
    "StartRunRequest",
    "WaitingContinueRunRequest",
]
