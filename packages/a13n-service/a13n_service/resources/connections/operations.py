"""The one remote operation a connection may have outstanding, and recovery of operations whose owner vanished.

An operation is claimed under the row lock before its request is sent; only the claimant sends it, and only a
claim that is still current may publish the result. Invalidation (revoke, a change of server or account, an
authorization that claims an operation itself) drops the claim, so a late response changes nothing; starting an
OAuth browser flow drops only a completion of the flow it replaces. `perform` sends the request and
publishes its result shielded from the caller's cancellation and bounded, so only a timeout or a crash leaves an
outcome unknown. An operation that may have been sent without a result is never repeated: it fails as
`outcome_unknown`.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

import anyio
from a13n_harness.providers.connector.contracts import ConnectorProviderError
from a13n_logging import exception_details, get_logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.audit import record
from a13n_service.infra.db import Storage, lock, now, short_session, transaction
from a13n_service.infra.errors import ServiceError, conflict
from a13n_service.infra.ids import new_object_id
from a13n_service.providers.tools.mcp import McpConfig
from a13n_service.providers.tools.oauth import OAuthError
from a13n_service.resources.connections.schemas import ConnectionFailure
from a13n_service.resources.connections.tables import ConnectionRow, OperationKind
from a13n_service.tenancy.authorize import WorkspaceScope

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Operation:
    connection_id: str
    id: str


def claim_operation(row: ConnectionRow, kind: OperationKind, *, current: datetime, seconds: float) -> Operation:
    """Record an operation on the locked row; callers first end, join or wait for an outstanding one."""
    assert row.operation_id is None
    operation_id = new_object_id("connop")
    row.operation_id, row.operation_kind = operation_id, kind
    # Twice the owner's own bound, so recovery never races an owner still inside its deadline.
    row.operation_deadline = current + timedelta(seconds=2 * seconds)
    return Operation(row.id, operation_id)


def supersede(row: ConnectionRow) -> None:
    """End any outstanding operation so its late result can never publish; a superseded refresh is lost."""
    if row.operation_kind == "refresh":
        record_failure(row, "outcome_unknown", "superseded")
    _end(row)


def invalidate(row: ConnectionRow) -> None:
    """End any outstanding operation and drop the pending browser authorization."""
    supersede(row)
    drop_flow(row)


def replace_flow(row: ConnectionRow) -> None:
    """Drop the pending browser authorization and a completion of it in progress, whose late result would drop the
    next flow; a refresh or revocation continues, so the credential stays usable until a flow completes."""
    if row.operation_kind == "complete":
        _end(row)
    drop_flow(row)


def drop_flow(row: ConnectionRow) -> None:
    row.authorization = row.oauth_state_hash = row.authorization_expires_at = None


def drop_credential(row: ConnectionRow) -> None:
    row.credential = row.tokens = row.expires_at = None


def expire_operation(session: AsyncSession, row: ConnectionRow) -> None:
    """Fail an operation whose owner ran out of time: it may have been sent, so its outcome is unknown."""
    record(
        session,
        WorkspaceScope(row.organization_id, row.workspace_id),
        actor_id=None,
        action="connection.operation.expire",
        target_kind="connection",
        target_id=row.id,
        outcome="failed",
        details={"operation_id": row.operation_id, "operation_kind": row.operation_kind},
    )
    record_failure(row, "outcome_unknown", "deadline_exceeded")


def record_failure(row: ConnectionRow, reason: str, code: str | None) -> None:
    """End the outstanding operation as failed; what stays usable follows from its kind and outcome."""
    operation_id, kind = row.operation_id, row.operation_kind
    _end(row)
    _failed(row, operation_id, kind, reason, code)


def refuse(row: ConnectionRow, kind: OperationKind, code: str) -> None:
    """Record an operation refused before it was claimed; an outstanding one continues."""
    _failed(row, new_object_id("connop"), kind, "rejected", code)


def _failed(row: ConnectionRow, operation_id: str | None, kind: str | None, reason: str, code: str | None) -> None:
    row.failure = ConnectionFailure.model_validate(
        {"operation_id": operation_id, "operation_kind": kind, "reason": reason, "code": code}
    ).model_dump(mode="json")
    if kind == "refresh" and _loses_credential(row, reason, code):
        drop_credential(row)
        row.status = "reauthorization_required"
    elif kind in {"setup", "complete"}:
        drop_flow(row)


def _loses_credential(row: ConnectionRow, reason: str, code: str | None) -> bool:
    """Whether a failed refresh leaves no usable credential.

    A response that may have rotated the refresh token is never replaced by presenting the old one again, a
    refused grant is gone, and a client-credentials client's failed renewal needs `authorize` again. Any other
    refusal never reached the grant: the credential stays and a later use renews again.
    """
    if reason == "outcome_unknown" or code == "invalid_grant":
        return True
    oauth = McpConfig.model_validate(row.config).oauth
    return oauth is not None and oauth.grant_type == "client_credentials"


def failure_of(error: BaseException) -> tuple[str, str | None]:
    """The reason and safe code a failed operation records; anything unclassified may have taken effect."""
    if isinstance(error, OAuthError):
        return ("outcome_unknown" if error.unknown else "rejected"), error.code
    if isinstance(error, ConnectorProviderError):
        return ("outcome_unknown" if error.outcome_unknown else "rejected"), error.code
    if isinstance(error, ServiceError) and error.code in {"conflict", "invalid_argument", "disabled"}:
        return "rejected", str(error.details.get("reason", error.code))
    return "outcome_unknown", None


async def perform[T](
    storage: Storage,
    operation: Operation,
    send: Callable[[], Awaitable[T]],
    publish: Callable[[AsyncSession, ConnectionRow, T], None],
    *,
    seconds: float,
) -> T:
    """Send the claimed operation and publish its result while the claim is still current.

    Both are shielded from the caller's cancellation and bounded by `seconds`, so only a timeout or a crash
    leaves the outcome unknown. A failure is recorded on the row and raised; a claim superseded meanwhile
    publishes nothing and raises `superseded`.
    """
    try:
        with anyio.fail_after(seconds, shield=True):
            result = await send()
            published = await _settle(storage, operation, lambda session, row: publish(session, row, result))
    except Exception as error:
        logger.warning(
            "Connection operation failed",
            extra={
                "connection_id": operation.connection_id,
                "operation_id": operation.id,
                "exception_details": exception_details(error),
            },
        )
        with anyio.CancelScope(shield=True):
            await _fail(storage, operation, error)
        raise
    if not published:
        raise conflict("connection", operation.connection_id, "superseded")
    return result


async def wait_for(storage: Storage, connection_id: str, operation_id: str, *, timeout: float) -> None:
    """Await another owner's operation without holding a session; its outcome is then on the row."""
    with anyio.move_on_after(timeout):
        while True:
            async with short_session(storage) as session:
                current = await session.scalar(
                    select(ConnectionRow.operation_id).where(ConnectionRow.id == connection_id)
                )
            if current != operation_id:
                return
            await anyio.sleep(0.1)
    raise ServiceError("unavailable", "A connection operation is still in progress", {"dependency": "connection"})


async def recover_operations(storage: Storage, *, limit: int) -> int:
    """Fail operations past their deadline: their owner crashed or lost the response."""
    async with transaction(storage) as session:
        rows = (
            await session.scalars(
                select(ConnectionRow)
                .where(ConnectionRow.operation_deadline <= await now(session))
                .order_by(ConnectionRow.operation_deadline)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        ).all()
        for row in rows:
            expire_operation(session, row)
    return len(rows)


async def _settle(
    storage: Storage, operation: Operation, publish: Callable[[AsyncSession, ConnectionRow], None]
) -> bool:
    async with transaction(storage) as session:
        row = await lock(session, ConnectionRow, operation.connection_id)
        if row is None or row.operation_id != operation.id:
            return False
        publish(session, row)
        _end(row)
        row.failure = None
        return True


async def _fail(storage: Storage, operation: Operation, error: Exception) -> None:
    reason, code = failure_of(error)
    async with transaction(storage) as session:
        row = await lock(session, ConnectionRow, operation.connection_id)
        if row is not None and row.operation_id == operation.id:
            record_failure(row, reason, code)


def _end(row: ConnectionRow) -> None:
    row.operation_id = row.operation_kind = row.operation_deadline = None
