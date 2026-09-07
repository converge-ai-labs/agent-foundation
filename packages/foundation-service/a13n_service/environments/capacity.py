"""Workspace capacity admission, serialized before acquiring an Environment row."""

from dataclasses import dataclass

from a13n_environment_provider import EnvironmentError
from sqlalchemy import func, select
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
        """Reserve only new target/use slots; recovery and shared users reuse their slots."""
        await session.flush()
        if environment.ownership == "managed" and environment.status in {"unprepared", "deleted"}:
            targets = await session.scalar(
                select(func.count())
                .select_from(EnvironmentRecord)
                .where(
                    EnvironmentRecord.workspace_id == environment.workspace_id,
                    EnvironmentRecord.ownership == "managed",
                    EnvironmentRecord.status.not_in(("unprepared", "deleted")),
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
        if environment.status in {"unprepared", "deleted"}:
            environment.status = "unavailable"


def _exceeded(kind: str, limit: int) -> EnvironmentError:
    return EnvironmentError(
        "Workspace Environment capacity is exhausted. Release capacity before retrying.",
        code="environment_capacity_exceeded",
        details={"capacity": kind, "limit": limit},
        retry_hint="dependency_change",
    )


DEFAULT_CAPACITY_LIMITS = CapacityLimits()
