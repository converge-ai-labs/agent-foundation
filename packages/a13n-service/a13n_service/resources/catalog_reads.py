"""Small discovery indexes, with full-text matching before descriptions are shortened."""

from collections.abc import Sequence

from pydantic import BaseModel

from a13n_service.infra.reads import digest, json_text, preview, snapshot_page
from a13n_service.providers.tools import ToolInfo


class CatalogIndexItem(BaseModel):
    name: str
    description: str
    description_truncated: bool = False
    provider_version: str | None = None
    tool_count: int | None = None
    supported_tool_count: int | None = None


class CatalogIndex(BaseModel):
    items: list[CatalogIndexItem]
    next_cursor: str | None
    omitted_sections: tuple[str, ...] = ("definitions",)


def index_page(
    records: Sequence[CatalogIndexItem],
    *,
    revision: str,
    scope: str,
    query: str | None,
    limit: int,
    cursor: str | None,
) -> CatalogIndex:
    items = []
    for record in records:
        if query and query.casefold() not in (record.name + "\n" + record.description).casefold():
            continue
        short = preview(record.description, 200)
        items.append(record.model_copy(update={"description": short.text, "description_truncated": short.truncated}))
    selected, following = snapshot_page(
        items, key=lambda item: item.name, scope=scope, filters=query, cursor=cursor, limit=limit, revision=revision
    )
    return CatalogIndex(items=selected, next_cursor=following)


def index(
    values: Sequence[ToolInfo],
    *,
    scope: str,
    query: str | None,
    limit: int,
    cursor: str | None,
) -> CatalogIndex:
    records = [
        CatalogIndexItem(name=value.name, description=value.description or "", provider_version=value.provider_version)
        for value in values
    ]
    return index_page(
        records,
        revision=digest(json_text(sorted(values, key=lambda value: value.name))),
        scope=scope,
        query=query,
        limit=limit,
        cursor=cursor,
    )
