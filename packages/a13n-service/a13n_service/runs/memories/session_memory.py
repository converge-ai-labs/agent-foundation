"""Session Space selection and one-time Agent defaults. Spaces own organization policy."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.errors import not_found
from a13n_service.resources.memories.schemas import SessionMemorySettings
from a13n_service.resources.memories.tables import MemorySpaceRow
from a13n_service.runs.tables import SessionRow


async def configure(
    session: AsyncSession,
    owner: SessionRow,
    settings: SessionMemorySettings,
) -> None:
    """Update the caller-locked Session. An explicit list replaces, rather than merges, selection."""
    values = owner.memory | settings.model_dump(mode="json", exclude_unset=True)
    resolved = SessionMemorySettings.model_validate(values)
    if resolved.spaces:
        ids = {item.space_id for item in resolved.spaces}
        existing = set(
            await session.scalars(
                select(MemorySpaceRow.id)
                .where(
                    MemorySpaceRow.workspace_id == owner.workspace_id,
                    MemorySpaceRow.id.in_(ids),
                )
                .order_by(MemorySpaceRow.id)
                .with_for_update(read=True)
            )
        )
        for binding in resolved.spaces:
            if binding.space_id not in existing:
                raise not_found(MemorySpaceRow.KIND, binding.space_id)
    owner.memory = values


async def inherit_defaults(
    session: AsyncSession,
    session_id: str,
    defaults: SessionMemorySettings,
) -> None:
    """First business acceptance snapshots defaults, with explicit Session fields taking precedence."""
    owner = await session.scalar(
        select(SessionRow)
        .where(SessionRow.id == session_id, SessionRow.last_run_id.is_(None))
        .with_for_update(key_share=True)
        .execution_options(populate_existing=True)
    )
    if owner is None:
        await session.get_one(SessionRow, session_id, populate_existing=True)
        return
    settings = SessionMemorySettings.model_validate(defaults.model_dump(mode="json") | owner.memory)
    await configure(session, owner, settings)
    await session.flush()
