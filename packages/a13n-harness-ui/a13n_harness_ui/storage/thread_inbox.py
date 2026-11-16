"""Pending Thread inputs and atomic checkpoint consumption."""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from a13n_harness.envelopes import Envelope, Sender
from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_harness_ui.bot_models import BOT_THREAD_ID, BotInboxInput, BotState
from a13n_harness_ui.configuration.models import BotConfiguration
from a13n_harness_ui.errors import ThreadError

from .database import DatabaseSessions, short_session, transaction
from .models import (
    BotStateRecord,
    ConnectorConfigurationRecord,
    DeletedThreadRecord,
    ThreadInboxRecord,
    ToolExecutionHeadRecord,
)


@dataclass(frozen=True)
class InboxEntry:
    envelope: Envelope
    created_at: datetime
    dedupe_key: str | None = None


def entry(row: ThreadInboxRecord) -> InboxEntry:
    return InboxEntry(
        Envelope.model_validate(
            dict(
                id=row.id,
                kind=row.kind,
                sender=json.loads(row.sender),
                execution=None if row.execution is None else json.loads(row.execution),
                text=row.text,
                delivery=row.delivery,
            )
        ),
        row.created_at,
        row.dedupe_key,
    )


def sender_json(sender: Sender) -> str:
    return sender.model_dump_json(exclude_none=True)


def bot_envelope(item: BotInboxInput) -> Envelope:
    sender = (
        Sender(kind="connector", name=item.connector_id)
        if item.source == "connector"
        else Sender(kind="agent", thread_id=item.source_thread_id, name=item.source_thread_title)
        if item.source == "thread"
        else Sender(kind="system", name="heartbeat")
    )
    return Envelope(
        id=item.input_id,
        kind="agent_message" if item.source == "thread" else "event",
        sender=sender,
        text=item.model_dump_json(),
    )


def bot_automatic(envelope: Envelope) -> bool:
    return envelope.execution is None and (
        envelope.sender.kind == "connector" or envelope.sender.name == "heartbeat" or envelope.kind == "agent_message"
    )


async def append(
    session: AsyncSession,
    thread_id: str,
    envelope: Envelope,
    *,
    dedupe_key: str | None = None,
    created_at: datetime | None = None,
) -> str:
    """Join the producer's fact transaction; callbacks only schedule dispatch."""
    if await session.get(DeletedThreadRecord, thread_id) is not None:
        raise ThreadError("The target Thread was deleted.", code="thread_missing")
    await session.execute(
        insert(ThreadInboxRecord)
        .values(
            id=envelope.id,
            thread_id=thread_id,
            kind=envelope.kind,
            sender=sender_json(envelope.sender),
            execution=None if envelope.execution is None else envelope.execution.model_dump_json(),
            text=envelope.text,
            delivery=envelope.delivery,
            dedupe_key=dedupe_key,
            created_at=created_at or datetime.now(UTC),
        )
        .on_conflict_do_nothing()
    )
    if dedupe_key is not None:
        return (
            await session.scalars(
                select(ThreadInboxRecord.id).where(
                    ThreadInboxRecord.thread_id == thread_id, ThreadInboxRecord.dedupe_key == dedupe_key
                )
            )
        ).one()
    return envelope.id


async def consume(session: AsyncSession, thread_id: str, ids: Sequence[str]) -> None:
    """Delete only inputs durably carried by the checkpoint selected in this transaction."""
    if not ids:
        return
    rows = (
        await session.scalars(
            select(ThreadInboxRecord).where(ThreadInboxRecord.thread_id == thread_id, ThreadInboxRecord.id.in_(ids))
        )
    ).all()
    # A notice's consumption shortens retained output lifetime, not its execution state.
    from .tool_executions import consumed

    for row in rows:
        execution = entry(row).envelope.execution
        if execution is not None and execution.kind == "tool":
            head = await session.get(ToolExecutionHeadRecord, execution.execution_id)
            if head is not None and head.thread_id == thread_id:
                consumed(head, datetime.now(UTC))
        await session.delete(row)


