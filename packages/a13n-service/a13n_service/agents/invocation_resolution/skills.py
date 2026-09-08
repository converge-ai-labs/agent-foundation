"""Skill preparation and freezing for Agent invocations."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.iam import (
    AuthenticatedActor,
    WorkspaceAction,
    authorize_workspace,
)
from a13n_service.skills.domain import SkillRevisionLock

from ..domain import (
    ResolvedSkillBinding,
    SkillSelection,
)
from ..errors import (
    agent_revision_not_executable,
)
from ..skill_resolution import (
    PreparedSkillLock,
    SkillSelectionInvalid,
    freeze_skill_locks,
    prepare_skill_locks_from_bindings,
    prepare_skill_locks_from_selections,
)
from .contracts import (
    PreparedAgentInvocation,
)


async def prepare_skills(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    organization_id: str,
    workspace_id: str,
    selections: tuple[SkillSelection, ...],
    retained: tuple[ResolvedSkillBinding, ...] | None,
) -> tuple[PreparedSkillLock, ...]:
    if not selections:
        return ()
    await authorize_workspace(
        session,
        actor=actor,
        workspace_id=workspace_id,
        action=WorkspaceAction.skill_read,
    )
    try:
        if retained is not None:
            if tuple((item.skill_key, item.version) for item in retained) != tuple(
                (item.skill_key, item.version) for item in selections
            ):
                raise SkillSelectionInvalid
            return await prepare_skill_locks_from_bindings(
                session,
                organization_id=organization_id,
                workspace_id=workspace_id,
                bindings=retained,
            )
        return await prepare_skill_locks_from_selections(
            session,
            organization_id=organization_id,
            workspace_id=workspace_id,
            selections=selections,
        )
    except SkillSelectionInvalid as error:
        raise agent_revision_not_executable("skill_selection_invalid") from error


async def freeze_skills(
    session: AsyncSession,
    prepared: PreparedAgentInvocation,
) -> tuple[SkillRevisionLock, ...]:
    if not prepared.skills:
        return ()
    await authorize_workspace(
        session,
        actor=prepared.actor,
        workspace_id=prepared.workspace_id,
        action=WorkspaceAction.skill_read,
    )
    try:
        return await freeze_skill_locks(
            session,
            organization_id=prepared.organization_id,
            workspace_id=prepared.workspace_id,
            prepared=prepared.skills,
        )
    except SkillSelectionInvalid as error:
        raise agent_revision_not_executable("skill_selection_invalid") from error
