"""Publish committed application hints through the existing native notifications."""

from datetime import datetime, timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.durable_operations.models import OutboxRecord
from a13n_service.durable_operations.outbox import claim_outbox, complete_outbox
from a13n_service.iam import AuthenticatedActor, WorkspaceAction
from a13n_service.interactions.access import configuration_visibility
from a13n_service.interactions.models import SessionRecord, ThreadRecord
from a13n_service.storage import short_session, transaction
from a13n_service.temporal import assume_utc, utc_now

from .models import ConfigurationApplicationRecord, ConfigurationDraftRecord


async def read_application_hints(
    sessions: async_sessionmaker[AsyncSession],
    *,
    actor: AuthenticatedActor,
    workspace_id: str,
    organization_id: str,
    thread_id: str | None,
    actions: frozenset[WorkspaceAction],
    after: tuple[datetime, str],
    limit: int,
) -> tuple[tuple[SessionRecord, tuple[datetime, str]], ...]:
    """The receipt remains authoritative; every subscriber has its own hint cursor."""
    async with short_session(sessions) as database:
        visibility = and_(
            *[
                await configuration_visibility(
                    database,
                    actor=actor,
                    workspace_id=workspace_id,
                    organization_id=organization_id,
                    action=action,
                )
                for action in actions
            ]
        )
        query = (
            select(SessionRecord, OutboxRecord.created_at, OutboxRecord.id)
            .select_from(ConfigurationApplicationRecord)
            .join(
                OutboxRecord,
                OutboxRecord.source_id == ConfigurationApplicationRecord.id,
            )
            .join(ConfigurationDraftRecord, ConfigurationDraftRecord.id == ConfigurationApplicationRecord.draft_id)
            .join(SessionRecord, SessionRecord.id == ConfigurationDraftRecord.session_id)
            .where(
                OutboxRecord.source_kind == "configuration_application",
                OutboxRecord.destination_kind == "native.notification",
                SessionRecord.workspace_id == workspace_id,
                visibility,
                or_(
                    OutboxRecord.created_at > after[0],
                    and_(OutboxRecord.created_at == after[0], OutboxRecord.id > after[1]),
                ),
            )
        )
        if thread_id is not None:
            query = query.where(
                select(ThreadRecord.id)
                .where(ThreadRecord.id == thread_id, ThreadRecord.session_id == SessionRecord.id)
                .exists()
            )
        rows = (await database.execute(query.order_by(OutboxRecord.created_at, OutboxRecord.id).limit(limit))).all()
        return tuple((row, (assume_utc(created_at), outbox_id)) for row, created_at, outbox_id in rows)


async def acknowledge_application_hint(sessions: async_sessionmaker[AsyncSession], *, application_id: str) -> None:
    """Record best-effort publication after send; other subscribers still see the intent."""
    async with transaction(sessions) as database:
        now = utc_now()
        claims = await claim_outbox(
            database,
            source_kind="configuration_application",
            destination_kind="native.notification",
            destination_ref=application_id,
            now=now,
            lease_duration=timedelta(seconds=30),
            limit=1,
        )
        for claim in claims:
            await complete_outbox(database, claim, completed_at=now)
