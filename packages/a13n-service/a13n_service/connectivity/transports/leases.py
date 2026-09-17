"""Short transactional claims and fencing for persistent provider connections."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.application_errors import ErrorCategory
from a13n_service.connectivity.errors import NativeError
from a13n_service.storage import transaction
from a13n_service.temporal import assume_utc, utc_now

from .models import EventConnectionRecord

LEASE_SECONDS = 30


@dataclass(frozen=True, slots=True)
class ConnectionClaim:
    key: str
    owner: str
    generation: int


async def require_claim(session: AsyncSession, claim: ConnectionClaim) -> EventConnectionRecord:
    record = await session.scalar(
        select(EventConnectionRecord).where(EventConnectionRecord.key == claim.key).with_for_update()
    )
    if (
        record is None
        or record.owner != claim.owner
        or record.generation != claim.generation
        or assume_utc(record.lease_expires_at) <= utc_now()
    ):
        raise NativeError(
            "connection_lease_lost", "Event connection ownership expired.", category=ErrorCategory.unavailable
        )
    return record


class ConnectionLeases:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], owner: str) -> None:
        self.sessions = sessions
        self.owner = owner

    async def claim(self, key: str, account_versions: dict[str, int]) -> ConnectionClaim | None:
        now = utc_now()
        async with transaction(self.sessions) as session:
            await session.execute(
                insert(EventConnectionRecord)
                .values(
                    key=key,
                    account_versions=account_versions,
                    owner=self.owner,
                    generation=0,
                    lease_expires_at=now,
                    state="connecting",
                    observed_at=now,
                )
                .on_conflict_do_nothing(index_elements=["key"])
            )
            record = await session.scalar(
                select(EventConnectionRecord).where(EventConnectionRecord.key == key).with_for_update()
            )
            assert record is not None
            if assume_utc(record.lease_expires_at) > now:
                return None
            record.account_versions = account_versions
            record.owner = self.owner
            record.generation += 1
            record.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
            record.state = "connecting"
            record.error_code = None
            record.observed_at = now
            return ConnectionClaim(key, self.owner, record.generation)

    async def update(
        self,
        claim: ConnectionClaim,
        *,
        state: str | None = None,
        error_code: str | None = None,
        renew: bool = False,
        event_at: datetime | None = None,
    ) -> None:
        async with transaction(self.sessions) as session:
            record = await require_claim(session, claim)
            now = utc_now()
            if renew:
                record.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
            if state is not None:
                record.state = state
                record.error_code = error_code
            if event_at is not None:
                record.last_event_at = event_at
            record.observed_at = now

    async def release(self, claim: ConnectionClaim) -> None:
        async with transaction(self.sessions) as session:
            record = await session.scalar(
                select(EventConnectionRecord).where(EventConnectionRecord.key == claim.key).with_for_update()
            )
            if record is not None and record.owner == claim.owner and record.generation == claim.generation:
                record.lease_expires_at = utc_now()
                record.state = "disconnected"
                record.observed_at = utc_now()
