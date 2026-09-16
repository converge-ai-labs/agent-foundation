"""Validate Agent defaults and exact child template configurations without provisioning targets."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.iam import AuthenticatedActor
from a13n_service.iam.authorization import WorkspaceAction, authorize_workspace
from a13n_service.iam.resource_scope import visible_workspace

from .errors import environment_not_found
from .models import EnvironmentProviderRecord, EnvironmentTemplateRecord, EnvironmentTemplateRevisionRecord


async def authorize_template(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    workspace_id: str,
    template_id: str | None = None,
    revision_id: str | None = None,
) -> None:
    if template_id is None and revision_id is None:
        return
    workspace = await authorize_workspace(
        session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.environment_template_use
    )
    query = (
        select(EnvironmentTemplateRevisionRecord)
        .join(EnvironmentTemplateRecord, EnvironmentTemplateRecord.id == EnvironmentTemplateRevisionRecord.template_id)
        .join(EnvironmentProviderRecord, EnvironmentProviderRecord.id == EnvironmentTemplateRevisionRecord.provider_id)
        .where(
            EnvironmentTemplateRecord.organization_id == workspace.organization_id,
            visible_workspace(EnvironmentTemplateRecord.workspace_id, workspace_id),
            EnvironmentTemplateRecord.archived_at.is_(None),
            EnvironmentProviderRecord.enabled.is_(True),
        )
    )
    query = (
        query.where(EnvironmentTemplateRevisionRecord.id == revision_id)
        if revision_id
        else query.where(
            EnvironmentTemplateRecord.id == template_id,
            EnvironmentTemplateRevisionRecord.id == EnvironmentTemplateRecord.current_revision_id,
        )
    )
    if await session.scalar(query) is None:
        raise environment_not_found()
