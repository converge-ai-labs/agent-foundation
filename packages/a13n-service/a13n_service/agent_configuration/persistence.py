"""Short-transaction ownership, concurrency and source helpers for drafts."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.agents.models import AgentRecord, AgentRevisionRecord
from a13n_service.application_errors import ApplicationError, ErrorCategory
from a13n_service.etags import etag_matches, resource_etag
from a13n_service.iam import AuthenticatedActor, AuthorizationError
from a13n_service.interactions.models import SessionRecord

from .authorization import authorize_session
from .domain import ConfigurationDraft, candidate_digest
from .models import ConfigurationDraftRecord
from .requests import SourceSelection


def failure(code: str, message: str, *, category: ErrorCategory = ErrorCategory.conflict) -> ApplicationError:
    return ApplicationError(code, message, category=category)


def not_found() -> ApplicationError:
    return failure(
        "configuration_not_found", "The configuration resource was not found.", category=ErrorCategory.not_found
    )


def require_open(record: ConfigurationDraftRecord, *, expected_version: int, if_match: str | None = None) -> None:
    if record.status != "open":
        raise failure("configuration_draft_terminal", "The draft is no longer editable.")
    if record.version != expected_version:
        raise failure("configuration_version_conflict", "The draft has changed; review its current version.")
    if if_match is not None and not etag_matches(if_match, resource_etag(record.id, record.updated_at)):
        raise failure(
            "precondition_failed", "The draft representation has changed.", category=ErrorCategory.stale_version
        )


async def load_owned_draft(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    draft_id: str,
    write: bool,
    lock: bool = False,
) -> tuple[SessionRecord, ConfigurationDraftRecord]:
    """Lock order is Session, draft, then the business target; no Thread owns a draft."""
    scope = await session.scalar(
        select(ConfigurationDraftRecord.session_id).where(ConfigurationDraftRecord.id == draft_id)
    )
    if scope is None:
        raise not_found()
    try:
        conversation = await authorize_session(session, actor=actor, session_id=scope, write=write, lock=lock)
    except AuthorizationError as error:
        raise not_found() from error
    query = select(ConfigurationDraftRecord).where(
        ConfigurationDraftRecord.id == conversation.configuration_draft_id,
        ConfigurationDraftRecord.id == draft_id,
        ConfigurationDraftRecord.workspace_id == actor.workspace_id,
    )
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    record = await session.scalar(query)
    if record is None:
        raise not_found()
    return conversation, record


async def create_draft(
    session: AsyncSession,
    *,
    conversation: SessionRecord,
    target_id: str | None,
    source: SourceSelection | None,
    now: datetime,
) -> ConfigurationDraftRecord:
    selection = source or SourceSelection(selector="empty" if target_id is None else "current")
    target = None if target_id is None else await session.get(AgentRecord, target_id)
    revision = None
    if target_id is None:
        if selection.selector != "empty":
            raise failure("configuration_source_invalid", "A create draft starts from an empty source.")
    else:
        if target is None or target.system_purpose is not None or target.workspace_id != conversation.workspace_id:
            raise not_found()
        if selection.selector == "empty":
            raise failure("configuration_source_invalid", "An update draft requires a retained target Revision.")
        revision = await session.scalar(
            select(AgentRevisionRecord).where(
                AgentRevisionRecord.id == (selection.revision_id or target.default_revision_id),
                AgentRevisionRecord.agent_id == target.id,
                AgentRevisionRecord.workspace_id == conversation.workspace_id,
            )
        )
        if revision is None:
            raise not_found()
    config = None if revision is None else revision.to_resource().config
    assert conversation.configuration_draft_id is not None
    value = ConfigurationDraft(
        id=conversation.configuration_draft_id,
        organization_id=conversation.organization_id,
        workspace_id=conversation.workspace_id,
        session_id=conversation.id,
        mode="create" if target is None else "update",
        target_agent_id=target_id,
        source_selector=selection.selector,
        source_agent_revision_id=None if revision is None else revision.id,
        source_agent_revision_version=None if revision is None else revision.version,
        base_agent_revision_id=None if revision is None else revision.id,
        base_agent_etag=None if target is None else resource_etag(target.id, target.updated_at),
        version=1,
        config=config,
        content_digest=candidate_digest(config, None),
        status="open",
        created_at=now,
        updated_at=now,
    )
    record = ConfigurationDraftRecord(
        **value.model_dump(mode="json", exclude={"created_at", "updated_at"}), created_at=now, updated_at=now
    )
    session.add(record)
    return record
