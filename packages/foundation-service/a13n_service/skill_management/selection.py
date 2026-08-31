"""Agent publication locks and immutable Turn-time Skill selection."""

from __future__ import annotations

from dataclasses import dataclass

from a13n_harness.environment import EnvironmentAction, EnvironmentPermissionSet
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.iam import AuthenticatedActor, authorize_agent_skill_binding
from a13n_service.storage import short_session

from .domain import (
    FoundationAgentSkillSelection,
    FoundationAgentSkillSelectionRequest,
    FoundationSkillRevisionLock,
    ManagedSkillPackageManifest,
    TurnSkillSelectionRequest,
)
from .errors import SkillManagementError
from .models import WorkspaceSkillRecord, WorkspaceSkillRevisionRecord

MAX_AGENT_SKILLS = 512
SKILL_MATERIALIZATION_ACTIONS = frozenset(
    {
        EnvironmentAction.FILE_LIST,
        EnvironmentAction.FILE_STAT,
        EnvironmentAction.FILE_READ_TEXT,
        EnvironmentAction.FILE_READ_BYTES,
        EnvironmentAction.FILE_WRITE_BYTES,
        EnvironmentAction.FILE_MKDIR,
        EnvironmentAction.FILE_REMOVE,
    }
)


@dataclass(frozen=True, slots=True)
class PreparedAgentSkillSelection:
    """Authorized publication evidence to recheck in the final short transaction."""

    actor: AuthenticatedActor
    organization_id: str
    workspace_id: str
    agent_preset_id: str
    request: FoundationAgentSkillSelectionRequest
    selection: FoundationAgentSkillSelection


class FrozenSkillSelectionError(ValueError):
    """Persisted Turn Skill names do not match their immutable Agent catalog."""


@dataclass(frozen=True, slots=True)
class _ResolvedRevision:
    revision: WorkspaceSkillRevisionRecord
    skill: WorkspaceSkillRecord


