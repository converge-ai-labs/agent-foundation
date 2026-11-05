"""Shared selection checks; no preparation, transport or lifecycle I/O."""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.errors import ServiceError, conflict, not_found
from a13n_service.resources.environment_instances.tables import EnvironmentRow


def unusable(environment: EnvironmentRow) -> bool:
    """Whether a permanent failure refuses every new use of the instance until its cause is fixed or it is
    deleted."""
    return environment.failure is not None and environment.failure["permanent"]


def require_usable_state(environment: EnvironmentRow) -> None:
    """Refuse a new use, or a stop that would clear the failure, while a permanent failure makes the instance
    unusable; the refusal names the failure."""
    if unusable(environment):
        assert environment.failure is not None
        raise conflict("environment", environment.id, environment.failure["code"])


def require_usable(environment: EnvironmentRow, principal_id: str) -> None:
    """New use refuses retiring instances, unresolved permanent failures and other principals' devices."""
    if environment.owner_principal_id not in {None, principal_id}:
        raise ServiceError("forbidden", "Private environments are usable only by their owner", {"id": environment.id})
    if environment.status in {"disabled", "deleting", "deleted"}:
        raise conflict("environment", environment.id, f"environment_{environment.status}")
    require_usable_state(environment)


async def lock_environments(
    session: AsyncSession, workspace_id: str, environment_ids: Sequence[str], *, principal_id: str
) -> list[EnvironmentRow]:
    """Share-lock the workspace's instances in ID order, the order every multi-environment writer uses, and check
    each is usable. New use waits for a stop, delete or lifecycle step, which lock the row exclusively, but never
    for other new use."""
    ids = sorted(set(environment_ids))
    if not ids:
        return []
    rows = (
        await session.scalars(
            select(EnvironmentRow)
            .where(EnvironmentRow.workspace_id == workspace_id, EnvironmentRow.id.in_(ids))
            .order_by(EnvironmentRow.id)
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        )
    ).all()
    found = {row.id for row in rows}
    for missing in ids:
        if missing not in found:
            raise not_found("environment", missing)
    for row in rows:
        require_usable(row, principal_id)
    return list(rows)
