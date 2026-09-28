"""Short, authorized transactions for agent heads and immutable revisions.

Every configuration a revision or a run freezes passes `validation.validate_config`, which needs the provider
definitions and the plugin factories the deployment installed.
"""

from dataclasses import dataclass

from a13n_harness.plugin_factories import HarnessPluginFactoryCatalog
from pydantic import JsonValue, ValidationError
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra import cursors, images
from a13n_service.infra.db import Storage, short_session, transaction
from a13n_service.infra.errors import conflict, disabled, invalid, not_found
from a13n_service.infra.ids import new_object_id
from a13n_service.infra.labels import label_filter
from a13n_service.infra.objects.interface import ObjectStore
from a13n_service.providers.registry import Registry, web_operations
from a13n_service.resources import revisions
from a13n_service.resources.agents import toolsets
from a13n_service.resources.agents.schemas import (
    Agent,
    AgentConfig,
    AgentCreate,
    AgentDuplicate,
    AgentOverride,
    AgentPage,
    AgentRevision,
    AgentRevisionCreate,
    AgentRevisionPage,
    AgentSource,
    AgentUpdate,
    AgentValidate,
    apply_override,
    freeze_override,
)
from a13n_service.resources.agents.tables import AgentRevisionRow, AgentRow
from a13n_service.resources.agents.validation import validate_config
from a13n_service.resources.rows import audit_row, given
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import ExecutionAuthority, Principal, WorkspaceScope


async def resolve_agent(session: AsyncSession, workspace_id: str, reference: str, *, lock: bool = False) -> AgentRow:
    return await revisions.resolve_head(session, AgentRow, workspace_id, reference, lock=lock)


def agent_view(head: AgentRow) -> Agent:
    return Agent(
        id=head.id,
        organization_id=head.organization_id,
        workspace_id=head.workspace_id,
        name=head.name,
        description=head.description,
        labels=head.labels,
        default_revision_id=head.default_revision_id,
        source=head.source,
        image_url=images.url(f"/api/v1/agents/{head.id}/avatar", head.image),
        archived_at=head.archived_at,
        version=head.version,
        created_by_id=head.created_by_id,
        updated_by_id=head.updated_by_id,
        created_at=head.created_at,
        updated_at=head.updated_at,
    )


async def create_agent(
    storage: Storage,
    actor: Principal,
    workspace_id: str,
    body: AgentCreate,
    *,
    registry: Registry,
    plugins: HarnessPluginFactoryCatalog,
) -> Agent:
    async with transaction(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "write")
        head = await insert_head(session, actor, scope, body, registry=registry, plugins=plugins)
        audit_row(session, actor, head, "create")
        return agent_view(head)


async def duplicate_agent(
    storage: Storage,
    actor: Principal,
    workspace_id: str,
    agent_id: str,
    body: AgentDuplicate,
    *,
    registry: Registry,
    plugins: HarnessPluginFactoryCatalog,
) -> Agent:
    """A new custom head whose first revision copies one revision of the source, revalidated as the actor's."""
    async with transaction(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "write")
        source = await resolve_agent(session, scope.workspace_id, agent_id)
        if source.archived_at is not None:
            raise conflict(source.KIND, source.id, "archived")
        revision_id = body.revision_id or source.default_revision_id
        if revision_id is None:
            raise not_found(AgentRevisionRow.KIND, source.id)
        revision = await _revision(session, source, revision_id)
        copy = AgentCreate(
            **body.model_dump(exclude={"revision_id"}), config=AgentConfig.model_validate(revision.config)
        )
        head = await insert_head(session, actor, scope, copy, registry=registry, plugins=plugins)
        audit_row(session, actor, head, "duplicate", {"source_revision_id": revision.id})
        return agent_view(head)


async def insert_head(
    session: AsyncSession,
    actor: Principal,
    scope: WorkspaceScope,
    body: AgentCreate,
    *,
    source: str = "custom",
    registry: Registry,
    plugins: HarnessPluginFactoryCatalog,
) -> AgentRow:
    head = AgentRow(
        id=new_object_id("ap"),
        organization_id=scope.organization_id,
        workspace_id=scope.workspace_id,
        name=body.name,
        description=body.description,
        labels=body.labels,
        source=source,
        created_by_id=actor.id,
        updated_by_id=actor.id,
    )
    config = await validate_config(session, actor, scope, head.id, body.config, registry=registry, plugins=plugins)
    session.add(head)
    await session.flush()
    await revisions.publish(session, head, AgentRevisionRow, config, actor=actor, note=None)
    await session.flush()
    await session.refresh(head)
    return head


async def update_agent(
    storage: Storage, actor: Principal, workspace_id: str, agent_id: str, body: AgentUpdate, *, if_match: str | None
) -> Agent:
    async with transaction(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "write")
        head = await revisions.open_head(session, AgentRow, scope.workspace_id, agent_id, if_match)
        await revisions.update_head(session, actor, head, given(body, "name", "description", "labels"))
        return agent_view(head)


