"""Shared application commands behind the public Gateway protocols."""

from __future__ import annotations

import hashlib

from pydantic import Field
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.agents.domain import AgentRunOverride, canonical_digest
from a13n_service.agents.invocation_resolution import AgentInvocationResolver, FrozenAgentInvocation
from a13n_service.assets.service import AssetService
from a13n_service.endpoint_policy import EndpointPolicy
from a13n_service.hooks.domain import InlineHookSubscriptionInput
from a13n_service.hooks.persistence import load_inline_hook_subscription
from a13n_service.iam import AuthenticatedActor, AuthorizationError, WorkspaceAction, authorize_agent
from a13n_service.interactions import (
    AgentInput,
    AgentInputAcceptance,
    AgentInputAcceptanceContext,
    AgentInputError,
    RecoveryBudget,
    RecoveryUsage,
    Run,
    RunAcceptanceError,
    RunAcceptanceReceipt,
    RunAcceptanceService,
    RunInputKind,
    RunLineageKind,
    RunStateSeed,
    RunStateStore,
    RunStatus,
    Session,
    Thread,
    ThreadOriginKind,
    ThreadRole,
    initialize_completed_continuation_state,
    initialize_start_state,
    new_run_id,
    new_session_id,
    new_thread_id,
)
from a13n_service.interactions.domain import StrictModel
from a13n_service.interactions.models import RunRecord, SessionRecord, ThreadRecord
from a13n_service.public_errors import PublicError
from a13n_service.storage import short_session, transaction
from a13n_service.temporal import Clock, utc_now


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
            )
        except RunAcceptanceError as error:
            raise _map_acceptance_error(error) from error

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
        async def authorize_asset(asset_id: str):
            return await self._assets.require_for_use(actor=actor, asset_id=asset_id)

        environment = frozen.effective_config.resolved_environment
        acceptance = AgentInputAcceptance(self._endpoint_policy, authorize_asset)
        try:
            return await acceptance.accept(
                submitted,
                AgentInputAcceptanceContext(
                    workspace_id=workspace_id,
                    model_characteristics=frozen.effective_config.resolved_model.characteristics,
                    max_input_bytes=frozen.effective_config.protocol.limits.max_input_bytes,
                    structured_content_schema=frozen.effective_config.protocol.input_data_schema,
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


__all__ = [
    "ContinueRunRequest",
    "GatewayCommandError",
    "NativeInteractionCommands",
    "StartRunRequest",
]
