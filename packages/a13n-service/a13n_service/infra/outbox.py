"""Durable at-least-once delivery rows: enqueue with the owning state change, claim, settle.

The outbox owns persistence, claiming, settlement and the retry policy. Owners register one handler per
`kind`; a handler runs outside any database session for external I/O and settles its claim itself, so an
internal delivery can settle in the same transaction that applies it. A handler that raises instead leaves
the retry to the outbox: backoff, then dead once the row has used its attempts. Every claim uses an attempt,
so a handler that keeps being cancelled, or whose process dies, also ends dead; a handler that settles its claim
`deferred` (not deliverable yet, through no fault of the delivery) gives its attempt back.
"""

import secrets
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

import anyio
from a13n_logging import get_logger
from pydantic import JsonValue
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    delete,
    func,
    or_,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.infra.crypto import SecretLocation, secret_hash
from a13n_service.infra.db import Base, Storage, now, transaction
from a13n_service.infra.ids import new_object_id

logger = get_logger(__name__)

type OutboxKind = Literal["webhook", "child_result", "email", "memory_purge"]


class OutboxRow(Base):
    __tablename__ = "outbox"
    __table_args__ = (
        UniqueConstraint("kind", "dedupe_key"),
        CheckConstraint("kind IN ('webhook', 'child_result', 'email', 'memory_purge')", name="kind"),
        # Account mail (password reset, email change) belongs to no organization; every other delivery does.
        CheckConstraint("organization_id IS NOT NULL OR kind = 'email'", name="tenant"),
        ForeignKeyConstraint(["organization_id", "workspace_id"], ["workspaces.organization_id", "workspaces.id"]),
        CheckConstraint("status IN ('pending', 'delivered', 'dead')", name="status"),
        CheckConstraint("(status = 'delivered') = (delivered_at IS NOT NULL)", name="delivered"),
        Index("ix_outbox_due", "kind", "available_at", postgresql_where=text("status = 'pending'")),
        Index("ix_outbox_settled", "created_at", postgresql_where=text("status <> 'pending'")),
    )
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str | None] = mapped_column(ForeignKey("organizations.id"))
    workspace_id: Mapped[str | None]
    kind: Mapped[str]
    dedupe_key: Mapped[str]
    # Everything delivery needs is copied here; `subscription_id` is diagnostic provenance, not a foreign key.
    target: Mapped[dict] = mapped_column(JSONB)
    payload: Mapped[dict] = mapped_column(JSONB)
    subscription_id: Mapped[str | None]
    status: Mapped[str] = mapped_column(server_default="pending")
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    attempts: Mapped[int] = mapped_column(server_default="0")
    lease_owner: Mapped[str | None]
    lease_token_hash: Mapped[str | None]
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


@dataclass(frozen=True, slots=True)
class Claim:
    id: str
    kind: str
    organization_id: str | None
    workspace_id: str | None
    target: dict
    payload: dict
    attempts: int
    token: str


type Handler = Callable[[Claim], Awaitable[None]]


class Undelivered(Exception):
    """A failed attempt with a short, secret-free reason worth keeping as the row's error."""


def secret_location(organization_id: str | None, row_id: str, column: Literal["target", "payload"]) -> SecretLocation:
    """Where a secret staged in an outbox row's `target` or `payload` is bound, re-encrypted for that row."""
    return SecretLocation(organization_id, "outbox", column, row_id)


def enqueue(
    session: AsyncSession,
    *,
    organization_id: str | None,
    workspace_id: str | None,
    kind: OutboxKind,
    target: Mapping[str, JsonValue],
    payload: Mapping[str, JsonValue],
    dedupe_key: str | None = None,
    subscription_id: str | None = None,
    row_id: str | None = None,
    dead: str | None = None,
) -> str:
    """Stage a delivery in the caller's transaction; without a dedupe key the row ID is its own identity.

    `dead` stages a delivery that can never be sent as already dead, with that reason as its error.
    """
    identity = row_id or new_object_id("obx")
    session.add(
        OutboxRow(
            id=identity,
            organization_id=organization_id,
            workspace_id=workspace_id,
            kind=kind,
            dedupe_key=dedupe_key or identity,
            target=dict(target),
            payload=dict(payload),
            subscription_id=subscription_id,
            status="pending" if dead is None else "dead",
            last_error=dead,
        )
    )
    return identity


async def enqueue_once(
    session: AsyncSession,
    *,
    organization_id: str,
    workspace_id: str | None,
    kind: OutboxKind,
    dedupe_key: str,
    target: Mapping[str, JsonValue],
    payload: Mapping[str, JsonValue],
) -> None:
    """Idempotent staging for deliveries keyed by a durable fact, such as one sealed child run."""
    await session.execute(
        insert(OutboxRow)
        .values(
            id=new_object_id("obx"),
            organization_id=organization_id,
            workspace_id=workspace_id,
            kind=kind,
            dedupe_key=dedupe_key,
            target=dict(target),
            payload=dict(payload),
        )
        .on_conflict_do_nothing(index_elements=["kind", "dedupe_key"])
    )


