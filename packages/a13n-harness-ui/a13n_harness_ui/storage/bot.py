"""Narrow retained Bot ingress and separately published user communication."""

from sqlalchemy import func, select, update

from a13n_harness_ui.bot_models import BOT_THREAD_ID, BotInput, BotMessage, BotState
from a13n_harness_ui.errors import ThreadError

from .database import DatabaseSessions, short_session, transaction
from .models import BotInputRecord, BotMessageRecord, BotStateRecord, ThreadRecord


class BotRepository:
    def __init__(self, sessions: DatabaseSessions) -> None:
        self._sessions = sessions

    async def state(self) -> BotState:
        async with short_session(self._sessions) as session:
            row = await session.get(BotStateRecord, 1)
            return BotState() if row is None else BotState.model_validate_json(row.payload)

    async def save_state(self, state: BotState) -> None:
        async with transaction(self._sessions) as session:
            row = await session.get(BotStateRecord, 1)
            if row is None:
                session.add(BotStateRecord(singleton_id=1, payload=state.model_dump_json()))
            else:
                row.payload = state.model_dump_json()

    async def get_input(self, input_id: str) -> BotInput | None:
        async with short_session(self._sessions) as session:
            row = await session.get(BotInputRecord, input_id)
            return BotInput.model_validate_json(row.payload) if row is not None else None

    async def retain(self, item: BotInput) -> BotInput:
        async with transaction(self._sessions) as session:
            row = await session.get(BotInputRecord, item.input_id)
            if row is not None:
                previous = BotInput.model_validate_json(row.payload)
                if (previous.source, previous.text, previous.parts, previous.source_id, previous.causation_id) != (
                    item.source,
                    item.text,
                    item.parts,
                    item.source_id,
                    item.causation_id,
                ):
                    raise ThreadError("This Bot input ID already has different content.", code="bot_input_conflict")
                return previous
            pending = await session.scalar(
                select(func.count())
                .select_from(BotInputRecord)
                .where(BotInputRecord.state.in_(("queued", "dispatching", "admitted")))
            )
            if pending is not None and pending >= 16:
                raise ThreadError("Bot has 16 pending inputs. Inspect them before adding more.", code="bot_input_full")
            session.add(
                BotInputRecord(
                    input_id=item.input_id, state=item.state, created_at=item.created_at, payload=item.model_dump_json()
                )
            )
            return item

    async def save_input(self, item: BotInput) -> None:
        async with transaction(self._sessions) as session:
            row = await session.get(BotInputRecord, item.input_id)
            if row is None:
                raise ThreadError("Bot input is unavailable.", code="bot_input_missing")
            row.state, row.payload = item.state, item.model_dump_json()

    async def inputs(self, *, pending_only: bool = False) -> tuple[BotInput, ...]:
        statement = select(BotInputRecord)
        if pending_only:
            statement = statement.where(BotInputRecord.state.in_(("queued", "dispatching", "admitted")))
        statement = statement.order_by(BotInputRecord.created_at.desc(), BotInputRecord.input_id.desc()).limit(32)
        async with short_session(self._sessions) as session:
            rows = (await session.scalars(statement)).all()
            return tuple(reversed([BotInput.model_validate_json(row.payload) for row in rows]))

    async def publish(self, message: BotMessage) -> bool:
        async with transaction(self._sessions) as session:
            row = await session.get(BotMessageRecord, message.message_id)
            if row is not None:
                return False
            session.add(
                BotMessageRecord(
                    message_id=message.message_id, created_at=message.created_at, payload=message.model_dump_json()
                )
            )
            if message.role == "assistant":
                await session.execute(
                    update(ThreadRecord)
                    .where(ThreadRecord.thread_id == BOT_THREAD_ID)
                    .values(
                        unread=True,
                        read_state_version=ThreadRecord.read_state_version + 1,
                    )
                )
            return True

    async def has_visible_result(self, *, direct_reply: bool = False) -> bool:
        """Final text or intentional publications advance Bot's result marker, regardless of input source."""
        if direct_reply:
            return True
        current = tuple(
            item for item in await self.inputs(pending_only=True) if item.state in {"dispatching", "admitted"}
        )
        identities = {item.input_id for item in current}
        return any(message.role == "assistant" and message.input_id in identities for message in await self.messages())

    async def messages(self, *, before: str | None = None, limit: int = 100) -> tuple[BotMessage, ...]:
        statement = select(BotMessageRecord)
        async with short_session(self._sessions) as session:
            if before is not None:
                anchor = await session.get(BotMessageRecord, before)
                if anchor is None:
                    raise ThreadError("Message cursor is unavailable.", code="bot_message_cursor_invalid")
                statement = statement.where(
                    (BotMessageRecord.created_at < anchor.created_at)
                    | ((BotMessageRecord.created_at == anchor.created_at) & (BotMessageRecord.message_id < before))
                )
            rows = (
                await session.scalars(
                    statement.order_by(BotMessageRecord.created_at.desc(), BotMessageRecord.message_id.desc()).limit(
                        limit
                    )
                )
            ).all()
            return tuple(reversed([BotMessage.model_validate_json(row.payload) for row in rows]))
