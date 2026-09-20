"""Atomic authenticated application of a reviewed draft to its business Agent."""

from __future__ import annotations

from a13n_logging import get_logger
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.agents.domain import new_agent_id, new_agent_revision_id
from a13n_service.agents.models import AgentRecord
from a13n_service.agents.persistence import (
    lock_agent,
    lock_revision,
    new_agent_audit,
    new_revision,
    next_revision_number,
    require_custom_mutable,
    touch_agent,
)
from a13n_service.agents.resolution import AgentResolver, resolution_error
from a13n_service.collection_cursors import encode_time_cursor
from a13n_service.durable_operations.idempotency import IdempotencyIdentity
from a13n_service.durable_operations.models import OutboxRecord
from a13n_service.durable_operations.requests import evidence_record, load_replay, request_identity
from a13n_service.etags import resource_etag
from a13n_service.iam import AuthenticatedActor
from a13n_service.ids import new_object_id
from a13n_service.resource_keys import insert_with_key
from a13n_service.storage import short_session, transaction
from a13n_service.temporal import Clock, next_updated_at, utc_now

from .context import StrictModel
from .conversations import cursor_boundary
from .domain import ConfigurationApplicationReceipt
from .drafts import audit_draft
from .models import ConfigurationApplicationRecord, ConfigurationDraftRecord
from .persistence import failure, load_owned_draft, require_open
from .requests import ApplyDraftRequest
from .validation import dependency_digest

logger = get_logger(__name__)


class ConfigurationApplicationCollection(StrictModel):
    items: tuple[ConfigurationApplicationReceipt, ...]
    next_cursor: str | None