async def claim(
    storage: Storage, kind: OutboxKind, *, owner: str, limit: int, lease_seconds: float, max_attempts: int
) -> list[Claim]:
    """Lease up to `limit` due rows, each using an attempt. A row that has already used `max_attempts` is dead
    instead: its last claim lapsed without settling, since a settled failure there would have ended it."""
    async with transaction(storage) as session:
        current = await now(session)
        rows = (
            await session.scalars(
                select(OutboxRow)
                .where(
                    OutboxRow.kind == kind,
                    OutboxRow.status == "pending",
                    OutboxRow.available_at <= current,
                    or_(OutboxRow.lease_expires_at.is_(None), OutboxRow.lease_expires_at <= current),
                )
                .order_by(OutboxRow.available_at, OutboxRow.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        ).all()
        claims = []
        for row in rows:
            if row.attempts >= max_attempts:
                row.status, row.last_error = "dead", "unsettled"
                row.lease_owner = row.lease_token_hash = row.lease_expires_at = None
                logger.warning(
                    "Outbox delivery dead", extra={"outbox_id": row.id, "kind": row.kind, "reason": "unsettled"}
                )
                continue
            token = secrets.token_urlsafe(32)
            row.attempts += 1
            row.lease_owner = owner
            row.lease_token_hash = secret_hash(token)
            row.lease_expires_at = current + timedelta(seconds=lease_seconds)
            claims.append(
                Claim(
                    id=row.id,
                    kind=row.kind,
                    organization_id=row.organization_id,
                    workspace_id=row.workspace_id,
                    target=row.target,
                    payload=row.payload,
                    attempts=row.attempts,
                    token=token,
                )
            )
        return claims


async def settle(
    session: AsyncSession,
    claimed: Claim,
    outcome: Literal["delivered", "retry", "deferred", "dead"],
    *,
    error: str | None = None,
    retry_after: float = 0,
) -> bool:
    """Apply an outcome only while this claim still holds the lease; a stale sender changes nothing.

    `retry` and `deferred` both make the row due again after `retry_after`; `deferred` gives back the attempt the
    claim used.
    """
    row = await session.get(OutboxRow, claimed.id, with_for_update=True)
    if row is None or row.status != "pending" or row.lease_token_hash != secret_hash(claimed.token):
        return False
    current = await now(session)
    row.lease_owner = row.lease_token_hash = row.lease_expires_at = None
    row.last_error = error[:1024] if error else None
    if outcome == "delivered":
        row.status, row.delivered_at = "delivered", current
    elif outcome == "dead":
        row.status = "dead"
    else:
        row.available_at = current + timedelta(seconds=retry_after)
        if outcome == "deferred":
            row.attempts -= 1
    return True


def backoff(attempts: int, *, base: float = 2, cap: float = 3600) -> float:
    return min(cap, base ** min(attempts, 16))


@dataclass(frozen=True, slots=True)
class Delivery:
    """One bounded pass of up to `limit` claims per kind, as a sweep runs it.

    Kinds are delivered side by side, so a slow kind never delays another. Within a kind, claims are taken
    `parallel` at a time and handled together, so each starts when it is claimed and no claim waits out its lease
    behind others. A handler that raises leaves its row for a retry with backoff, or dead after `max_attempts`.
    """

    storage: Storage
    handlers: Mapping[OutboxKind, Handler]
    owner: str
    limit: int
    lease_seconds: float
    max_attempts: int
    parallel: int = 8

    async def __call__(self) -> None:
        async with anyio.create_task_group() as group:
            for kind, handler in self.handlers.items():
                group.start_soon(self._deliver_kind, kind, handler)

    async def _deliver_kind(self, kind: OutboxKind, handler: Handler) -> None:
        # No batch starts after one lease has passed, so a pass ends within two leases, whatever the limit.
        deadline = anyio.current_time() + self.lease_seconds
        limit = self.limit
        while limit > 0 and anyio.current_time() < deadline:
            batch = min(self.parallel, limit)
            claims = await claim(
                self.storage,
                kind,
                owner=self.owner,
                limit=batch,
                lease_seconds=self.lease_seconds,
                max_attempts=self.max_attempts,
            )
            async with anyio.create_task_group() as group:
                for claimed in claims:
                    group.start_soon(self._handle, handler, claimed)
            if len(claims) < batch:
                return
            limit -= batch

    async def _handle(self, handler: Handler, claimed: Claim) -> None:
        """Never raises, so one delivery cannot cancel the others; an unsettled claim retries when its lease ends."""
        try:
            await handler(claimed)
        except Exception as error:
            await self._fail(claimed, str(error) if isinstance(error, Undelivered) else type(error).__name__)

    async def _fail(self, claimed: Claim, reason: str) -> None:
        dead = claimed.attempts >= self.max_attempts
        logger.warning(
            "Outbox delivery dead" if dead else "Outbox delivery failed",
            extra={"outbox_id": claimed.id, "kind": claimed.kind, "reason": reason},
        )
        try:
            async with transaction(self.storage) as session:
                outcome = "dead" if dead else "retry"
                await settle(session, claimed, outcome, error=reason, retry_after=backoff(claimed.attempts))
        except Exception as failure:
            logger.warning(
                "Outbox settlement failed", extra={"outbox_id": claimed.id, "error_type": type(failure).__name__}
            )


async def purge_settled(storage: Storage, *, older_than: timedelta, limit: int) -> int:
    """Bounded retention for delivered and dead rows; pending rows are never removed."""
    async with transaction(storage) as session:
        cutoff = await now(session) - older_than
        ids = (
            await session.scalars(
                select(OutboxRow.id)
                .where(OutboxRow.status != "pending", OutboxRow.created_at < cutoff)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        ).all()
        if ids:
            await session.execute(delete(OutboxRow).where(OutboxRow.id.in_(ids)))
        return len(ids)
