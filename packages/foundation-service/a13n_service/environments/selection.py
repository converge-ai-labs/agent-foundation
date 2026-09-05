"""One read-only selection rule and transactional Environment allocation."""

from datetime import datetime
from enum import Enum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.ids import new_object_id

from .domain import EnvironmentSelection, NewEnvironmentSelection
from .errors import invalid_environment
from .models import (
    EnvironmentProviderRecord,
    EnvironmentRecord,
    EnvironmentTemplateRecord,
    EnvironmentTemplateRevisionRecord,
)


class Omitted(Enum):
    UNSET = "omitted"


async def resolve_selection(
    session: AsyncSession, *, workspace_id: str, choice: EnvironmentSelection
) -> EnvironmentRecord | EnvironmentTemplateRevisionRecord:
    if isinstance(choice, NewEnvironmentSelection):
        template = await session.scalar(
            select(EnvironmentTemplateRecord).where(
                EnvironmentTemplateRecord.id == choice.template_id,
                EnvironmentTemplateRecord.workspace_id == workspace_id,
                EnvironmentTemplateRecord.archived_at.is_(None),
            )
        )
        if template is None:
            raise invalid_environment("Environment template is unavailable")
        selected = await session.scalar(
            select(EnvironmentTemplateRevisionRecord).where(
                EnvironmentTemplateRevisionRecord.template_id == template.id,
                EnvironmentTemplateRevisionRecord.version == (choice.version or template.version),
            )
        )
    else:
        selected = await session.scalar(
            select(EnvironmentRecord).where(
                EnvironmentRecord.id == choice.environment_id,
                EnvironmentRecord.workspace_id == workspace_id,
            )
        )
    if selected is None:
        raise invalid_environment("Environment selection is unavailable")
    provider = await session.get(EnvironmentProviderRecord, selected.provider_id)
    if provider is None or provider.workspace_id != workspace_id or not provider.enabled:
        raise invalid_environment("Environment Provider is unavailable")
    return selected


def intersect_access(access: str, ceiling: str | None = None) -> str:
    ranks = {"read_only": 0, "read_write": 1, "full": 2}
    return min((access, ceiling or access), key=ranks.__getitem__)


def allocate_selection(
    session: AsyncSession, selected: EnvironmentRecord | EnvironmentTemplateRevisionRecord, *, now: datetime
) -> EnvironmentRecord:
    if isinstance(selected, EnvironmentRecord):
        return selected
    environment = EnvironmentRecord(
        id=new_object_id("env"),
        organization_id=selected.organization_id,
        workspace_id=selected.workspace_id,
        provider_id=selected.provider_id,
        template_revision_id=selected.id,
        ownership="managed",
        access=selected.to_resource().access.value,
        generation=0,
        status="unprepared",
        retention_condition="idle",
        condition_since=now,
        operation_generation=0,
        created_at=now,
        updated_at=now,
    )
    session.add(environment)
    return environment
