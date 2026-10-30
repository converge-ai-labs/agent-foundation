"""Reference inspection and deletion of environment connections, without touching native targets."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.db import Storage, short_session, transaction
from a13n_service.infra.errors import conflict
from a13n_service.infra.http import require_match
from a13n_service.resources.environment_templates.tables import EnvironmentTemplateRow
from a13n_service.resources.providers.schemas import EnvironmentProviderReferences, ProviderReference
from a13n_service.resources.providers.tables import EnvironmentProviderRow
from a13n_service.resources.rows import audit_row, find_row
from a13n_service.runs.environments.tables import EnvironmentRow
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import Principal


async def _references(session: AsyncSession, workspace_id: str, provider_id: str) -> EnvironmentProviderReferences:
    templates = (
        await session.execute(
            select(
                EnvironmentTemplateRow.id,
                EnvironmentTemplateRow.name,
                EnvironmentTemplateRow.enabled,
                func.count().over(),
            )
            .where(
                EnvironmentTemplateRow.workspace_id == workspace_id, EnvironmentTemplateRow.provider_id == provider_id
            )
            .order_by(EnvironmentTemplateRow.id)
            .limit(10)
        )
    ).all()
    environments = (
        await session.execute(
            select(EnvironmentRow.id, EnvironmentRow.name, EnvironmentRow.status, func.count().over())
            .where(EnvironmentRow.workspace_id == workspace_id, EnvironmentRow.provider_id == provider_id)
            .order_by(EnvironmentRow.id)
            .limit(10)
        )
    ).all()
    return EnvironmentProviderReferences(
        template_count=templates[0][3] if templates else 0,
        templates=[
            ProviderReference(id=id, name=name, status="enabled" if enabled else "disabled")
            for id, name, enabled, _ in templates
        ],
        environment_count=environments[0][3] if environments else 0,
        environments=[ProviderReference(id=id, name=name, status=status) for id, name, status, _ in environments],
    )


async def references(
    storage: Storage, actor: Principal, workspace_id: str, provider_id: str
) -> EnvironmentProviderReferences:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        await find_row(session, actor, EnvironmentProviderRow, scope, provider_id, "read")
        return await _references(session, workspace_id, provider_id)


async def delete(
    storage: Storage, actor: Principal, workspace_id: str, provider_id: str, *, if_match: str | None
) -> None:
    async with transaction(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        # FOR UPDATE conflicts with the key-share lock taken by new FK references.
        # Check again inside the deletion transaction, never trust a prior UI probe.
        row = await find_row(session, actor, EnvironmentProviderRow, scope, provider_id, "write", lock=True)
        require_match(if_match, row.id, row.version)
        refs = await _references(session, workspace_id, provider_id)
        if refs.template_count or refs.environment_count:
            raise conflict(row.KIND, row.id, "provider_in_use", references=refs.model_dump(mode="json"))
        audit_row(session, actor, row, "delete")
        await session.delete(row)
        await session.flush()
