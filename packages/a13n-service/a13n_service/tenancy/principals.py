"""Principals as views show them: who holds, issued or did something, with a user's profile image."""

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra import images
from a13n_service.tenancy.schemas import PrincipalSummary
from a13n_service.tenancy.tables import PrincipalRow


def avatar_url(row: PrincipalRow) -> str | None:
    return images.url(f"/api/v1/users/{row.id}/avatar", row.image)


def principal_summary(row: PrincipalRow) -> PrincipalSummary:
    return PrincipalSummary(
        id=row.id,
        kind=row.kind,
        name=row.name,
        email=row.email,
        oidc_email=row.oidc_email,
        status=row.status,
        image_url=avatar_url(row),
    )


async def principal_summaries(session: AsyncSession, principal_ids: Iterable[str]) -> dict[str, PrincipalSummary]:
    """The named principals by ID."""
    rows = await session.scalars(select(PrincipalRow).where(PrincipalRow.id.in_(set(principal_ids))))
    return {row.id: principal_summary(row) for row in rows}