class ThreadInboxRepository:
    def __init__(self, sessions: DatabaseSessions) -> None:
        self.sessions = sessions

    async def put(self, thread_id: str, envelope: Envelope) -> str:
        async with transaction(self.sessions) as session:
            return await append(session, thread_id, envelope)

    async def pending(self, thread_id: str, *, exclude: Sequence[str] = ()) -> tuple[InboxEntry, ...]:
        async with short_session(self.sessions) as session:
            rows = (
                await session.scalars(
                    select(ThreadInboxRecord)
                    .where(ThreadInboxRecord.thread_id == thread_id, ThreadInboxRecord.id.not_in(exclude))
                    .order_by(ThreadInboxRecord.created_at, ThreadInboxRecord.id)
                )
            ).all()
            return tuple(entry(row) for row in rows)

    async def threads(self, *, after: str = "", limit: int = 100) -> tuple[str, ...]:
        async with short_session(self.sessions) as session:
            return tuple(
                await session.scalars(
                    select(ThreadInboxRecord.thread_id)
                    .where(ThreadInboxRecord.thread_id > after)
                    .distinct()
                    .order_by(ThreadInboxRecord.thread_id)
                    .limit(limit)
                )
            )

    async def discard(self, thread_id: str, *, ids: Sequence[str] | None = None) -> None:
        async with transaction(self.sessions) as session:
            statement = delete(ThreadInboxRecord).where(ThreadInboxRecord.thread_id == thread_id)
            if ids is not None:
                statement = statement.where(ThreadInboxRecord.id.in_(ids))
            await session.execute(statement)

    async def bot_batch(self, inputs: tuple[InboxEntry, ...]) -> tuple[InboxEntry, ...]:
        """Heartbeat first; only automatic-source objects share the bounded Bot budget."""
        async with short_session(self.sessions) as session:
            enabled = {
                row.connector_id
                for row in await session.scalars(
                    select(ConnectorConfigurationRecord).where(
                        ConnectorConfigurationRecord.enabled.is_(True),
                        ConnectorConfigurationRecord.deliver_to_bot.is_(True),
                        ConnectorConfigurationRecord.deleted.is_(False),
                    )
                )
            }
        automatic = sorted(
            (item for item in inputs if bot_automatic(item.envelope)),
            key=lambda item: (item.envelope.sender.name != "heartbeat", item.created_at, item.envelope.id),
        )
        result: list[InboxEntry] = []
        size = count = 0
        for item in automatic:
            if item.envelope.sender.kind == "connector" and item.envelope.sender.name not in enabled:
                continue
            length = len(item.envelope.text.encode("utf-8"))
            if count == 32 or size + length > 60 * 1024:
                break
            result.append(item)
            size += length
            count += 1
        result.extend(item for item in inputs if not bot_automatic(item.envelope))
        return tuple(result)

    async def heartbeat(self, *, policy: BotConfiguration, now: datetime, text: str) -> None:
        async with transaction(self.sessions) as session:
            row = await session.get(BotStateRecord, 1)
            state = BotState() if row is None else BotState.model_validate_json(row.payload)
            next_at, interval = state.next_heartbeat_at, state.heartbeat_interval_seconds
            if not policy.heartbeat_enabled:
                await session.execute(
                    delete(ThreadInboxRecord).where(
                        ThreadInboxRecord.thread_id == BOT_THREAD_ID, ThreadInboxRecord.dedupe_key == "heartbeat"
                    )
                )
            if not policy.enabled or not policy.heartbeat_enabled:
                next_at, interval = None, None
            elif next_at is None or interval != policy.interval_seconds:
                next_at, interval = now + timedelta(seconds=policy.interval_seconds), policy.interval_seconds
            elif next_at <= now:
                item = BotInboxInput(
                    input_id=f"heartbeat-{uuid4().hex}",
                    source="heartbeat",
                    source_event_id=next_at.isoformat(),
                    event_type="heartbeat",
                    text=text,
                    created_at=now,
                )
                await append(session, BOT_THREAD_ID, bot_envelope(item), dedupe_key="heartbeat", created_at=now)
                next_at = now + timedelta(seconds=policy.interval_seconds)
            updated = state.model_copy(update={"next_heartbeat_at": next_at, "heartbeat_interval_seconds": interval})
            if updated != state:
                if row is None:
                    session.add(BotStateRecord(singleton_id=1, payload=updated.model_dump_json()))
                else:
                    row.payload = updated.model_dump_json()
