"""Strict ordinal windows for discovery and versioned reads of selected display fields."""

from datetime import datetime
from typing import Literal

from a13n_stream_protocol.display import Item, ItemKind, ItemState
from pydantic import BaseModel

from a13n_service.infra import reads
from a13n_service.infra.errors import invalid, not_found
from a13n_service.runs import read_views, runs
from a13n_service.runs.runtime import Runtime
from a13n_service.tenancy.authorize import Principal


class ItemSummary(BaseModel):
    id: str
    ordinal: int
    kind: ItemKind
    state: ItemState
    started_at: datetime
    ended_at: datetime | None
    tool_call_id: str | None
    tool_name: str | None
    text: reads.Preview
    arguments: reads.Preview
    result: reads.Preview
    source_truncated: bool
    source_truncated_fields: list[str]
    omitted_sections: tuple[str, ...] = ("content", "content_refs")


class ItemPage(BaseModel):
    view: Literal["compact", "detailed"]
    run: read_views.RunSummary
    items: list[ItemSummary | Item]
    complete: bool
    # Continue the same filters and direction; empty filtered windows can still have a continuation.
    next_after: int | None = None
    next_before: int | None = None


def summary(value: Item) -> ItemSummary:
    def short(field: str) -> reads.Preview:
        if field in value.content_refs:
            ref = value.content_refs[field]
            return reads.Preview(text=reads.preview(ref.preview).text, truncated=True)
        return reads.preview(value.content.get(field))

    tool_call_id, tool_name = value.content.get("toolCallId"), value.content.get("toolCallName")
    result_field = next(
        (
            field
            for field in ("result", "value", "result_parts")
            if field in value.content or field in value.content_refs
        ),
        "result",
    )
    return reads.bounded(
        ItemSummary(
            id=value.id,
            ordinal=value.ordinal,
            kind=value.kind,
            state=value.state,
            started_at=value.started_at,
            ended_at=value.ended_at,
            tool_call_id=tool_call_id if isinstance(tool_call_id, str) else None,
            tool_name=tool_name if isinstance(tool_name, str) else None,
            text=short("text"),
            arguments=short("arguments"),
            result=short(result_field),
            source_truncated=value.content.get("truncated") is True,
            source_truncated_fields=[key for key, ref in value.content_refs.items() if ref.truncated],
        )
    )


async def list_items(
    runtime: Runtime,
    actor: Principal,
    workspace_id: str,
    run_id: str,
    *,
    view: reads.ReadView,
    before: int | None,
    after: int | None,
    limit: int,
    kind: ItemKind | None,
    state: ItemState | None,
    tool_name: str | None,
) -> ItemPage:
    """Scan one bounded ordinal window; the continuation advances past examined records, never pending matches."""
    if before is not None and after is not None:
        raise invalid("before", "before and after exclude each other")
    scan_limit = max(limit, 100) if any((kind, state, tool_name)) else limit
    page = await runs.items(
        runtime,
        actor,
        workspace_id,
        run_id,
        before=before,
        after=after if before is not None else after or 0,
        limit=scan_limit,
    )
    matches = [
        item
        for item in page.items
        if (kind is None or item.kind == kind)
        and (state is None or item.state == state)
        and (tool_name is None or item.content.get("toolCallName") == tool_name)
    ]
    # Backward windows must consume from their newest end before advancing next_before.
    ordered = list(reversed(matches)) if before is not None else matches
    candidates = ordered[:limit]
    values: list[ItemSummary | Item] = [summary(item) if view == "compact" else item for item in candidates]
    run = await runs.compact_view(runtime.storage, page.run)
    count = reads.fit_count(values, overhead=reads.size(run) + 500)
    values = values[:count]
    unreturned = len(ordered) > count
    boundary = (
        candidates[count - 1].ordinal
        if unreturned
        else ((page.items[0].ordinal if before is not None else page.items[-1].ordinal) if page.items else None)
    )
    more = unreturned or (
        boundary is not None and (boundary > 1 if before is not None else len(page.items) == scan_limit)
    )
    return ItemPage(
        view=view,
        run=run,
        items=list(reversed(values)) if before is not None else values,
        complete=page.complete,
        next_before=boundary if more and before is not None else None,
        next_after=boundary if more and before is None else None,
    )


async def get_item(
    runtime: Runtime, actor: Principal, workspace_id: str, run_id: str, item_id: str, ordinal: int
) -> Item:
    page = await runs.items(runtime, actor, workspace_id, run_id, after=ordinal - 1, limit=1)
    if not page.items or page.items[0].id != item_id:
        raise not_found("run_item", item_id)
    return page.items[0]


async def content(
    runtime: Runtime,
    actor: Principal,
    workspace_id: str,
    run_id: str,
    item_id: str,
    *,
    ordinal: int,
    field: Literal["all", "text", "arguments", "result", "value", "result_parts", "metadata", "failure"],
    offset: int,
    limit: int,
    version: str | None,
    query: str | None,
) -> reads.TextRead:
    item = await get_item(runtime, actor, workspace_id, run_id, item_id, ordinal)
    truncated = item.content.get("truncated") is True
    if field == "result" and field not in item.content and field not in item.content_refs:
        if "value" in item.content or "value" in item.content_refs:
            field = "value"
        elif "result_parts" in item.content or "result_parts" in item.content_refs:
            field = "result_parts"
    if field == "all":
        value = item
    elif field in item.content_refs:
        saved = await runs.content(runtime, actor, workspace_id, run_id, item.content_refs[field].id)
        value, truncated = saved.value, truncated or saved.truncated
    elif field in item.content:
        value = item.content[field]
    else:
        raise invalid("field", "This item has no selected field; read all for its available content and references")
    return reads.read_value(
        value,
        source=f"runs/{run_id}/items/{item_id}/{field}",
        offset=offset,
        limit=limit,
        version=version,
        query=query,
        source_truncated=truncated,
    )
