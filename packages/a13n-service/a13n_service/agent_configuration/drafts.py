"""Validated draft mutations, separate from formal Agent publication."""

from __future__ import annotations

from a13n_logging import get_logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.agents.domain import new_agent_id
from a13n_service.agents.resolution import AgentResolver, resolution_error
from a13n_service.durable_operations.requests import evidence_record, load_replay, request_identity
from a13n_service.iam import AuthenticatedActor
from a13n_service.iam.audit import security_audit_record
from a13n_service.ids import new_object_id
from a13n_service.interactions.attempts import AttemptContext, lock_attempt_authority
from a13n_service.interactions.models import SessionRecord
from a13n_service.storage import short_session, transaction
from a13n_service.temporal import Clock, next_updated_at, utc_now

from .authorization import authorize_candidate_snapshot, authorize_execution
from .context import ConfigurationRunContext
from .domain import ConfigurationDraft, ConfigurationValidation, candidate_digest
from .editing import edit_config
from .models import ConfigurationDraftRecord
from .persistence import failure, load_owned_draft, require_open
from .requests import DiscardDraftRequest, RebaseDraftRequest, UpdateConfigurationDraftRequest
from .validation import dependency_digest

logger = get_logger(__name__)


class ConfigurationDrafts:
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], resolver: AgentResolver, *, clock: Clock = utc_now
    ) -> None:
        self._sessions = sessions
        self._resolver = resolver
        self._clock = clock

    async def get(self, *, actor: AuthenticatedActor, draft_id: str) -> ConfigurationDraft:
        async with short_session(self._sessions) as session:
            _, record = await load_owned_draft(session, actor=actor, draft_id=draft_id, write=False)
            return record.to_resource()

    async def update(
        self,
        *,
        actor: AuthenticatedActor,
        draft_id: str,
        request: UpdateConfigurationDraftRequest,
        idempotency_key: str,
        if_match: str | None,
        attempt: AttemptContext | None = None,
    ) -> ConfigurationDraft:
        identity = request_identity(idempotency_key, request)
        async with transaction(self._sessions) as session:
            _, record = await load_owned_draft(session, actor=actor, draft_id=draft_id, write=True)
            await require_attempt(session, record=record, attempt=attempt, now=self._clock())
            replay = await load_replay(
                session,
                actor=actor,
                operation="configuration.draft.update",
                scope_id=draft_id,
                identity=identity,
                now=self._clock(),
            )
            if replay is not None:
                return replay.restore(ConfigurationDraft)
            require_open(record, expected_version=request.expected_version, if_match=if_match)
            if request.expected_digest is not None and record.content_digest != request.expected_digest:
                raise failure("configuration_draft_conflict", "The expected candidate digest changed.")
            current = record.to_resource()
        candidate = edit_config(current.config, request.operations) if request.operations else current.config
        metadata = current.creation_metadata
        if "creation_metadata" in request.model_fields_set:
            if current.mode != "create":
                raise failure("configuration_metadata_invalid", "Only create drafts can change creation metadata.")
            metadata = request.creation_metadata
        if candidate is None:
            raise failure("configuration_uninitialized", "Initialize a complete configuration before saving.")
        if attempt is not None:
            async with short_session(self._sessions) as session:
                await authorize_candidate_snapshot(
                    session,
                    actor=actor,
                    config=candidate,
                    target_agent_id=current.target_agent_id,
                    snapshot=attempt.authorization.snapshot,
                )
        try:
            prepared = await self._resolver.prepare(
                actor=actor,
                organization_id=current.organization_id,
                workspace_id=current.workspace_id,
                agent_id=current.target_agent_id or new_agent_id(),
                config=candidate,
                creation=current.mode == "create",
            )
        except Exception as error:
            raise resolution_error(error) from error
        async with transaction(self._sessions) as session:
            _, record = await load_owned_draft(session, actor=actor, draft_id=draft_id, write=True, lock=True)
            await require_attempt(session, record=record, attempt=attempt, now=self._clock())
            if attempt is not None:
                await authorize_candidate_snapshot(
                    session,
                    actor=actor,
                    config=candidate,
                    target_agent_id=current.target_agent_id,
                    snapshot=attempt.authorization.snapshot,
                )
            replay = await load_replay(
                session,
                actor=actor,
                operation="configuration.draft.update",
                scope_id=draft_id,
                identity=identity,
                now=self._clock(),
            )
            if replay is not None:
                return replay.restore(ConfigurationDraft)
            require_open(record, expected_version=request.expected_version, if_match=if_match)
            try:
                resolved = await self._resolver.freeze_in_transaction(session, prepared=prepared)
            except Exception as error:
                raise resolution_error(error) from error
            digest = candidate_digest(candidate, metadata)
            if digest != record.content_digest:
                record.version += 1
                record.evidence_refs = []
                record.config = candidate.model_dump(mode="json", by_alias=True)
                record.creation_metadata = None if metadata is None else metadata.model_dump(mode="json")
                record.content_digest = digest
            record.latest_validation = ConfigurationValidation(
                draft_version=record.version,
                content_digest=digest,
                dependency_digest=await dependency_digest(session, prepared=prepared, resolved=resolved),
                checked_at=self._clock(),
            ).model_dump(mode="json")
            record.updated_at = next_updated_at(record.updated_at, self._clock())
            result = record.to_resource()
            session.add(
                evidence_record(
                    actor=actor,
                    organization_id=record.organization_id,
                    workspace_id=record.workspace_id,
                    operation="configuration.draft.update",
                    scope_id=draft_id,
                    identity=identity,
                    result_kind="configuration_draft",
                    result_ref=draft_id,
                    now=self._clock(),
                    response=result,
                )
            )
            audit_draft(session, actor=actor, record=record, action="configuration.draft.update", now=self._clock())
            await session.flush()
        logger.info("configuration_draft_saved", extra={"draft_id": draft_id, "draft_version": result.version})
        return result

    async def rebase(
        self,
        *,
        actor: AuthenticatedActor,
        draft_id: str,
        request: RebaseDraftRequest,
        idempotency_key: str,
        if_match: str,
    ) -> ConfigurationDraft:
        from a13n_service.agents.persistence import lock_agent

        identity = request_identity(idempotency_key, request)
        async with transaction(self._sessions) as session:
            _, record = await load_owned_draft(session, actor=actor, draft_id=draft_id, write=True)
            replay = await load_replay(
                session,
                actor=actor,
                operation="configuration.draft.rebase",
                scope_id=draft_id,
                identity=identity,
                now=self._clock(),
            )
            if replay is not None:
                return replay.restore(ConfigurationDraft)
            require_open(record, expected_version=request.expected_version, if_match=if_match)
            current = record.to_resource()
        if current.mode != "update" or current.target_agent_id is None:
            raise failure("configuration_rebase_invalid", "Only update drafts have a target baseline to rebase.")
        try:
            prepared = await self._resolver.prepare(
                actor=actor,
                organization_id=current.organization_id,
                workspace_id=current.workspace_id,
                agent_id=current.target_agent_id,
                config=request.config,
            )
        except Exception as error:
            raise resolution_error(error) from error
        async with transaction(self._sessions) as session:
            _, record = await load_owned_draft(session, actor=actor, draft_id=draft_id, write=True, lock=True)
            replay = await load_replay(
                session,
                actor=actor,
                operation="configuration.draft.rebase",
                scope_id=draft_id,
                identity=identity,
                now=self._clock(),
            )
            if replay is not None:
                return replay.restore(ConfigurationDraft)
            require_open(record, expected_version=request.expected_version, if_match=if_match)
            target = await lock_agent(session, record.organization_id, record.workspace_id, current.target_agent_id)
            if target.version != request.expected_target_version:
                raise failure("configuration_target_conflict", "The target changed while preparing the rebase.")
            try:
                resolved = await self._resolver.freeze_in_transaction(session, prepared=prepared)
            except Exception as error:
                raise resolution_error(error) from error
            record.config = request.config.model_dump(mode="json", by_alias=True)
            record.content_digest = candidate_digest(request.config, current.creation_metadata)
            record.base_agent_revision_id = target.current_revision_id
            record.base_agent_version = target.version
            record.version += 1
            record.evidence_refs = []
            record.updated_at = next_updated_at(record.updated_at, self._clock())
            record.latest_validation = ConfigurationValidation(
                draft_version=record.version,
                content_digest=record.content_digest,
                dependency_digest=await dependency_digest(session, prepared=prepared, resolved=resolved),
                checked_at=self._clock(),
            ).model_dump(mode="json")
            result = record.to_resource()
            session.add(
                evidence_record(
                    actor=actor,
                    organization_id=record.organization_id,
                    workspace_id=record.workspace_id,
                    operation="configuration.draft.rebase",
                    scope_id=draft_id,
                    identity=identity,
                    result_kind="configuration_draft",
                    result_ref=draft_id,
                    now=self._clock(),
                    response=result,
                )
            )
            audit_draft(session, actor=actor, record=record, action="configuration.draft.rebase", now=self._clock())
            await session.flush()
            return result

    async def discard(
        self,
        *,
        actor: AuthenticatedActor,
        draft_id: str,
        request: DiscardDraftRequest,
        idempotency_key: str,
        if_match: str,
    ) -> ConfigurationDraft:
        identity = request_identity(idempotency_key, request)
        async with transaction(self._sessions) as session:
            _, record = await load_owned_draft(session, actor=actor, draft_id=draft_id, write=True, lock=True)
            replay = await load_replay(
                session,
                actor=actor,
                operation="configuration.draft.discard",
                scope_id=draft_id,
                identity=identity,
                now=self._clock(),
            )
            if replay is not None:
                return replay.restore(ConfigurationDraft)
            require_open(record, expected_version=request.expected_version, if_match=if_match)
            record.status, record.terminal_reason = "discarded", "user_discarded"
            record.updated_at = next_updated_at(record.updated_at, self._clock())
            result = record.to_resource()
            session.add(
                evidence_record(
                    actor=actor,
                    organization_id=record.organization_id,
                    workspace_id=record.workspace_id,
                    operation="configuration.draft.discard",
                    scope_id=draft_id,
                    identity=identity,
                    result_kind="configuration_draft",
                    result_ref=draft_id,
                    now=self._clock(),
                    response=result,
                )
            )
            audit_draft(session, actor=actor, record=record, action="configuration.draft.discard", now=self._clock())
            await session.flush()
            return result


