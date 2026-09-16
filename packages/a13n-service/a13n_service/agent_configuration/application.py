"""Atomic authenticated application of a reviewed draft to its business Agent."""

from __future__ import annotations

from a13n_logging import get_logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.agents.domain import new_agent_id, new_agent_revision_id
from a13n_service.agents.models import AgentRecord
from a13n_service.agents.persistence import (
    lock_agent,
    lock_revision,
    new_agent_audit,
    new_revision,
    require_custom_mutable,
    touch_agent,
)
from a13n_service.agents.resolution import AgentResolver, resolution_error
from a13n_service.durable_operations.models import OutboxRecord
from a13n_service.durable_operations.requests import evidence_record, load_replay, request_identity
from a13n_service.iam import AuthenticatedActor
from a13n_service.ids import new_object_id
from a13n_service.interactions.models import ThreadRecord
from a13n_service.resource_keys import insert_with_key
from a13n_service.storage import transaction
from a13n_service.temporal import Clock, next_updated_at, utc_now

from .context import ConfigurationApplicationReceipt
from .drafts import audit_draft
from .models import ConfigurationDraftRecord
from .persistence import failure, load_owned_draft, require_open
from .requests import ApplyDraftRequest
from .validation import dependency_digest

logger = get_logger(__name__)


class ConfigurationApplication:
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], resolver: AgentResolver, *, clock: Clock = utc_now
    ) -> None:
        self._sessions, self._resolver, self._clock = sessions, resolver, clock

    async def apply(
        self,
        *,
        actor: AuthenticatedActor,
        draft_id: str,
        request: ApplyDraftRequest,
        idempotency_key: str,
        if_match: str,
    ) -> ConfigurationApplicationReceipt:
        identity = request_identity(idempotency_key, request)
        async with transaction(self._sessions, sqlite_immediate=True) as session:
            _, _, record = await load_owned_draft(session, actor=actor, draft_id=draft_id, write=True)
            retained = retained_receipt(record, key_digest=identity.key_digest, request_digest=identity.request_digest)
            if retained is not None:
                return retained
            replay = await load_replay(
                session,
                actor=actor,
                operation="configuration.draft.apply",
                scope_id=draft_id,
                identity=identity,
                now=self._clock(),
            )
            if replay is not None:
                return replay.restore(ConfigurationApplicationReceipt)
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
        async with transaction(self._sessions, sqlite_immediate=True) as session:
            conversation, _, record = await load_owned_draft(
                session, actor=actor, draft_id=draft_id, write=True, lock=True
            )
            retained = retained_receipt(record, key_digest=identity.key_digest, request_digest=identity.request_digest)
            if retained is not None:
                return retained
            replay = await load_replay(
                session,
                actor=actor,
                operation="configuration.draft.apply",
                scope_id=draft_id,
                identity=identity,
                now=self._clock(),
            )
            if replay is not None:
                return replay.restore(ConfigurationApplicationReceipt)
            require_review(record, request=request, if_match=if_match)
            # The Session lock serializes every draft operation, including competing
            # create-mode Threads. Lock children before touching the business head.
            threads = tuple(
                await session.scalars(
                    select(ThreadRecord)
                    .where(ThreadRecord.session_id == conversation.id)
                    .order_by(ThreadRecord.id)
                    .with_for_update()
                )
            )
            siblings = tuple(
                await session.scalars(
                    select(ConfigurationDraftRecord)
                    .where(ConfigurationDraftRecord.session_id == conversation.id)
                    .order_by(ConfigurationDraftRecord.id)
                    .with_for_update()
                )
            )
            now = self._clock()
            if record.mode == "create":
                if conversation.configuration_target_agent_id is not None:
                    raise failure(
                        "configuration_target_resolved", "This configuration Session already created its target."
                    )
                assert candidate.creation_metadata is not None
                target = AgentRecord(
                    id=agent_id,
                    organization_id=record.organization_id,
                    workspace_id=record.workspace_id,
                    source="custom",
                    name=candidate.creation_metadata.name,
                    description=candidate.creation_metadata.description,
                    labels={},
                    version=1,
                    current_revision_id=new_agent_revision_id(),
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
                if target.version != record.base_agent_version or request.expected_target_version != target.version:
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
            if record.mode == "create":
                await insert_with_key(session, target, prefix="agent")
            revision = new_revision(
                target,
                revision_id=target.current_revision_id if record.mode == "create" else new_agent_revision_id(),
                version=1 if record.mode == "create" else target.version + 1,
                config=candidate.config,
                resolved=resolved,
                source_revision_id=record.source_agent_revision_id,
                actor=actor,
                now=now,
            )
            if record.mode == "update":
                current = await lock_revision(
                    session,
                    organization_id=record.organization_id,
                    workspace_id=record.workspace_id,
                    agent_id=target.id,
                    revision_id=target.current_revision_id,
                )
                if current.content_digest == revision.content_digest:
                    revision, no_change = current, True
            if not no_change:
                session.add(revision)
                target.version, target.current_revision_id = revision.version, revision.id
                touch_agent(target, actor=actor, now=now)
            # Persist the revision before the draft receipt references it.
            await session.flush()
            receipt = ConfigurationApplicationReceipt(
                draft_id=record.id,
                reviewed_version=record.version,
                reviewed_digest=record.content_digest,
                agent_id=target.id,
                agent_revision_id=revision.id,
                agent_version=revision.version,
                applied_by_user_id=actor.principal.principal_id,
                applied_at=now,
                no_change=no_change,
                verification_acknowledgement=request.verification_acknowledgement,
                verification_run_ids=request.verification_run_ids,
            )
            record.status = "applied"
            record.application_receipt = receipt.model_dump(mode="json")
            record.applied_agent_revision_id = revision.id
            record.application_key_hash, record.application_request_digest = (
                identity.key_digest,
                identity.request_digest,
            )
            record.updated_at = next_updated_at(record.updated_at, now)
            if record.mode == "create":
                conversation.configuration_target_agent_id = target.id
                for sibling in siblings:
                    if sibling.id != record.id and sibling.mode == "create" and sibling.status == "open":
                        sibling.status, sibling.terminal_reason = "discarded", "target_resolved"
                        sibling.updated_at = next_updated_at(sibling.updated_at, now)
            closed_ids = {item.id for item in siblings if item.status != "open"}
            for thread in threads:
                if thread.configuration_active_draft_id in closed_ids:
                    thread.configuration_active_draft_id = None
            conversation.updated_at = next_updated_at(conversation.updated_at, now)
            session.add(
                evidence_record(
                    actor=actor,
                    organization_id=record.organization_id,
                    workspace_id=record.workspace_id,
                    operation="configuration.draft.apply",
                    scope_id=draft_id,
                    identity=identity,
                    result_kind="configuration_application",
                    result_ref=draft_id,
                    now=now,
                    response=receipt,
                )
            )
            audit_draft(session, actor=actor, record=record, action="configuration.draft.apply", now=now)
            session.add(
                OutboxRecord(
                    id=new_object_id("out"),
                    source_kind="configuration_application",
                    source_id=record.id,
                    destination_kind="native.notification",
                    destination_ref=record.id,
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
            session.add(
                new_agent_audit(
                    actor=actor,
                    organization_id=record.organization_id,
                    workspace_id=record.workspace_id,
                    action="agent.configuration.apply",
                    agent_id=target.id,
                    now=now,
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


def retained_receipt(
    record: ConfigurationDraftRecord, *, key_digest: str, request_digest: str
) -> ConfigurationApplicationReceipt | None:
    if record.status != "applied":
        return None
    if record.application_key_hash != key_digest or record.application_request_digest != request_digest:
        raise failure("configuration_already_applied", "The draft was already applied; read its retained receipt.")
    return ConfigurationApplicationReceipt.model_validate(record.application_receipt)


def require_review(record: ConfigurationDraftRecord, *, request: ApplyDraftRequest, if_match: str) -> None:
    require_open(record, expected_version=request.expected_version, if_match=if_match)
    if record.content_digest != request.content_digest:
        raise failure("configuration_review_conflict", "The reviewed candidate digest does not match.")
    if record.config is None or (record.mode == "create" and record.creation_metadata is None):
        raise failure(
            "configuration_uninitialized", "Application requires a complete configuration and creation metadata."
        )
    if record.mode == "create" and request.expected_target_version is not None:
        raise failure("configuration_target_conflict", "A create draft has no target version.")
    if record.mode == "update" and request.expected_target_version != record.base_agent_version:
        raise failure("configuration_target_conflict", "Review the draft's exact target baseline before application.")
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
