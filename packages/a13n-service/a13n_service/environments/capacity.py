"""Workspace capacity admission, serialized before acquiring an Environment row."""

from dataclasses import dataclass

from a13n_harness.providers.environment.models import EnvironmentError
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.iam.models import WorkspaceRecord

from .models import EnvironmentRecord
from .policy import DEFAULT_MAX_ACTIVE, DEFAULT_MAX_TARGETS
from .retention import active_use_exists, has_active_use


@dataclass(frozen=True, slots=True)
class CapacityLimits:
    max_targets: int = DEFAULT_MAX_TARGETS
    max_active: int = DEFAULT_MAX_ACTIVE

    def __post_init__(self) -> None:
        if self.max_targets < 1 or self.max_active < 1:
            raise ValueError("Environment capacity limits must be positive")

    async def lock_workspace(self, session: AsyncSession, environment_id: str) -> None:
        workspace_id = select(EnvironmentRecord.workspace_id).where(EnvironmentRecord.id == environment_id)
        workspace = await session.scalar(
            select(WorkspaceRecord.id).where(WorkspaceRecord.id == workspace_id.scalar_subquery()).with_for_update()
        )
        if workspace is None:
            raise ValueError("Environment Workspace is unavailable")

    async def admit(self, session: AsyncSession, environment: EnvironmentRecord) -> None:
        """Check new slots before recording prepare/use in the same locked transaction."""
        await session.flush()
        if (
            environment.ownership == "managed"
            and environment.status in {"unprepared", "deleted"}
            and not (environment.operation_id is not None and environment.operation_action == "prepare")
        ):
            targets = await session.scalar(
                select(func.count())
                .select_from(EnvironmentRecord)
                .where(
                    EnvironmentRecord.workspace_id == environment.workspace_id,
                    EnvironmentRecord.ownership == "managed",
                    or_(
                        EnvironmentRecord.status.not_in(("unprepared", "deleted")),
                        and_(
                            EnvironmentRecord.operation_id.is_not(None), EnvironmentRecord.operation_action == "prepare"
                        ),
                    ),
                )
            )
            if targets is not None and targets >= self.max_targets:
                raise _exceeded("targets", self.max_targets)
        if not await has_active_use(session, environment.id):
            active = await session.scalar(
                select(func.count())
                .select_from(EnvironmentRecord)
                .where(
                    EnvironmentRecord.workspace_id == environment.workspace_id, active_use_exists(EnvironmentRecord.id)
                )
            )
            if active is not None and active >= self.max_active:
                raise _exceeded("active", self.max_active)


def _exceeded(kind: str, limit: int) -> EnvironmentError:
    return EnvironmentError(
        "Workspace Environment capacity is exhausted. Release capacity before retrying.",
        code="environment_capacity_exceeded",
        details={"capacity": kind, "limit": limit},
        retry_hint="dependency_change",
    )


DEFAULT_CAPACITY_LIMITS = CapacityLimits()