async def require_attempt(
    session: AsyncSession, *, record: ConfigurationDraftRecord, attempt: AttemptContext | None, now
) -> None:
    if attempt is None:
        return
    run, _, _ = await lock_attempt_authority(session, attempt, now)
    context = run.configuration_context
    conversation = await session.get(SessionRecord, record.session_id)
    if (
        context is None
        or context.get("draft_id") != record.id
        or run.session_id != record.session_id
        or conversation is None
        or conversation.configuration_owner_user_id != run.authority_principal_id
    ):
        raise failure("configuration_binding_invalid", "The Run is not bound to this draft.")
    await authorize_execution(
        session,
        principal=run.to_resource().authority_principal,
        organization_id=record.organization_id,
        workspace_id=record.workspace_id,
        agent_id=run.agent_id,
        context=ConfigurationRunContext.model_validate(context),
        snapshot=attempt.authorization.snapshot,
    )


def audit_draft(
    session: AsyncSession, *, actor: AuthenticatedActor, record: ConfigurationDraftRecord, action: str, now
) -> None:
    session.add(
        security_audit_record(
            audit_id=new_object_id("audit"),
            actor=actor,
            organization_id=record.organization_id,
            workspace_id=record.workspace_id,
            action=action,
            resource_type="configuration_draft",
            resource_id=record.id,
            outcome="success",
            occurred_at=now,
            details={"draft_version": record.version},
        )
    )
