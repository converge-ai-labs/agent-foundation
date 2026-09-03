"""Bounded background reconciliation for MCP setup, catalogs, and cleanup."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from anyio import sleep
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.storage import transaction

from .catalog_service import MCPCatalogService
from .errors import MCPConnectionError
from .models import MCPConnectionRecord, MCPOAuthSessionRecord
from .oauth_service import MCPOAuthService
from .service import MCPConnectionService


class MCPReconciler:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        connections: MCPConnectionService,
        oauth: MCPOAuthService,
        catalog: MCPCatalogService,
        *,
        poll_interval_seconds: float = 2,
        refresh_skew_seconds: int = 60,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._sessions = sessions
        self._connections = connections
        self._oauth = oauth
        self._catalog = catalog
        self._poll_interval_seconds = poll_interval_seconds
        self._refresh_skew_seconds = refresh_skew_seconds
        self._clock = clock

    async def run(self) -> None:
        while True:
            await sleep(self._poll_interval_seconds)
            await self.reconcile_one()

    async def reconcile_one(self) -> bool:
        if await self._expire_oauth_session():
            return True
        expired_session_id = await self._expired_oauth_cleanup_candidate()
        if expired_session_id is not None:
            await self._oauth.cleanup_expired_session(expired_session_id)
            return True
        cleanup_id = await self._cleanup_candidate()
        if cleanup_id is not None:
            await self._connections.reconcile_cleanup(cleanup_id)
            return True
        connection_id = await self._catalog_candidate()
        if connection_id is None:
            return False
        refresh = await self._oauth.refresh_if_due(
            connection_id,
            skew_seconds=self._refresh_skew_seconds,
        )
        if refresh is None:
            try:
                await self._catalog.refresh(connection_id)
            except MCPConnectionError:
                pass
        return True

    async def _expire_oauth_session(self) -> bool:
        now = self._clock()
        async with transaction(self._sessions) as session:
            oauth_session = await session.scalar(
                select(MCPOAuthSessionRecord)
                .where(
                    MCPOAuthSessionRecord.status.in_(("pending", "exchanging")),
                    MCPOAuthSessionRecord.expires_at <= now,
                )
                .order_by(MCPOAuthSessionRecord.expires_at, MCPOAuthSessionRecord.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if oauth_session is None:
                return False
            oauth_session.status = "expired"
            oauth_session.claim_owner = None
            oauth_session.claim_expires_at = None
            oauth_session.updated_at = now
            return True

    async def _expired_oauth_cleanup_candidate(self) -> str | None:
        now = self._clock()
        async with transaction(self._sessions) as session:
            return await session.scalar(
                select(MCPOAuthSessionRecord.id)
                .where(
                    MCPOAuthSessionRecord.status == "expired",
                    MCPOAuthSessionRecord.consumed_at.is_(None),
                    or_(
                        MCPOAuthSessionRecord.claim_expires_at.is_(None),
                        MCPOAuthSessionRecord.claim_expires_at <= now,
                    ),
                )
                .order_by(MCPOAuthSessionRecord.updated_at, MCPOAuthSessionRecord.id)
                .limit(1)
            )

    async def _cleanup_candidate(self) -> str | None:
        async with transaction(self._sessions) as session:
            return await session.scalar(
                select(MCPConnectionRecord.id)
                .where(
                    MCPConnectionRecord.cleanup_pending.is_(True),
                    MCPConnectionRecord.cleanup_available_at <= self._clock(),
                )
                .order_by(MCPConnectionRecord.cleanup_available_at, MCPConnectionRecord.id)
                .limit(1)
            )

    async def _catalog_candidate(self) -> str | None:
        async with transaction(self._sessions) as session:
            return await session.scalar(
                select(MCPConnectionRecord.id)
                .where(
                    MCPConnectionRecord.deleted_at.is_(None),
                    MCPConnectionRecord.status.in_(("pending", "ready")),
                    MCPConnectionRecord.catalog_available_at <= self._clock(),
                    or_(
                        MCPConnectionRecord.catalog_claim_expires_at.is_(None),
                        MCPConnectionRecord.catalog_claim_expires_at <= self._clock(),
                    ),
                    or_(
                        MCPConnectionRecord.auth_mode == "none",
                        MCPConnectionRecord.credential_secret_id.is_not(None),
                    ),
                )
                .order_by(MCPConnectionRecord.catalog_available_at, MCPConnectionRecord.id)
                .limit(1)
            )
