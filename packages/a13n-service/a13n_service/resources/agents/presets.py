"""Deployment-maintained presets with independent workspace identities and model selection."""

from collections.abc import Callable, Sequence

from a13n_harness.plugin_factories import HarnessPluginFactoryCatalog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.db import Storage, transaction
from a13n_service.infra.errors import ServiceError, conflict
from a13n_service.providers.registry import Registry
from a13n_service.resources import revisions
from a13n_service.resources.agents.schemas import Agent, AgentConfig, AgentCreate
from a13n_service.resources.agents.service import agent_view, insert_head
from a13n_service.resources.agents.tables import AgentRevisionRow, AgentRow, PresetKind
from a13n_service.resources.agents.validation import validate_config
from a13n_service.resources.models.schemas import ModelConfig
from a13n_service.resources.models.service import resolve_model
from a13n_service.resources.models.tables import ModelRow
from a13n_service.resources.rows import audit_row
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import Principal, WorkspaceScope
from a13n_service.tenancy.tables import WorkspaceRow


async def prepare(
    storage: Storage,
    actor: Principal,
    workspace_id: str,
    *,
    kind: PresetKind,
    name: str,
    description: str,
    configuration: Callable[[str], AgentConfig],
    preferred: Sequence[str],
    registry: Registry,
    plugins: HarnessPluginFactoryCatalog,
) -> Agent:
    async with transaction(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "write")
        # One preparation of a workspace at a time: the first one creates the head. `FOR NO KEY UPDATE` leaves
        # rows that merely reference the workspace free to be written meanwhile.
        await session.execute(
            select(WorkspaceRow.id).where(WorkspaceRow.id == scope.workspace_id).with_for_update(key_share=True)
        )
        head = await session.scalar(
            select(AgentRow)
            .where(
                AgentRow.workspace_id == scope.workspace_id, AgentRow.source == "builtin", AgentRow.preset_kind == kind
            )
            .with_for_update()
        )
        if head is None:
            model = await _model(session, actor, scope, None, preferred)
            body = AgentCreate(name=name, description=description, config=configuration(model))
            head = await insert_head(
                session, actor, scope, body, source="builtin", preset_kind=kind, registry=registry, plugins=plugins
            )
            audit_row(session, actor, head, f"{kind}.prepare")
            return agent_view(head)
        current = (
            None if head.default_revision_id is None else await session.get(AgentRevisionRow, head.default_revision_id)
        )
        model = await _model(session, actor, scope, current, preferred)
        config = await validate_config(
            session, actor, scope, head.id, configuration(model), registry=registry, plugins=plugins
        )
        await revisions.update_head(session, actor, head, {"name": name, "description": description})
        revision, created = await revisions.publish(session, head, AgentRevisionRow, config, actor=actor, note=None)
        if created:
            audit_row(session, actor, head, f"{kind}.prepare", {"revision_id": revision.id})
            await session.flush()
            await session.refresh(head)
        return agent_view(head)


async def _model(
    session: AsyncSession,
    actor: Principal,
    scope: WorkspaceScope,
    current: AgentRevisionRow | None,
    preferred: Sequence[str],
) -> str:
    """The current revision's model while usable, else the most preferred usable model, else the first by key."""
    kept = None if current is None else AgentConfig.model_validate(current.config).model
    rows = (
        await session.scalars(
            select(ModelRow).where(ModelRow.workspace_id == scope.workspace_id, ModelRow.enabled).order_by(ModelRow.key)
        )
    ).all()

    def rank(row: ModelRow) -> tuple[int, int]:
        name = ModelConfig.model_validate(row.config).model_name.rsplit("/", 1)[-1]
        return (row.key != kept, preferred.index(name) if name in preferred else len(preferred))

    for row in sorted(rows, key=rank):
        try:
            await resolve_model(session, actor, scope, row.key, verb="read")
        except ServiceError:
            continue
        return row.key
    raise conflict("workspace", scope.workspace_id, "model_required")
