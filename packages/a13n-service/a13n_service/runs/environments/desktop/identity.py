"""Resolve one control identity for all aliases of a native desktop."""

import hashlib

from a13n_harness.environment.models import EnvironmentState
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.ids import new_object_id
from a13n_service.runs.environments.desktop.tables import DesktopControlRow
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


async def target(session: AsyncSession, key: str) -> DesktopControlRow:
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


async def aliases(session: AsyncSession, environment: EnvironmentRow) -> list[str]:
    state = Handle.model_validate(environment.handle).state if environment.handle else None
    if state is None or not isinstance(state.state, dict):
        return [environment.id]
    field = TARGET_FIELDS.get(state.provider_key)
    if field is None:
        return [environment.id]
    return list(
        await session.scalars(
            select(EnvironmentRow.id).where(
                EnvironmentRow.handle["state"]["provider_key"].astext == state.provider_key,
                EnvironmentRow.handle["state"]["state"][field].astext == state.state[field],
            )
        )
    )
