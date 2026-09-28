"""Live environment templates: what a managed sandbox is created from, and the idle policy it follows.

A template names a provider usable in its workspace and a recipe that provider type validates.
Creating one, or changing its provider or recipe, directs that provider's backend, so it needs `write` on the
provider; the idle policy alone needs only to read it. An instance keeps the recipe current at its first
dispatch, which its provider state is bound to; the idle policy is read live. A disabled template refuses new
environments.
"""

from datetime import datetime
from typing import Literal

from pydantic import JsonValue, ValidationError
from sqlalchemy import ColumnElement, Integer, SQLColumnExpression, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra import cursors
from a13n_service.infra.db import Storage, assign, short_session, transaction
from a13n_service.infra.errors import disabled, invalid, not_found
from a13n_service.infra.http import require_match
from a13n_service.infra.ids import new_object_id
from a13n_service.infra.labels import label_filter
from a13n_service.providers.registry import Registry
from a13n_service.resources.environment_templates.schemas import (
    Template,
    TemplateConfig,
    TemplateCreate,
    TemplatePage,
    TemplateUpdate,
)
from a13n_service.resources.environment_templates.tables import EnvironmentTemplateRow
from a13n_service.resources.providers.service import rejection_reason, resolve_provider
from a13n_service.resources.providers.tables import EnvironmentProviderRow
from a13n_service.resources.rows import audit_row, find_row, given, record_update
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import ExecutionAuthority, Principal, Verb, WorkspaceScope, authorize


async def _validated_config(
    session: AsyncSession,
    actor: Principal,
    scope: WorkspaceScope,
    provider_id: str,
    config: TemplateConfig,
    registry: Registry,
    *,
    current: EnvironmentTemplateRow | None,
) -> dict[str, JsonValue]:
    """The config to store: the recipe normalized by the provider type.

    Unless the provider and normalized recipe stay those of the `current` template, the caller must write the
    provider.
    """
    provider = await resolve_provider(session, actor, EnvironmentProviderRow, scope, provider_id, verb="read")
    definition = registry.get("environment", provider.type)
    try:
        recipe = definition.environment_model.model_validate(config.recipe)
    except ValidationError as error:
        raise invalid("config.recipe", f"invalid for {provider.type}: {rejection_reason(error)}") from None
    # An explicit value remains a pin even when it equals today's provider default.
    normalized = config.model_copy(update={"recipe": recipe.model_dump(mode="json", exclude_unset=True)})
    if current is None or (provider_id, normalized.recipe) != (current.provider_id, current.config["recipe"]):
        await resolve_provider(session, actor, EnvironmentProviderRow, scope, provider_id, verb="write")
    # The idle policy is stored in full: maintenance reads it in SQL.
    return normalized.model_dump(mode="json")


async def create_template(
    storage: Storage, actor: Principal, workspace_id: str, body: TemplateCreate, *, registry: Registry
) -> Template:
    async with transaction(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "write")
        config = await _validated_config(session, actor, scope, body.provider_id, body.config, registry, current=None)
        row = EnvironmentTemplateRow(
            id=new_object_id("envtpl"),
            organization_id=scope.organization_id,
            workspace_id=scope.workspace_id,
            name=body.name,
            description=body.description,
            provider_id=body.provider_id,
            config=config,
            enabled=True,
            labels=body.labels,
            created_by_id=actor.id,
            updated_by_id=actor.id,
        )
        session.add(row)
        await session.flush()
        audit_row(session, actor, row, "create")
        return Template.model_validate(row)


async def list_templates(
    storage: Storage, actor: Principal, workspace_id: str, *, labels: list[str], limit: int, cursor: str | None
) -> TemplatePage:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        rows, next_cursor = await cursors.id_page(
            session,
            select(EnvironmentTemplateRow).where(
                EnvironmentTemplateRow.workspace_id == scope.workspace_id,
                label_filter(EnvironmentTemplateRow.labels, labels),
            ),
            EnvironmentTemplateRow.id,
            kind="environment_templates",
            owner=cursors.query_owner(scope.workspace_id, labels),
            cursor=cursor,
            limit=limit,
        )
    return TemplatePage(items=[Template.model_validate(row) for row in rows], next_cursor=next_cursor)


async def get_template(storage: Storage, actor: Principal, workspace_id: str, template_id: str) -> Template:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        return Template.model_validate(
            await find_row(session, actor, EnvironmentTemplateRow, scope, template_id, "read")
        )


async def update_template(
    storage: Storage,
    actor: Principal,
    workspace_id: str,
    template_id: str,
    body: TemplateUpdate,
    *,
    if_match: str | None,
    registry: Registry,
) -> Template:
    async with transaction(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        row = await find_row(session, actor, EnvironmentTemplateRow, scope, template_id, "write", lock=True)
        require_match(if_match, row.id, row.version)
        values = given(body, "provider_id", "name", "labels", "enabled")
        if body.provider_id is not None or body.config is not None:
            provider_id = body.provider_id or row.provider_id
            config = body.config or TemplateConfig.model_validate(row.config)
            values["config"] = await _validated_config(
                session, actor, scope, provider_id, config, registry, current=row
            )
        if "description" in body.model_fields_set:
            values["description"] = body.description
        if record_update(session, actor, row, assign(row, values)):
            await session.flush()
        return Template.model_validate(row)


async def resolve_template(
    session: AsyncSession,
    actor: Principal,
    scope: WorkspaceScope,
    template_id: str,
    *,
    verb: Verb = "run",
    authority: ExecutionAuthority | None = None,
) -> Template:
    """An enabled template new environments may be created from, read in the caller's short session."""
    authorize(actor, scope, verb, authority=authority)
    row = await find_row(session, actor, EnvironmentTemplateRow, scope, template_id, "read")
    if not row.enabled:
        raise disabled(row.KIND, template_id)
    return Template.model_validate(row)


async def read_template(session: AsyncSession, template_id: str) -> Template:
    """The current template of an existing environment, enabled or not; lifecycle maintenance has no actor."""
    row = await session.get(EnvironmentTemplateRow, template_id)
    if row is None:
        raise not_found(EnvironmentTemplateRow.KIND, template_id)
    return Template.model_validate(row)


def idle_past(
    template_id: SQLColumnExpression[str | None],
    since: SQLColumnExpression[datetime],
    policy: Literal["stop_after_seconds", "delete_after_seconds"],
) -> ColumnElement[bool]:
    """Whether `since` lies further back than the template's current idle threshold; false when it is off."""
    seconds = (
        select(EnvironmentTemplateRow.config[policy].astext.cast(Integer))
        .where(EnvironmentTemplateRow.id == template_id)
        .scalar_subquery()
    )
    return since < func.clock_timestamp() - func.make_interval(0, 0, 0, 0, 0, 0, seconds)