class AgentSkillLockResolver:
    """Resolve exact revision IDs without ever consulting mutable Skill heads."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def prepare(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        agent_preset_id: str,
        request: FoundationAgentSkillSelectionRequest,
        permission_ceiling: EnvironmentPermissionSet | None,
    ) -> PreparedAgentSkillSelection:
        async with short_session(self._sessions) as session:
            authorized = await authorize_agent_skill_binding(
                session,
                actor=actor,
                workspace_id=workspace_id,
                agent_preset_id=agent_preset_id,
            )
            _require_materialization_permissions(request, permission_ceiling)
            records = await _load_revisions(
                session,
                organization_id=authorized.organization_id,
                workspace_id=workspace_id,
                revision_ids=request.available_revision_ids,
                for_update=False,
            )
        selection = _resolve_selection(request, records)
        return PreparedAgentSkillSelection(
            actor=actor,
            organization_id=authorized.organization_id,
            workspace_id=workspace_id,
            agent_preset_id=agent_preset_id,
            request=request,
            selection=selection,
        )

    async def freeze_in_transaction(
        self,
        session: AsyncSession,
        *,
        prepared: PreparedAgentSkillSelection,
        permission_ceiling: EnvironmentPermissionSet | None,
    ) -> FoundationAgentSkillSelection:
        """Reauthorize and prove the prepared locks still publish exactly as resolved."""

        authorized = await authorize_agent_skill_binding(
            session,
            actor=prepared.actor,
            workspace_id=prepared.workspace_id,
            agent_preset_id=prepared.agent_preset_id,
        )
        _require_materialization_permissions(prepared.request, permission_ceiling)
        if authorized.organization_id != prepared.organization_id:
            raise _selection_changed()
        records = await _load_revisions(
            session,
            organization_id=prepared.organization_id,
            workspace_id=prepared.workspace_id,
            revision_ids=prepared.request.available_revision_ids,
            for_update=True,
        )
        try:
            current = _resolve_selection(prepared.request, records)
        except SkillManagementError as error:
            raise _selection_changed() from error
        if current != prepared.selection:
            raise _selection_changed()
        return current


def resolve_turn_skill_selection(
    selection: FoundationAgentSkillSelection,
    request: TurnSkillSelectionRequest,
) -> tuple[str, ...]:
    """Resolve one effective Turn tuple in immutable available-catalog order."""

    available = tuple(item.skill_name for item in selection.available)
    if request.has_override:
        requested = request.selected_skill_names
        if requested is None:
            raise _turn_selection_invalid()
    elif selection.default_mode == "all":
        requested = available
    else:
        requested = selection.default_names
    unknown = set(requested) - set(available)
    if unknown:
        raise _turn_selection_invalid()
    requested_set = set(requested)
    return tuple(name for name in available if name in requested_set)


def validate_frozen_turn_skill_selection(
    selection: FoundationAgentSkillSelection,
    selected_skill_names: tuple[str, ...],
) -> tuple[FoundationSkillRevisionLock, ...]:
    """Validate persisted Turn state and return its selected immutable locks."""

    if len(selected_skill_names) > MAX_AGENT_SKILLS or len(selected_skill_names) != len(set(selected_skill_names)):
        raise FrozenSkillSelectionError("The accepted Turn Skill selection is invalid.")
    available = {item.skill_name: item for item in selection.available}
    if any(name not in available for name in selected_skill_names):
        raise FrozenSkillSelectionError("The accepted Turn Skill selection is invalid.")
    ordered = tuple(item for item in selection.available if item.skill_name in set(selected_skill_names))
    if tuple(item.skill_name for item in ordered) != selected_skill_names:
        raise FrozenSkillSelectionError("The accepted Turn Skill selection is invalid.")
    return ordered


async def _load_revisions(
    session: AsyncSession,
    *,
    organization_id: str,
    workspace_id: str,
    revision_ids: tuple[str, ...],
    for_update: bool,
) -> dict[str, _ResolvedRevision]:
    if not revision_ids:
        return {}
    statement = (
        select(WorkspaceSkillRevisionRecord, WorkspaceSkillRecord)
        .join(WorkspaceSkillRecord, WorkspaceSkillRecord.id == WorkspaceSkillRevisionRecord.skill_id)
        .where(
            WorkspaceSkillRevisionRecord.organization_id == organization_id,
            WorkspaceSkillRevisionRecord.workspace_id == workspace_id,
            WorkspaceSkillRevisionRecord.id.in_(revision_ids),
            WorkspaceSkillRecord.organization_id == organization_id,
            WorkspaceSkillRecord.workspace_id == workspace_id,
            WorkspaceSkillRecord.deleted_at.is_(None),
        )
    )
    if for_update:
        statement = statement.with_for_update()
    rows = (await session.execute(statement)).all()
    return {revision.id: _ResolvedRevision(revision=revision, skill=skill) for revision, skill in rows}


def _resolve_selection(
    request: FoundationAgentSkillSelectionRequest,
    records: dict[str, _ResolvedRevision],
) -> FoundationAgentSkillSelection:
    if len(records) != len(request.available_revision_ids):
        raise SkillManagementError(
            "skill_revision_unavailable",
            "A selected Skill revision is unavailable.",
            status_code=404,
        )
    locks: list[FoundationSkillRevisionLock] = []
    try:
        for revision_id in request.available_revision_ids:
            revision = records[revision_id].revision
            manifest = ManagedSkillPackageManifest.model_validate(revision.manifest)
            if revision.content_digest != manifest.content_digest:
                raise ValueError("revision digest does not match its manifest")
            locks.append(
                FoundationSkillRevisionLock(
                    skill_revision_id=revision.id,
                    skill_name=manifest.skill_name,
                    content_digest=manifest.content_digest,
                )
            )
    except (KeyError, ValidationError, ValueError) as error:
        raise SkillManagementError(
            "skill_revision_invalid",
            "A selected Skill revision is invalid.",
            status_code=500,
        ) from error
    names = tuple(item.skill_name for item in locks)
    if len(names) != len(set(names)):
        raise SkillManagementError(
            "skill_selection_ambiguous",
            "Selected Skill revisions contain duplicate final names.",
            status_code=409,
        )
    unknown_defaults = set(request.default_names) - set(names)
    if unknown_defaults:
        raise SkillManagementError(
            "skill_selection_invalid",
            "The default Skill selection contains an unavailable name.",
            status_code=400,
        )
    default_set = set(request.default_names)
    ordered_defaults = tuple(name for name in names if name in default_set)
    return FoundationAgentSkillSelection(
        available=tuple(locks),
        materialization_mount=request.materialization_mount,
        default_mode=request.default_mode,
        default_names=ordered_defaults,
    )


def _require_materialization_permissions(
    request: FoundationAgentSkillSelectionRequest,
    permission_ceiling: EnvironmentPermissionSet | None,
) -> None:
    if not request.available_revision_ids:
        return
    if permission_ceiling is None or not SKILL_MATERIALIZATION_ACTIONS.issubset(permission_ceiling.operations):
        raise SkillManagementError(
            "skill_materialization_mount_invalid",
            "The Skill materialization mount lacks required file permissions.",
            status_code=400,
        )


def _selection_changed() -> SkillManagementError:
    return SkillManagementError(
        "skill_selection_changed",
        "The selected Skill revisions changed during Agent publication.",
        status_code=409,
    )


def _turn_selection_invalid() -> SkillManagementError:
    return SkillManagementError(
        "skill_selection_invalid",
        "The requested Skill selection is invalid.",
        status_code=400,
    )
