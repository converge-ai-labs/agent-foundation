"""Expire short-lived OAuth state without retrying exchanges or remote cleanup."""

from anyio import sleep
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.storage import transaction
from a13n_service.temporal import Clock, utc_now

from .models import MCPConnectionRecord, MCPOAuthSessionRecord


class MCPReconciler:
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], *, poll_interval_seconds: float = 2, clock: Clock = utc_now
    ) -> None:
        self._sessions = sessions
        self._poll_interval_seconds = poll_interval_seconds
        self._clock = clock

    async def run(self) -> None:
        while True:
            await sleep(self._poll_interval_seconds)
            await self.reconcile_one()

    async def reconcile_one(self) -> bool:
        now = self._clock()
        async with transaction(self._sessions) as session:
            connection = await session.scalar(
                select(MCPConnectionRecord)
                .join(MCPOAuthSessionRecord, MCPOAuthSessionRecord.mcp_connection_id == MCPConnectionRecord.id)
                .where(
                    MCPOAuthSessionRecord.status.in_(("pending", "exchanging")),
                    or_(
                        MCPOAuthSessionRecord.expires_at <= now,
                        (MCPOAuthSessionRecord.status == "exchanging")
                        & (MCPOAuthSessionRecord.claim_expires_at <= now),
                    ),
                )
                .order_by(MCPOAuthSessionRecord.expires_at, MCPOAuthSessionRecord.id)
                .limit(1)
                .with_for_update(of=MCPConnectionRecord, skip_locked=True)
            )
            if connection is None:
                return False
            state = await session.scalar(
                select(MCPOAuthSessionRecord)
                .where(
                    MCPOAuthSessionRecord.mcp_connection_id == connection.id,
                    MCPOAuthSessionRecord.status.in_(("pending", "exchanging")),
                    or_(
                        MCPOAuthSessionRecord.expires_at <= now,
                        (MCPOAuthSessionRecord.status == "exchanging")
                        & (MCPOAuthSessionRecord.claim_expires_at <= now),
                    ),
                )
                .order_by(MCPOAuthSessionRecord.expires_at, MCPOAuthSessionRecord.id)
                .limit(1)
                .with_for_update()
            )
            if state is None:
                return False
            state.status = "expired"
            state.clear_credential()
            state.claim_owner = None
            state.claim_expires_at = None
            state.updated_at = now
            if (
                connection is not None
                and connection.deleted_at is None
                and connection.status == "pending"
                and connection.version == state.connection_version
            ):
                connection.status = "action_required"
                connection.status_reason = "reauthorization_required"
                connection.updated_at = now
            return True
