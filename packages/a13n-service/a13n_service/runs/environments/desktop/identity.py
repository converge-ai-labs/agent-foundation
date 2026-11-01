"""Resolve one control identity for all aliases of a native desktop."""

import hashlib

from a13n_harness.environment.models import EnvironmentState
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.db import now
from a13n_service.infra.errors import conflict
from a13n_service.infra.ids import new_object_id
from a13n_service.runs.environments.desktop.tables import (
    ComputerOperationRow,
    DesktopBlockedRow,
    DesktopControlRow,
    DesktopViewerRow,
)
from a13n_service.runs.environments.schemas import Handle
from a13n_service.runs.environments.tables import EnvironmentRow

TARGET_FIELDS = {
    "e2b": "sandbox_id",
    "docker": "container_id",
    "daytona": "target_id",
    "modal": "target_id",
    "vercel": "backing_id",
    "sprites": "backing_id",
}


def target_key(state: EnvironmentState | None) -> str | None:
    if state is None or not isinstance(state.state, dict):
        return None
    field = TARGET_FIELDS.get(state.provider_key)
    native = state.state.get(field) if field else None
    if not isinstance(native, str) or not native:
        return None
    return hashlib.sha256(f"{state.provider_key}:{native}".encode()).hexdigest()


def environment_key(environment: EnvironmentRow) -> str | None:
    return target_key(Handle.model_validate(environment.handle).state) if environment.handle else None


async def _locked(session: AsyncSession, key: str) -> DesktopControlRow:
    await session.execute(
        insert(DesktopControlRow).values(id=new_object_id("dctrl"), target_key=key).on_conflict_do_nothing()
    )
    row = await session.scalar(
        select(DesktopControlRow)
        .where(DesktopControlRow.target_key == key)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    assert row is not None
    return row


async def target(session: AsyncSession, key: str) -> DesktopControlRow:
    row = await _locked(session, key)
    if row.failure == "environment_lost":
        raise conflict("desktop_control", row.id, "environment_lost")
    return row


async def invalidate(session: AsyncSession, state: EnvironmentState | None) -> None:
    """Retire a proven-lost native target under the lifecycle owner's Environment lock.

    The tombstone also rejects aliases whose own lifecycle has not observed the loss.
    No guest cleanup or availability notice is appropriate for a target that no longer exists.
    """
    key = target_key(state)
    if key is None:
        return
    row = await _locked(session, key)
    if row.failure == "environment_lost":
        return
    current = await now(session)
    row.generation += 1
    row.control = "automation"
    row.failure = "environment_lost"
    row.controller_id = row.controller_thread_id = None
    row.expires_at = row.viewer = row.grant_id = row.guest_generation = None
    row.claim_id = row.claim_until = row.due_at = None
    row.grant_dispatched = False
    # Target destruction ends possible execution without inventing effect/cleanup evidence.
    await session.execute(
        update(ComputerOperationRow)
        .where(ComputerOperationRow.control_id == row.id, ComputerOperationRow.settled_at.is_(None))
        .values(settled_at=current)
    )
    await session.execute(
        update(DesktopViewerRow).where(DesktopViewerRow.control_id == row.id).values(expires_at=current)
    )
    await session.execute(
        update(DesktopBlockedRow).where(DesktopBlockedRow.control_id == row.id).values(suppressed=True)
    )
