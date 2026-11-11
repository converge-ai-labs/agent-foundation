"""One automatic inbox and atomic Heartbeat clock; no execution waits."""

import json
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert

from a13n_harness_ui.bot_models import BotInboxInput, BotState
from a13n_harness_ui.configuration.models import BotConfiguration

from .database import DatabaseSessions, short_session, transaction
from .models import BotInboxRecord, BotStateRecord, ConnectorConfigurationRecord


def serialize_batch(inputs: tuple[BotInboxInput, ...]) -> str:
    return json.dumps([item.model_dump(mode="json") for item in inputs], ensure_ascii=False, separators=(",", ":"))


class BotInboxRepository:
    def __init__(self, sessions: DatabaseSessions) -> None:
        self._sessions = sessions

    async def heartbeat(self, *, policy: BotConfiguration, now: datetime, text: str) -> None:
        """Retain/coalesce a due opportunity and advance its clock in one transaction."""
        async with transaction(self._sessions) as session:
            row = await session.get(BotStateRecord, 1)
            state = BotState() if row is None else BotState.model_validate_json(row.payload)
            next_at = state.next_heartbeat_at
            interval = state.heartbeat_interval_seconds
            if not policy.heartbeat_enabled:
                await session.execute(delete(BotInboxRecord).where(BotInboxRecord.source == "heartbeat"))
            if not policy.enabled or not policy.heartbeat_enabled:
                next_at, interval = None, None
            elif next_at is None or interval != policy.interval_seconds:
                next_at, interval = now + timedelta(seconds=policy.interval_seconds), policy.interval_seconds
            elif next_at <= now:
                await session.execute(
                    insert(BotInboxRecord)
                    .values(
                        input_id=f"heartbeat-{uuid4().hex}",
                        source="heartbeat",
                        connector_id=None,
                        source_event_id=next_at.isoformat(),
                        event_type="heartbeat",
                        text=text,
                        created_at=now,
                    )
                    .on_conflict_do_nothing()
                )
                next_at = now + timedelta(seconds=policy.interval_seconds)
            updated = state.model_copy(update={"next_heartbeat_at": next_at, "heartbeat_interval_seconds": interval})
            if updated != state:
                if row is None:
                    session.add(BotStateRecord(singleton_id=1, payload=updated.model_dump_json()))
                else:
                    row.payload = updated.model_dump_json()

    async def pending(self) -> tuple[BotInboxInput, ...]:
        """Fair Heartbeat selection followed by oldest eligible Connector events."""
        async with short_session(self._sessions) as session:
            rows = (
                await session.scalars(
                    select(BotInboxRecord)
                    .outerjoin(ConnectorConfigurationRecord)
                    .where(
                        (BotInboxRecord.source == "heartbeat")
                        | (
                            ConnectorConfigurationRecord.enabled.is_(True)
                            & ConnectorConfigurationRecord.deliver_to_bot.is_(True)
                        )
                    )
                    .order_by(
                        (BotInboxRecord.source == "heartbeat").desc(),
                        BotInboxRecord.created_at,
                        BotInboxRecord.input_id,
                    )
                    .limit(32)
                )
            ).all()
            result: tuple[BotInboxInput, ...] = ()
            for row in rows:
                item = BotInboxInput(
                    input_id=row.input_id,
                    source="heartbeat" if row.source == "heartbeat" else "connector",
                    connector_id=row.connector_id,
                    source_event_id=row.source_event_id,
                    event_type=row.event_type,
                    text=row.text,
                    created_at=row.created_at,
                )
                candidate = (*result, item)
                if len(serialize_batch(candidate).encode("utf-8")) > 60 * 1024:
                    break
                result = candidate
            return result

    async def delivered(self, inputs: tuple[BotInboxInput, ...]) -> None:
        if inputs:
            async with transaction(self._sessions) as session:
                await session.execute(
                    delete(BotInboxRecord).where(BotInboxRecord.input_id.in_(item.input_id for item in inputs))
                )
