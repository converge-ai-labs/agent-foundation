"""Shared selection checks; no preparation, transport or lifecycle I/O."""

from collections.abc import Sequence

from sqlalchemy import ColumnElement, SQLColumnExpression, exists, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from a13n_service.infra.errors import ServiceError, conflict, not_found
from a13n_service.resources.environment_instances.tables import EnvironmentRow

ENVIRONMENT_LOST = "environment_lost"


def superseded(environment_id: SQLColumnExpression[str] | str) -> ColumnElement[bool]:
    """A successor keeps the predecessor's quota slot, even after either row is retired."""
    successor = aliased(EnvironmentRow)
    return exists(
        select(successor.id).where(successor.replaces_environment_id == environment_id).correlate_except(successor)
    )


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


async def lock_environment_rows(
    session: AsyncSession, workspace_id: str, environment_ids: Sequence[str]
) -> dict[str, EnvironmentRow]:
    """Share-lock the selected instances in ID order before deciding whether each can be mounted."""
    ids = sorted(set(environment_ids))
    if not ids:
        return {}
    rows = (
        await session.scalars(
            select(EnvironmentRow)
            .where(EnvironmentRow.workspace_id == workspace_id, EnvironmentRow.id.in_(ids))
            .order_by(EnvironmentRow.id)
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        )
    ).all()
    found = {row.id: row for row in rows}
    for environment_id in ids:
        environment = found.get(environment_id)
        if environment is None:
            raise not_found("environment", environment_id)
        # An exact selection of a lost ID keeps its refusal even after maintenance retires the provider handle.
        if (
            environment.status in {"deleting", "deleted"}
            and environment.kind == "managed"
            and not environment.retired_for_idle
            and await session.scalar(select(superseded(environment_id)))
        ):
            raise conflict("environment", environment_id, ENVIRONMENT_LOST)
    return found


async def lock_environments(
    session: AsyncSession, workspace_id: str, environment_ids: Sequence[str], *, principal_id: str
) -> list[EnvironmentRow]:
    """Lock the selected instances and require each to be usable by the principal."""
    rows = await lock_environment_rows(session, workspace_id, environment_ids)
    for row in rows.values():
        require_usable(row, principal_id)
    return list(rows.values())