async def change_avatar(
    storage: Storage,
    objects: ObjectStore,
    actor: Principal,
    workspace_id: str,
    agent_id: str,
    data: bytes | None,
    *,
    if_match: str | None,
) -> Agent:
    """Replace the avatar with `data`, or remove it with None; bytes are stored only for an editable agent."""
    image = None
    if data is not None:
        async with short_session(storage) as session:
            scope = await workspace_scope(session, actor, workspace_id, "write")
            head = await resolve_agent(session, scope.workspace_id, agent_id)
            revisions.require_open(head, if_match)
        image = await images.store(objects, images.prefix_for(head.id, organization_id=scope.organization_id), data)
    async with transaction(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "write")
        head = await revisions.open_head(session, AgentRow, scope.workspace_id, agent_id, if_match)
        await revisions.update_head(session, actor, head, {"image": image})
        return agent_view(head)


async def get_avatar(
    storage: Storage, actor: Principal, workspace_id: str, agent_id: str
) -> dict[str, JsonValue] | None:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        return (await resolve_agent(session, scope.workspace_id, agent_id)).image


async def set_archived(
    storage: Storage,
    actor: Principal,
    workspace_id: str,
    agent_id: str,
    *,
    archived: bool,
    if_match: str | None,
) -> Agent:
    async with transaction(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "write")
        head = await resolve_agent(session, scope.workspace_id, agent_id, lock=True)
        await revisions.set_archived(session, actor, head, archived=archived, if_match=if_match)
        return agent_view(head)


async def _revision(session: AsyncSession, head: AgentRow, revision_id: str) -> AgentRevisionRow:
    return await revisions.find_revision(session, AgentRevisionRow, head.id, revision_id)


async def create_revision(
    storage: Storage,
    actor: Principal,
    workspace_id: str,
    agent_id: str,
    body: AgentRevisionCreate,
    *,
    if_match: str | None,
    registry: Registry,
    plugins: HarnessPluginFactoryCatalog,
) -> AgentRevision:
    """The new revision, or the default one when the validated configuration equals it."""
    async with transaction(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "write")
        head = await revisions.open_head(session, AgentRow, scope.workspace_id, agent_id, if_match)
        config = await validate_config(session, actor, scope, head.id, body.config, registry=registry, plugins=plugins)
        revision, created = await revisions.publish(
            session, head, AgentRevisionRow, config, actor=actor, note=body.note, make_default=body.make_default
        )
        if created:
            audit_row(session, actor, head, "revision.create", {"revision_id": revision.id})
            await session.flush()
            await session.refresh(revision)
        return AgentRevision.model_validate(revision)


async def validate_revision(
    storage: Storage,
    actor: Principal,
    workspace_id: str,
    body: AgentValidate,
    *,
    registry: Registry,
    plugins: HarnessPluginFactoryCatalog,
) -> None:
    """Check `body.config` as creating a revision checks it, in a session that writes nothing."""
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "write")
        agent_id = None
        if body.agent_id is not None:
            agent_id = (await resolve_agent(session, scope.workspace_id, body.agent_id)).id
        await validate_config(session, actor, scope, agent_id, body.config, registry=registry, plugins=plugins)


async def get_agent(storage: Storage, actor: Principal, workspace_id: str, agent_id: str) -> Agent:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        return agent_view(await resolve_agent(session, scope.workspace_id, agent_id))


async def get_revision(
    storage: Storage, actor: Principal, workspace_id: str, agent_id: str, revision_id: str
) -> AgentRevision:
    return await revisions.get_revision(
        storage, actor, AgentRow, AgentRevisionRow, _revision_view, workspace_id, agent_id, revision_id
    )


async def list_agents(
    storage: Storage,
    actor: Principal,
    workspace_id: str,
    *,
    labels: list[str],
    q: str | None = None,
    archived: bool | None = None,
    source: AgentSource | None = None,
    skill_id: str | None = None,
    skill_revision_id: str | None = None,
    limit: int,
    cursor: str | None,
) -> AgentPage:
    """`skill_id` and `skill_revision_id` keep the agents with a revision pinning that skill or skill revision."""
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        query = select(AgentRow).where(
            AgentRow.workspace_id == scope.workspace_id,
            label_filter(AgentRow.labels, labels),
            revisions.head_filter(AgentRow, q=q, archived=archived),
        )
        if source is not None:
            query = query.where(AgentRow.source == source)
        # Agent revisions store their pins as `config.skills: [{skill_id, revision_id}]`.
        pin = {name: value for name, value in (("skill_id", skill_id), ("revision_id", skill_revision_id)) if value}
        if pin:
            query = query.where(
                exists().where(
                    AgentRevisionRow.agent_id == AgentRow.id, AgentRevisionRow.config["skills"].contains([pin])
                )
            )
        rows, next_cursor = await cursors.id_page(
            session,
            query,
            AgentRow.id,
            kind="agents",
            owner=cursors.query_owner(scope.workspace_id, labels, q, archived, source, skill_id, skill_revision_id),
            cursor=cursor,
            limit=limit,
        )
        return AgentPage(items=[agent_view(row) for row in rows], next_cursor=next_cursor)


