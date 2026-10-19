"""Search and bounded discovery of public model metadata."""

from typing import Literal

from pydantic import BaseModel

from a13n_service.infra import reads
from a13n_service.infra.errors import invalid
from a13n_service.resources.models.schemas import CatalogModel, CatalogRef, ModelCatalog


class ModelSummary(BaseModel):
    view: Literal["compact"] = "compact"
    ref: CatalogRef
    name: str
    capabilities: list[str]
    context_window_tokens: int | None
    pricing_available: bool
    omitted_sections: tuple[str, ...] = ("characteristics", "pricing")


class ModelCatalogPage(BaseModel):
    items: list[ModelSummary | CatalogModel]
    status: Literal["ready", "stale", "unavailable"]
    next_cursor: str | None


def select(
    catalog: ModelCatalog,
    *,
    view: reads.ReadView,
    query: str | None,
    capability: str | None,
    limit: int | None,
    cursor: str | None,
    provider: str | None,
    model: str | None,
) -> ModelCatalogPage:
    values = [
        item
        for item in catalog.items
        if (query is None or query.casefold() in (item.name + "\n" + item.ref.model).casefold())
        and (capability is None or capability in item.characteristics.capabilities)
    ]
    items = (
        [
            ModelSummary(
                ref=item.ref,
                name=item.name,
                capabilities=sorted(item.characteristics.capabilities),
                context_window_tokens=item.characteristics.context_window_tokens,
                pricing_available=item.pricing is not None,
            )
            for item in values
        ]
        if view == "compact"
        else values
    )
    selected, following = reads.snapshot_page(
        items,
        key=lambda item: item.ref.provider + "/" + item.ref.model,
        scope="model-catalog",
        filters=(view, query, capability, provider, model),
        cursor=cursor,
        limit=reads.page_limit(view, limit),
        revision=reads.digest(reads.json_text(catalog.items)),
    )
    return ModelCatalogPage(items=selected, status=catalog.status, next_cursor=following)


def detail(
    catalog: ModelCatalog, section: str, *, offset: int, limit: int, version: str | None, query: str | None
) -> reads.TextRead:
    if len(catalog.items) != 1:
        raise invalid("model", "Section reads require provider and model selecting exactly one model")
    item = catalog.items[0]
    return reads.read_value(
        item if section == "all" else getattr(item, section),
        source=f"model-catalog/{item.ref.provider}/{item.ref.model}/{section}",
        offset=offset,
        limit=limit,
        version=version,
        query=query,
    )
