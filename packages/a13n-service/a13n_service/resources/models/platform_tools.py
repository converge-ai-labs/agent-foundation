"""Explicit internal business operations; HTTP admission does not expand this surface."""

from typing import Annotated, Literal

from a13n_harness.pricing import ModelPricingEntry
from pydantic import Field
from pydantic import Field as Query
from pydantic.experimental.missing_sentinel import MISSING

from a13n_service.infra import reads
from a13n_service.infra.errors import invalid
from a13n_service.infra.http import PageLimit
from a13n_service.infra.ids import ObjectId
from a13n_service.platform_tools import ToolContext as PlatformContext
from a13n_service.platform_tools import definition
from a13n_service.resources.models import media, read_views, service
from a13n_service.resources.models.catalog import ModelsDevCatalog
from a13n_service.resources.models.schemas import (
    CatalogRef,
    MediaDefaults,
    Model,
    ModelCatalog,
    ModelConfig,
    ModelConfigurationChanges,
    ModelCreate,
    ModelPage,
    ModelUpdate,
)
from a13n_service.resources.runtime import Runtime

type ToolContext = PlatformContext[Runtime]


class ModelCreation(ModelConfig):
    provider_id: ObjectId
    key: Annotated[str | None, Field(pattern=r"^[a-z0-9][a-z0-9.-]{0,127}$")] = None
    name: Annotated[str, Field(min_length=1, max_length=128)]
    description: Annotated[str, Field(max_length=2048)] = ""
    pricing: ModelPricingEntry | None = None
    catalog_ref: CatalogRef | None = None
    enabled: bool = True


class ModelEdit(ModelConfigurationChanges):
    name: Annotated[str | MISSING, Field(min_length=1, max_length=128)] = MISSING
    description: Annotated[str | MISSING, Field(max_length=2048)] = MISSING
    pricing: ModelPricingEntry | None = None
    catalog_ref: CatalogRef | None = None
    enabled: bool | MISSING = MISSING


async def create_model(ctx: ToolContext, *, body: ModelCreation) -> Model:
    """Create a model with direct settings and complete typed advanced configuration."""
    metadata = {key: getattr(body, key) for key in ModelCreate.model_fields if key != "config"}
    config = ModelConfig.model_validate(body.model_dump(include=set(ModelConfig.model_fields)))
    return await service.create_model(
        ctx.runtime.storage,
        ctx.actor,
        ctx.workspace_id,
        ModelCreate.model_validate({**metadata, "config": config}),
        registry=ctx.runtime.registry,
    )


async def list_models(ctx: ToolContext, *, limit: PageLimit = 50, cursor: str | None = None) -> ModelPage:
    workspace_id = ctx.workspace_id
    actor = ctx.actor
    runtime = ctx.runtime
    return await service.list_models(runtime.storage, actor, workspace_id, limit=limit, cursor=cursor)


async def get_model(ctx: ToolContext, *, key: str) -> Model:
    workspace_id = ctx.workspace_id
    actor = ctx.actor
    runtime = ctx.runtime
    return await service.get_model(runtime.storage, actor, workspace_id, key)


async def update_model(ctx: ToolContext, *, key: str, expected_version: str, body: ModelEdit) -> Model:
    """Change specified model fields against the version read; preserve all omitted settings.

    Supplied native settings, pricing, characteristics, headers and raw-body sections
    replace those sections whole. Provider/API validation remains authoritative.
    """
    values = body.model_dump(exclude_unset=True)
    config_changes = ModelConfigurationChanges.model_validate(
        {name: value for name, value in values.items() if name in ModelConfig.model_fields}
    )
    metadata = ModelUpdate.model_validate(
        {name: value for name, value in values.items() if name not in ModelConfig.model_fields}
    )
    return await service.update_model(
        ctx.runtime.storage,
        ctx.actor,
        ctx.workspace_id,
        key,
        metadata,
        if_match=expected_version,
        registry=ctx.runtime.registry,
        config_changes=config_changes,
    )


async def get_model_catalog(
    ctx: ToolContext,
    *,
    provider: Annotated[
        str | None,
        Query(pattern="^[a-z0-9][a-z0-9_-]{0,127}$", description="Exact catalog channel ID, such as openrouter."),
    ] = None,
    model: Annotated[
        str | None,
        Query(min_length=1, max_length=256, description="Exact, case-sensitive model ID within the catalog channel."),
    ] = None,
    view: reads.View = "compact",
    query: reads.IndexQuery = None,
    capability: Annotated[str | None, Query(max_length=64)] = None,
    limit: Annotated[int | None, Query(ge=1, le=100)] = None,
    cursor: str | None = None,
    section: Literal["all", "characteristics", "pricing"] | None = None,
    offset: reads.Offset = 0,
    text_limit: reads.TextLimit = 4000,
    version: reads.Version = None,
) -> ModelCatalog | read_views.ModelCatalogPage | reads.TextRead:
    """Read public model metadata and prices. Filter by provider and model to avoid a large full-catalog response.

    Both filters match exactly and combine with AND; omitted filters leave that dimension unrestricted.
    No match returns empty items with the catalog status. Refreshes always fetch the full catalog when due.
    Copy the selected item's characteristics, pricing and ref into model config, pricing and catalog_ref when saving.
    """
    request = ctx.request
    catalog: ModelsDevCatalog = request.app.state.model_catalog
    result = await catalog.read(provider=provider, model=model)
    if section is not None:
        return read_views.detail(result, section, offset=offset, limit=text_limit, version=version, query=query)
    if offset or version:
        raise invalid("section", "Select a section for fragment parameters")
    if view == "detailed" and query is None and (capability is None) and (limit is None) and (cursor is None):
        return result
    return read_views.select(
        result,
        view=view,
        query=query,
        capability=capability,
        limit=limit,
        cursor=cursor,
        provider=provider,
        model=model,
    )


async def get_media_defaults(ctx: ToolContext) -> MediaDefaults:
    workspace_id = ctx.workspace_id
    actor = ctx.actor
    runtime = ctx.runtime
    return await media.get_media_defaults(runtime.storage, actor, workspace_id)


TOOLS = (
    definition(create_model, group="models", effect="write"),
    definition(list_models, group="models", effect="read"),
    definition(get_model, group="models", effect="read"),
    definition(update_model, group="models", effect="write"),
    definition(get_model_catalog, group="models", effect="read"),
    definition(get_media_defaults, group="models", effect="read"),
)