async def list_revisions(
    storage: Storage, actor: Principal, workspace_id: str, agent_id: str, *, limit: int, cursor: str | None
) -> AgentRevisionPage:
    """Newest first."""
    items, next_cursor = await revisions.list_revisions(
        storage, actor, AgentRow, AgentRevisionRow, _revision_view, workspace_id, agent_id, limit=limit, cursor=cursor
    )
    return AgentRevisionPage(items=items, next_cursor=next_cursor)


def _revision_view(head: AgentRow, revision: AgentRevisionRow) -> AgentRevision:
    return AgentRevision.model_validate(revision)


async def set_default(
    storage: Storage,
    actor: Principal,
    workspace_id: str,
    agent_id: str,
    revision_id: str,
    *,
    if_match: str | None,
    registry: Registry,
    plugins: HarnessPluginFactoryCatalog,
) -> Agent:
    """Making a revision the default publishes it again, so its configuration is validated again."""
    async with transaction(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "write")
        head = await revisions.open_head(session, AgentRow, scope.workspace_id, agent_id, if_match)
        revision = await _revision(session, head, revision_id)
        config = AgentConfig.model_validate(revision.config)
        await validate_config(session, actor, scope, head.id, config, registry=registry, plugins=plugins)
        if revisions.set_default(head, revision, actor=actor):
            audit_row(session, actor, head, "revision.set_default", {"revision_id": revision.id})
        await session.flush()
        await session.refresh(head)
        return agent_view(head)


@dataclass(frozen=True, slots=True)
class SelectedRevision:
    agent_id: str
    revision_id: str
    digest: str
    config: AgentConfig


async def select_revision(
    session: AsyncSession, workspace_id: str, agent_id: str, revision_id: str | None
) -> SelectedRevision:
    """The revision a new run executes: the requested one, else the head's default. Archived heads refuse."""
    head = await session.scalar(select(AgentRow).where(AgentRow.workspace_id == workspace_id, AgentRow.id == agent_id))
    if head is None:
        raise not_found(AgentRow.KIND, agent_id)
    if head.archived_at is not None:
        raise disabled(AgentRow.KIND, agent_id)
    selected = revision_id or head.default_revision_id
    revision = (
        await session.scalar(
            select(AgentRevisionRow).where(AgentRevisionRow.agent_id == head.id, AgentRevisionRow.id == selected)
        )
        if selected is not None
        else None
    )
    if revision is None:
        raise not_found(AgentRevisionRow.KIND, selected or agent_id)
    return _selected(revision)


async def load_revision(session: AsyncSession, agent_id: str, revision_id: str) -> SelectedRevision:
    """The revision an accepted run executes: frozen at acceptance, so archiving its agent does not stop it."""
    revision = await session.scalar(
        select(AgentRevisionRow).where(AgentRevisionRow.agent_id == agent_id, AgentRevisionRow.id == revision_id)
    )
    if revision is None:
        raise not_found(AgentRevisionRow.KIND, revision_id)
    return _selected(revision)


def _selected(revision: AgentRevisionRow) -> SelectedRevision:
    return SelectedRevision(
        agent_id=revision.agent_id,
        revision_id=revision.id,
        digest=revision.digest,
        config=AgentConfig.model_validate(revision.config),
    )


async def toolset_catalog(
    storage: Storage, actor: Principal, workspace_id: str, *, registry: Registry
) -> toolsets.ToolsetCatalog:
    """The built-in toolsets; web tools whose operation no registered web provider type serves are unsupported."""
    async with short_session(storage) as session:
        await workspace_scope(session, actor, workspace_id, "read")
    return toolsets.catalog(
        frozenset(operation for definition in registry.web.values() for operation in web_operations(definition))
    )


async def validate_override(
    session: AsyncSession,
    principal: Principal,
    scope: WorkspaceScope,
    revision: SelectedRevision,
    override: AgentOverride,
    *,
    authority: ExecutionAuthority,
    registry: Registry,
    plugins: HarnessPluginFactoryCatalog,
) -> AgentOverride:
    """The override as a run freezes it, once the configuration it produces passes revision validation.

    Its references must be usable by the run's principal under the run's authority; pins the revision already
    holds are accepted as plain runs of it accept them. Paths in errors are relative to the configuration, whose
    field names the override shares.
    """
    try:
        config = apply_override(revision.config, override)
    except ValidationError as error:
        first = error.errors()[0]
        raise invalid(".".join(map(str, first["loc"])) or "overrides", first["type"]) from None
    validated = await validate_config(
        session,
        principal,
        scope,
        revision.agent_id,
        config,
        registry=registry,
        plugins=plugins,
        verb="run",
        authority=authority,
        held=revision.config,
    )
    return freeze_override(override, validated)
