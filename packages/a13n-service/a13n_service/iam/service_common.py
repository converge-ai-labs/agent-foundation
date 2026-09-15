"""Short identity transactions, safe failures, and canonical audit construction."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.application_errors import ApplicationError, ErrorCategory
from a13n_service.etags import etag_matches, resource_etag
from a13n_service.ids import new_object_id
from a13n_service.storage import transaction
from a13n_service.temporal import utc_now

from .audit import AuthenticationAuditActor, SystemAuditActor, security_audit_record
from .domain import AuthenticatedActor
from .models import OrganizationRecord, RoleBindingRecord, UserRecord, WorkspaceRecord


def identity_error(code: str, message: str, category: ErrorCategory = ErrorCategory.conflict) -> ApplicationError:
    return ApplicationError(code, message, category=category)


def not_found() -> ApplicationError:
    return identity_error(
        "identity_not_found", "The requested identity resource was not found.", ErrorCategory.not_found
    )


def require_version(current: int, expected: int) -> None:
    if current != expected:
        raise identity_error("version_conflict", "The identity resource changed after it was read.")


@asynccontextmanager
async def identity_transaction(
    sessions: async_sessionmaker[AsyncSession], organization_id: str
) -> AsyncIterator[AsyncSession]:
    """Serialize membership changes per Organization, including last-Admin checks.

    No password work or external I/O belongs inside this scope.
    """
    async with transaction(sessions) as session:
        organization = await session.scalar(
            select(OrganizationRecord).where(OrganizationRecord.id == organization_id).with_for_update()
        )
        if organization is None:
            raise not_found()
        yield session


async def lock_bootstrap(session: AsyncSession) -> None:
    """Serialize the initially empty OSS database without a second identity table."""
    # A fixed, private namespace; the canonical connection statement timeout bounds waiting.
    await session.execute(text("SELECT pg_advisory_xact_lock(134644, 1)"))


async def singleton_organization(session: AsyncSession) -> OrganizationRecord:
    rows = (await session.scalars(select(OrganizationRecord).limit(2))).all()
    if len(rows) != 1:
        raise identity_error(
            "oss_organization_invalid", "OSS requires exactly one Organization.", ErrorCategory.unavailable
        )
    return rows[0]


def audit(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor | SystemAuditActor | AuthenticationAuditActor,
    action: str,
    resource_type: str,
    resource_id: str | None,
    organization_id: str | None,
    workspace_id: str | None = None,
    now: datetime | None = None,
    outcome: str = "success",
) -> None:
    session.add(
        security_audit_record(
            audit_id=new_object_id("aud"),
            actor=actor,
            organization_id=organization_id,
            workspace_id=workspace_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            outcome=outcome,
            occurred_at=now or utc_now(),
            details=None,
        )
    )


def require_etag(row: UserRecord | OrganizationRecord | WorkspaceRecord | RoleBindingRecord, if_match: str) -> None:
    if not etag_matches(if_match, resource_etag(row.id, row.updated_at)):
        raise identity_error(
            "precondition_failed", "The identity resource changed after it was read.", ErrorCategory.stale_version
        )
