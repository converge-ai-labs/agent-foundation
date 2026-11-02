"""Explicit internal business operations; HTTP admission does not expand this surface."""

from typing import Annotated

from pydantic import Field as Query
from pydantic.experimental.missing_sentinel import MISSING

from a13n_service.business_tools import ToolContext as BusinessContext
from a13n_service.business_tools import definition
from a13n_service.infra.http import PageLimit
from a13n_service.resources.environment_templates import service
from a13n_service.resources.environment_templates.schemas import (
    Template,
    TemplateConfig,
    TemplateConfigurationChanges,
    TemplateCreate,
    TemplatePage,
    TemplateUpdate,
)
from a13n_service.resources.environment_templates.tool_inputs import TemplateCreation, TemplateEdit
from a13n_service.resources.runtime import Runtime

type ToolContext = BusinessContext[Runtime]


async def create_environment_template(ctx: ToolContext, *, body: TemplateCreation) -> Template:
    """Create a template using the selected provider's typed recipe and explicit lifecycle policy."""
    config = TemplateConfig(
        recipe=body.recipe.configuration.model_dump(mode="json", exclude_unset=True),
        stop_after_seconds=body.stop_after_seconds,
        delete_after_seconds=body.delete_after_seconds,
    )
    return await service.create_template(
        ctx.runtime.storage,
        ctx.actor,
        ctx.workspace_id,
        TemplateCreate(
            name=body.name,
            provider_id=body.provider_id,
            description=body.description,
            labels=body.labels,
            config=config,
        ),
        registry=ctx.runtime.registry,
        recipe_type=body.recipe.type,
    )


async def list_environment_templates(
    ctx: ToolContext,
    *,
    label: Annotated[list[str] | None, Query()] = None,
    limit: PageLimit = 50,
    cursor: str | None = None,
) -> TemplatePage:
    workspace_id = ctx.workspace_id
    actor = ctx.actor
    runtime = ctx.runtime
    return await service.list_templates(
        runtime.storage, actor, workspace_id, labels=label or [], limit=limit, cursor=cursor
    )


async def get_environment_template(ctx: ToolContext, *, template_id: str) -> Template:
    workspace_id = ctx.workspace_id
    actor = ctx.actor
    runtime = ctx.runtime
    return await service.get_template(runtime.storage, actor, workspace_id, template_id)


async def update_environment_template(
    ctx: ToolContext, *, template_id: str, expected_version: str, body: TemplateEdit
) -> Template:
    """Change only named template fields; preserve every omitted lifecycle value and recipe.

    Recipe replacement is typed and complete. Changing provider requires its new
    recipe; no incompatible old recipe is implicitly carried across providers.
    """
    values = body.model_dump(exclude_unset=True)
    config_values = {key: values.pop(key) for key in ("stop_after_seconds", "delete_after_seconds") if key in values}
    recipe_type = None
    if "recipe" in values:
        values.pop("recipe")
        assert body.recipe is not MISSING
        recipe_type = body.recipe.type
        config_values["recipe"] = body.recipe.configuration.model_dump(mode="json", exclude_unset=True)
    return await service.update_template(
        ctx.runtime.storage,
        ctx.actor,
        ctx.workspace_id,
        template_id,
        TemplateUpdate.model_validate(values),
        if_match=expected_version,
        registry=ctx.runtime.registry,
        config_changes=TemplateConfigurationChanges.model_validate(config_values),
        recipe_type=recipe_type,
    )


TOOLS = (
    definition(create_environment_template, group="environment_templates", effect="write"),
    definition(list_environment_templates, group="environment_templates", effect="read"),
    definition(get_environment_template, group="environment_templates", effect="read"),
    definition(update_environment_template, group="environment_templates", effect="write"),
)