class ConfigurationApplication:
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], resolver: AgentResolver, *, clock: Clock = utc_now
    ) -> None:
        self._sessions, self._resolver, self._clock = sessions, resolver, clock

    async def list_applications(
        self, *, actor: AuthenticatedActor, draft_id: str, limit: int, cursor: str | None
    ) -> ConfigurationApplicationCollection:
        scope: dict[str, object] = {"draft_id": draft_id, "owner": actor.principal.principal_id}
        after = cursor_boundary(cursor, scope=scope, prefix="capply_")
        async with short_session(self._sessions) as session:
            await load_owned_draft(session, actor=actor, draft_id=draft_id, write=False)
            query = select(ConfigurationApplicationRecord).where(ConfigurationApplicationRecord.draft_id == draft_id)
            if after is not None:
                query = query.where(
                    or_(
                        ConfigurationApplicationRecord.created_at < after[0],
                        and_(
                            ConfigurationApplicationRecord.created_at == after[0],
                            ConfigurationApplicationRecord.id < after[1],
                        ),
                    )
                )
            rows = tuple(
                await session.scalars(
                    query.order_by(
                        ConfigurationApplicationRecord.created_at.desc(), ConfigurationApplicationRecord.id.desc()
                    ).limit(limit + 1)
                )
            )
            page = rows[:limit]
            return ConfigurationApplicationCollection(
                items=tuple(row.to_resource() for row in page),
                next_cursor=encode_time_cursor(page[-1].created_at, page[-1].id, scope=scope)
                if len(rows) > limit
                else None,
            )

    async def apply(
        self,
        *,
        actor: AuthenticatedActor,
        draft_id: str,
        request: ApplyDraftRequest,
        idempotency_key: str,
        if_match: str,
    ) -> ConfigurationApplicationReceipt:
        identity = request_identity(idempotency_key)
        async with transaction(self._sessions) as session:
            _, record = await load_owned_draft(session, actor=actor, draft_id=draft_id, write=True, lock=True)
            retained = await application_replay(
                session, actor=actor, record=record, request=request, identity=identity, now=self._clock()
            )
            if retained is not None:
                return retained
            require_review(record, request=request, if_match=if_match)
            candidate = record.to_resource()
        assert candidate.config is not None
        agent_id = candidate.target_agent_id or new_agent_id()
        try:
            prepared = await self._resolver.prepare(
                actor=actor,
                organization_id=candidate.organization_id,
                workspace_id=candidate.workspace_id,
                agent_id=agent_id,
                config=candidate.config,
                creation=candidate.mode == "create",
            )
        except Exception as error:
            raise resolution_error(error) from error
        async with transaction(self._sessions) as session:
            conversation, record = await load_owned_draft(
                session, actor=actor, draft_id=draft_id, write=True, lock=True
            )
            retained = await application_replay(
                session, actor=actor, record=record, request=request, identity=identity, now=self._clock()
            )
            if retained is not None:
                return retained
            require_review(record, request=request, if_match=if_match)
            now = next_updated_at(record.updated_at, self._clock())
            if record.mode == "create":
                assert candidate.creation_metadata is not None
                target = AgentRecord(
                    id=agent_id,
                    organization_id=record.organization_id,
                    workspace_id=record.workspace_id,
                    source="custom",
                    name=candidate.creation_metadata.name,
                    description=candidate.creation_metadata.description,
                    labels={},
                    default_revision_id=new_agent_revision_id(),
                    enabled=True,
                    archived_at=None,
                    created_by_type=actor.principal.principal_type.value,
                    created_by_id=actor.principal.principal_id,
                    updated_by_type=actor.principal.principal_type.value,
                    updated_by_id=actor.principal.principal_id,
                    created_at=now,
                    updated_at=now,
                )
            else:
                target = await lock_agent(session, record.organization_id, record.workspace_id, agent_id)
                require_custom_mutable(target)
                if resource_etag(target.id, target.updated_at) != record.base_agent_etag:
                    raise failure(
                        "configuration_target_conflict",
                        "The target has changed; review and explicitly rebase the candidate.",
                    )
            try:
                resolved = await self._resolver.freeze_in_transaction(session, prepared=prepared)
            except Exception as error:
                raise resolution_error(error) from error
            if await dependency_digest(session, prepared=prepared, resolved=resolved) != request.dependency_digest:
                raise failure(
                    "configuration_dependencies_changed",
                    "Dependencies changed after review; save and review a fresh validation.",
                )
            no_change = False
            previous_default_revision_id = None if record.mode == "create" else target.default_revision_id
            if record.mode == "create":
                await insert_with_key(session, target, prefix="agent")
            assert target.default_revision_id is not None
            revision = new_revision(
                target,
                revision_id=target.default_revision_id if record.mode == "create" else new_agent_revision_id(),
                version=1 if record.mode == "create" else await next_revision_number(session, target.id),
                config=candidate.config,
                resolved=resolved,
                source_revision_id=record.source_agent_revision_id,
                change_summary=(
                    request.change_summary
                    if "change_summary" in request.model_fields_set
                    else candidate.suggested_change_summary
                ),
                actor=actor,
                now=now,
            )
            if record.mode == "update":
                current = await lock_revision(
                    session,
                    organization_id=record.organization_id,
                    workspace_id=record.workspace_id,
                    agent_id=target.id,
                    revision_id=target.default_revision_id,
                )
                if current.content_digest == revision.content_digest:
                    revision, no_change = current, True
            if not no_change:
                session.add(revision)
                target.default_revision_id = revision.id
                touch_agent(target, actor=actor, now=now)
            # Persist the revision before the draft receipt references it.
            await session.flush()
            receipt = ConfigurationApplicationReceipt(
                draft_id=record.id,
                reviewed_version=record.version,
                reviewed_digest=record.content_digest,
                reviewed_mode=candidate.mode,
                reviewed_target_agent_id=candidate.target_agent_id,
                reviewed_base_agent_revision_id=candidate.base_agent_revision_id,
                reviewed_creation_metadata=candidate.creation_metadata,
                agent_id=target.id,
                agent_revision_id=revision.id,
                agent_revision_version=revision.version,
                applied_by_user_id=actor.principal.principal_id,
                applied_at=now,
                no_change=no_change,
                verification_acknowledgement=request.verification_acknowledgement,
                verification_run_ids=request.verification_run_ids,
            )
            application = ConfigurationApplicationRecord(
                id=new_object_id("capply"),
                draft_id=record.id,
                organization_id=record.organization_id,
                workspace_id=record.workspace_id,
                reviewed_version=record.version,
                agent_revision_id=revision.id,
                key_hash=identity.key_digest,
                reviewed_digest=receipt.reviewed_digest,
                reviewed_mode=receipt.reviewed_mode,
                reviewed_target_agent_id=receipt.reviewed_target_agent_id,
                reviewed_base_agent_revision_id=receipt.reviewed_base_agent_revision_id,
                reviewed_creation_metadata=None
                if receipt.reviewed_creation_metadata is None
                else receipt.reviewed_creation_metadata.model_dump(mode="json"),
                agent_id=receipt.agent_id,
                agent_revision_version=receipt.agent_revision_version,
                applied_by_user_id=receipt.applied_by_user_id,
                no_change=receipt.no_change,
                verification_acknowledgement=None
                if receipt.verification_acknowledgement is None
                else receipt.verification_acknowledgement.model_dump(mode="json"),
                verification_run_ids=list(receipt.verification_run_ids),
                created_at=now,
            )
            session.add(application)
            record.mode = "update"
            record.target_agent_id = target.id
            record.base_agent_revision_id = revision.id
            record.base_agent_etag = resource_etag(target.id, target.updated_at)
            record.version += 1
            record.latest_validation = None
            record.evidence_refs = []
            record.updated_at = next_updated_at(record.updated_at, now)
            conversation.updated_at = next_updated_at(conversation.updated_at, now)
            audit_draft(session, actor=actor, record=record, action="configuration.draft.apply", now=now)
            session.add(
                OutboxRecord(
                    id=new_object_id("out"),
                    source_kind="configuration_application",
                    source_id=application.id,
                    destination_kind="native.notification",
                    destination_ref=application.id,
                    status="pending",
                    available_at=now,
                    claim_generation=0,
                    lease_expires_at=None,
                    attempt_count=0,
                    created_at=now,
                    updated_at=now,
                    published_at=None,
                    dead_lettered_at=None,
                    last_error_code=None,
                )
            )
            if not no_change:
                session.add(
                    new_agent_audit(
                        actor=actor,
                        organization_id=record.organization_id,
                        workspace_id=record.workspace_id,
                        action="agent.configuration.apply",
                        agent_id=target.id,
                        now=now,
                        details={"from_revision_id": previous_default_revision_id, "to_revision_id": revision.id},
                    )
                )
            await session.flush()
        logger.info(
            "configuration_draft_applied",
            extra={
                "draft_id": draft_id,
                "agent_id": receipt.agent_id,
                "agent_revision_id": receipt.agent_revision_id,
                "no_change": receipt.no_change,
            },
        )
        return receipt


