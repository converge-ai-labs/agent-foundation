"""Workspace media-understanding defaults, kept in `workspaces.settings.media`.

An agent that selects no model for a media kind uses the workspace's. Replacing the defaults validates each
model as an agent selection would; a default that later becomes unusable is skipped at execution rather than
failing every run of the workspace.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.audit import record
from a13n_service.infra.db import Storage, lock, short_session
from a13n_service.infra.errors import at_field
from a13n_service.infra.http import require_match
from a13n_service.resources.models.schemas import MediaDefaults, MediaUnderstandingSelection
from a13n_service.resources.models.service import resolve_media_model
from a13n_service.tenancy.access import Access, administering_workspace, workspace_scope
from a13n_service.tenancy.authorize import Principal
from a13n_service.tenancy.tables import WorkspaceRow


async def workspace_media(session: AsyncSession, workspace_id: str) -> MediaUnderstandingSelection:
    row = await session.get_one(WorkspaceRow, workspace_id)
    return MediaUnderstandingSelection.model_validate(row.settings.get("media", {}))


async def get_media_defaults(storage: Storage, actor: Principal, workspace_id: str) -> MediaDefaults:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        return _view(await session.get_one(WorkspaceRow, scope.workspace_id))


async def replace_media_defaults(
    storage: Storage,
    access: Access,
    actor: Principal,
    workspace_id: str,
    body: MediaUnderstandingSelection,
    *,
    if_match: str | None,
) -> MediaDefaults:
    async with administering_workspace(storage, access, actor, workspace_id, action="workspace.media.replace") as (
        session,
        scope,
    ):
        row = await lock(session, WorkspaceRow, scope.workspace_id)
        assert row is not None
        require_match(if_match, row.id, row.version)
        for kind, key in body.selections().items():
            with at_field(kind):
                await resolve_media_model(session, actor, scope, kind, key, verb="read")
        media = body.model_dump(mode="json", exclude_none=True)
        if media != row.settings.get("media", {}):
            row.settings = {**row.settings, "media": media}
            record(
                session,
                scope,
                actor_id=actor.id,
                action="workspace.media.replace",
                target_kind="workspace",
                target_id=scope.workspace_id,
                details={"media": media},
            )
            await session.flush()
            await session.refresh(row)
        return _view(row)


def _view(row: WorkspaceRow) -> MediaDefaults:
    return MediaDefaults(id=row.id, version=row.version, **row.settings.get("media", {}))
