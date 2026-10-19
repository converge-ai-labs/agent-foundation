"""Short-transaction persistence and authentication for revocable share grants."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select

from a13n_harness_ui.errors import HarnessUiError
from a13n_harness_ui.share_models import ShareCreate, ShareCreated, ShareGrant, SharePermission, ShareScope

from .database import DatabaseSessions, short_session, transaction
from .models import ShareGrantRecord


def unavailable() -> HarnessUiError:
    return HarnessUiError("This sharing link has expired, was revoked, or is unavailable.", code="share_unavailable")


def _active(record: ShareGrantRecord) -> None:
    if record.revoked_at is not None or record.expires_at <= datetime.now(UTC):
        raise unavailable()


def _view(record: ShareGrantRecord) -> ShareGrant:
    return ShareGrant.model_validate(
        {
            "share_id": record.share_id,
            "scope": record.scope,
            "target_id": record.target_id,
            "permission": record.permission,
            "created_at": record.created_at,
            "expires_at": record.expires_at,
            "revoked_at": record.revoked_at,
        }
    )


class ShareRepository:
    def __init__(self, sessions: DatabaseSessions) -> None:
        self._sessions = sessions

    async def create(self, command: ShareCreate) -> ShareCreated:
        key = secrets.token_urlsafe(32)
        now = datetime.now(UTC)
        record = ShareGrantRecord(
            share_id=f"share_{uuid4().hex}",
            scope=command.scope,
            target_id=command.target_id,
            permission=command.permission,
            token_digest=hashlib.sha256(key.encode()).hexdigest(),
            created_at=now,
            expires_at=now + timedelta(hours=command.lifetime_hours),
        )
        async with transaction(self._sessions) as session:
            session.add(record)
        return ShareCreated(grant=_view(record), key=key)

    async def list(self, scope: ShareScope, target_id: str) -> tuple[ShareGrant, ...]:
        async with short_session(self._sessions) as session:
            records = await session.scalars(
                select(ShareGrantRecord)
                .where(
                    ShareGrantRecord.scope == scope,
                    ShareGrantRecord.target_id == target_id,
                    ShareGrantRecord.revoked_at.is_(None),
                    ShareGrantRecord.expires_at > datetime.now(UTC),
                )
                .order_by(ShareGrantRecord.created_at.desc())
            )
            return tuple(_view(record) for record in records)

    async def authenticate(self, share_id: str, key: str) -> ShareGrant:
        async with short_session(self._sessions) as session:
            record = await session.get(ShareGrantRecord, share_id)
            digest = hashlib.sha256(key.encode()).hexdigest()
            if record is None or not hmac.compare_digest(record.token_digest, digest):
                raise unavailable()
            _active(record)
            return _view(record)

    async def active(self, share_id: str) -> ShareGrant:
        """Recheck an already authenticated connection, including permission changes."""
        async with short_session(self._sessions) as session:
            record = await session.get(ShareGrantRecord, share_id)
            if record is None:
                raise unavailable()
            _active(record)
            return _view(record)

    async def update(self, share_id: str, permission: SharePermission) -> ShareGrant:
        async with transaction(self._sessions) as session:
            record = await session.get(ShareGrantRecord, share_id)
            if record is None:
                raise unavailable()
            _active(record)
            record.permission = permission
            return _view(record)

    async def revoke(self, share_id: str) -> None:
        async with transaction(self._sessions) as session:
            record = await session.get(ShareGrantRecord, share_id)
            if record is None:
                raise unavailable()
            if record.revoked_at is None:
                record.revoked_at = datetime.now(UTC)