async def application_replay(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    record: ConfigurationDraftRecord,
    request: ApplyDraftRequest,
    identity: IdempotencyIdentity,
    now,
) -> ConfigurationApplicationReceipt | None:
    application = await session.scalar(
        select(ConfigurationApplicationRecord).where(
            ConfigurationApplicationRecord.draft_id == record.id,
            ConfigurationApplicationRecord.key_hash == identity.key_digest,
        )
    )
    if application is not None:
        return application.to_resource()
    reference = await load_replay(
        session, actor=actor, operation="configuration.draft.apply", scope_id=record.id, identity=identity, now=now
    )
    if reference is not None:
        application = await session.get(ConfigurationApplicationRecord, reference.result_ref)
        return None if application is None else application.to_resource()
    # A reviewed version has one application, even when a caller supplies a new key.
    application = await session.scalar(
        select(ConfigurationApplicationRecord).where(
            ConfigurationApplicationRecord.draft_id == record.id,
            ConfigurationApplicationRecord.reviewed_version == request.expected_version,
        )
    )
    if application is None:
        return None
    session.add(
        evidence_record(
            actor=actor,
            organization_id=record.organization_id,
            workspace_id=record.workspace_id,
            operation="configuration.draft.apply",
            scope_id=record.id,
            identity=identity,
            result_kind="configuration_application",
            result_ref=application.id,
            now=now,
        )
    )
    return application.to_resource()


def require_review(record: ConfigurationDraftRecord, *, request: ApplyDraftRequest, if_match: str) -> None:
    require_open(record, expected_version=request.expected_version, if_match=if_match)
    if record.content_digest != request.content_digest:
        raise failure("configuration_review_conflict", "The reviewed candidate digest does not match.")
    if record.config is None or (record.mode == "create" and record.creation_metadata is None):
        raise failure(
            "configuration_uninitialized", "Application requires a complete configuration and creation metadata."
        )
    validation = record.latest_validation
    if (
        validation is None
        or validation.get("content_digest") != record.content_digest
        or validation.get("draft_version") != record.version
        or validation.get("dependency_digest") != request.dependency_digest
    ):
        raise failure("configuration_validation_required", "Save and review current validation before application.")
    if request.verification_run_ids:
        raise failure(
            "configuration_verification_unsupported",
            "These Runs do not have accepted candidate verification provenance.",
        )
    if request.verification_acknowledgement is None:
        raise failure(
            "configuration_acknowledgement_required",
            "Explicitly acknowledge that this candidate is unverified, with a reason.",
        )
