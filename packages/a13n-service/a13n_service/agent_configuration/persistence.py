"""Short-transaction ownership, concurrency and source helpers for drafts."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.agents.models import AgentRecord, AgentRevisionRecord
from a13n_service.application_errors import ApplicationError, ErrorCategory
from a13n_service.etags import etag_matches, resource_etag
from a13n_service.iam import AuthenticatedActor, AuthorizationError
from a13n_service.interactions.models import SessionRecord, ThreadRecord

from .authorization import authorize_session
from .domain import ConfigurationDraft, candidate_digest, new_draft_id
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
) -> tuple[SessionRecord, ThreadRecord, ConfigurationDraftRecord]:
    """Lock order is Session, Thread, draft; callers lock the target only afterwards."""
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
        ConfigurationDraftRecord.id == draft_id,
        ConfigurationDraftRecord.owner_user_id == actor.principal.principal_id,
        ConfigurationDraftRecord.workspace_id == actor.workspace_id,
    )
    record = await session.scalar(query)
    if record is None:
        raise not_found()
    thread_query = select(ThreadRecord).where(
        ThreadRecord.id == record.thread_id, ThreadRecord.session_id == conversation.id
    )
    thread = await session.scalar(thread_query.with_for_update() if lock else thread_query)
    if thread is None:
        raise not_found()
    if lock:
        record = await session.scalar(query.with_for_update().execution_options(populate_existing=True))
        assert record is not None
    return conversation, thread, record


async def create_draft(
    session: AsyncSession,
    *,
    conversation: SessionRecord,
    thread: ThreadRecord,
    source: SourceSelection | None,
    now: datetime,
    predecessor: ConfigurationDraftRecord | None = None,
    persist: bool = True,
) -> ConfigurationDraftRecord:
    target_id = conversation.configuration_target_agent_id
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
                AgentRevisionRecord.id == (selection.revision_id or target.current_revision_id),
                AgentRevisionRecord.agent_id == target.id,
                AgentRevisionRecord.workspace_id == conversation.workspace_id,
            )
        )
        if revision is None:
            raise not_found()
    config = None if revision is None else revision.to_resource().config
    assert conversation.configuration_owner_user_id is not None
    value = ConfigurationDraft(
        id=new_draft_id(),
        organization_id=conversation.organization_id,
        workspace_id=conversation.workspace_id,
        owner_user_id=conversation.configuration_owner_user_id,
        session_id=conversation.id,
        thread_id=thread.id,
        predecessor_draft_id=None if predecessor is None else predecessor.id,
        mode="create" if target is None else "update",
        target_agent_id=target_id,
        source_selector=selection.selector,
        source_agent_revision_id=None if revision is None else revision.id,
        source_agent_revision_version=None if revision is None else revision.version,
        base_agent_revision_id=None if revision is None else revision.id,
        base_agent_version=None if target is None else target.version,
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
    if persist:
        session.add(record)
        thread.configuration_active_draft_id = record.id
        thread.configuration_latest_draft_id = record.id
    return record
