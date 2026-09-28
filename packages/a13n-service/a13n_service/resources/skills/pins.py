"""Agent revisions pin exact skill revisions; a pin is validated when it is written."""

from collections.abc import Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.errors import at_field, conflict, invalid, not_found
from a13n_service.resources.skills.schemas import SkillPin
from a13n_service.resources.skills.tables import SkillRevisionRow, SkillRow


async def require_pins(session: AsyncSession, workspace_id: str, pins: Mapping[str, SkillPin]) -> None:
    """New pins, by the field path a failure names, may name only existing revisions of this workspace's
    unarchived skills."""
    if not pins:
        return
    rows = (
        await session.execute(
            select(SkillRevisionRow.id, SkillRevisionRow.skill_id, SkillRow.archived_at)
            .join(SkillRow, SkillRow.id == SkillRevisionRow.skill_id)
            .where(
                SkillRevisionRow.workspace_id == workspace_id,
                SkillRevisionRow.id.in_({pin.revision_id for pin in pins.values()}),
            )
        )
    ).all()
    found = {row.id: row for row in rows}
    for path, pin in pins.items():
        with at_field(path):
            row = found.get(pin.revision_id)
            if row is None or row.skill_id != pin.skill_id:
                raise not_found(SkillRevisionRow.KIND, pin.revision_id)
            if row.archived_at is not None:
                raise conflict(SkillRow.KIND, pin.skill_id, "archived")


async def require_distinct_names(session: AsyncSession, workspace_id: str, revisions: Mapping[str, str]) -> None:
    """Pinned revisions, by the field path a failure names, declare distinct SKILL.md names: the model sees each
    skill of an agent by its name."""
    names = dict(
        (
            await session.execute(
                select(SkillRevisionRow.id, SkillRevisionRow.config["name"].astext).where(
                    SkillRevisionRow.workspace_id == workspace_id, SkillRevisionRow.id.in_(set(revisions.values()))
                )
            )
        )
        .tuples()
        .all()
    )
    seen: set[str] = set()
    for path, revision_id in revisions.items():
        if (name := names.get(revision_id)) is None:
            continue
        if name in seen:
            raise invalid(path, f"another skill of the agent is named {name}")
        seen.add(name)
