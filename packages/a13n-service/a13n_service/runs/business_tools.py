"""Explicit execution business tools, independent of HTTP operation admission."""

from typing import Annotated, Literal, get_args

from a13n_stream_protocol.display import ItemKind, ItemState
from pydantic import BaseModel, Field
from pydantic import Field as Query

from a13n_service.business_tools import ToolContext as BusinessContext
from a13n_service.business_tools import definition
from a13n_service.infra import reads
from a13n_service.infra.http import PageLimit
from a13n_service.resources.agents import read_views as agent_reads
from a13n_service.runs import authors, entries, item_reads, listings, read_views, runs, sessions, threads
from a13n_service.runs import business_content as content
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.schemas import (
    Attempts,
    AttemptView,
    EntryPage,
    EntryStatus,
    MessageAuthorQuery,
    MessageAuthors,
    RunPage,
    RunQuery,
    SessionPage,
    SessionQuery,
    SessionView,
    ThreadNavigationPage,
    ThreadPage,
    ThreadRunPage,
    ThreadView,
    context_response,
)

type ToolContext = BusinessContext[Runtime]


class ItemReadPage(BaseModel):
    page: item_reads.ItemPage
    content: dict[str, dict[str, str]]


async def list_sessions(ctx: ToolContext, *, filters: Annotated[SessionQuery, Query()]) -> SessionPage:
    runtime = ctx.runtime
    workspace_id = ctx.workspace_id
    actor = ctx.actor
    return await sessions.list_sessions(runtime.storage, actor, workspace_id, filters)


async def get_session(ctx: ToolContext, *, session_id: str) -> SessionView:
    runtime = ctx.runtime
    workspace_id = ctx.workspace_id
    actor = ctx.actor
    return await sessions.get_session(runtime.storage, actor, workspace_id, session_id)


async def get_message_authors(
    ctx: ToolContext, *, session_id: str, filters: Annotated[MessageAuthorQuery, Query()]
) -> MessageAuthors:
    """Get message senders' current display names, emails, account kinds, status and avatar URLs.

    Supply the session ID and 1 to 100 entry IDs from the thread inbox. Requires workspace read permission;
    only message entries in that session are returned. This is not an arbitrary user or member-directory lookup.
    Use returned names to identify senders in answers; retain IDs when needed for precise references.
    """
    runtime = ctx.runtime
    workspace_id = ctx.workspace_id
    actor = ctx.actor
    return await authors.message_authors(runtime.storage, actor, workspace_id, session_id, filters)


async def list_session_threads(
    ctx: ToolContext,
    *,
    session_id: str,
    origin: Annotated[list[Literal["new", "fork", "child"]] | None, Query(max_length=3)] = None,
    thread_id: Annotated[list[str] | None, Query(max_length=100)] = None,
    limit: PageLimit = 50,
    cursor: str | None = None,
) -> ThreadNavigationPage:
    """Lightweight Session navigation, oldest first, with stable per-origin positions and parent references."""
    runtime = ctx.runtime
    workspace_id = ctx.workspace_id
    actor = ctx.actor
    return await threads.navigation(
        runtime.storage,
        actor,
        workspace_id,
        session_id,
        origins=origin or [],
        thread_ids=thread_id or [],
        limit=limit,
        cursor=cursor,
    )


async def list_threads(
    ctx: ToolContext,
    *,
    session_id: str | None = None,
    occurrence_id: str | None = None,
    origin: Annotated[list[Literal["new", "fork", "child"]] | None, Query(max_length=3)] = None,
    origin_run_id: str | None = None,
    text_query: Annotated[str | None, Query(max_length=1024)] = None,
    label: Annotated[list[str] | None, Query()] = None,
    limit: PageLimit = 50,
    cursor: str | None = None,
) -> ThreadPage:
    runtime = ctx.runtime
    workspace_id = ctx.workspace_id
    actor = ctx.actor
    return await threads.list_threads(
        runtime.storage,
        actor,
        workspace_id,
        session_id=session_id,
        occurrence_id=occurrence_id,
        origins=origin or [],
        origin_run_id=origin_run_id,
        text_query=text_query,
        label=label or [],
        limit=limit,
        cursor=cursor,
    )


async def get_thread(ctx: ToolContext, *, thread_id: str) -> ThreadView:
    runtime = ctx.runtime
    workspace_id = ctx.workspace_id
    actor = ctx.actor
    return await threads.get(runtime.storage, actor, workspace_id, thread_id)


async def list_thread_runs(
    ctx: ToolContext,
    *,
    thread_id: str,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: str | None = None,
    anchor: Annotated[str | None, Query(max_length=72)] = None,
    sort: Literal["cost", "calls", "duration"] | None = None,
    direction: Literal["asc", "desc"] = "desc",
) -> ThreadRunPage:
    """Page chronological history from an anchor, or rank all Thread runs by a recorded metric."""
    runtime = ctx.runtime
    workspace_id = ctx.workspace_id
    actor = ctx.actor
    return await listings.list_thread_runs(
        runtime.storage,
        actor,
        workspace_id,
        thread_id,
        limit=limit,
        cursor=cursor,
        anchor=anchor,
        sort=sort,
        direction=direction,
    )


async def list_inbox(
    ctx: ToolContext,
    *,
    thread_id: str,
    status: Annotated[list[EntryStatus] | None, Query()] = None,
    kind: Literal["message", "child_result"] | None = None,
    limit: Annotated[int | None, Query(ge=1, le=100)] = None,
    cursor: str | None = None,
) -> EntryPage | read_views.EntrySummaryPage:
    """In inbox order."""
    runtime = ctx.runtime
    workspace_id = ctx.workspace_id
    actor = ctx.actor
    result = await entries.list_entries(
        runtime.storage,
        actor,
        workspace_id,
        thread_id,
        status=status or [],
        limit=reads.page_limit("compact", limit),
        cursor=cursor,
        view="compact",
        kind=kind,
    )
    return context_response(result)


async def get_inbox_entry(
    ctx: ToolContext, *, thread_id: str, entry_id: str
) -> content.ContentView[read_views.EntrySummary]:
    """Read an inbox entry's disposition and references to its payload, options and failure."""
    value = context_response(
        await entries.get_entry(ctx.runtime.storage, ctx.actor, ctx.workspace_id, thread_id, entry_id)
    )
    return content.ContentView(
        summary=read_views.entry_summary(value),
        content={
            section: content.value_reference(
                ctx,
                content.InboxSource(thread_id=thread_id, entry_id=entry_id, section=section),
                getattr(value, section),
            )
            for section in ("payload", "options", "failure")
        },
    )


async def list_runs(ctx: ToolContext, *, filters: Annotated[RunQuery, Query()]) -> RunPage:
    runtime = ctx.runtime
    workspace_id = ctx.workspace_id
    actor = ctx.actor
    return await listings.list_runs(runtime.storage, actor, workspace_id, filters)


async def get_run(ctx: ToolContext, *, run_id: str) -> content.ContentView[read_views.RunSummary]:
    """Read Run status and previews with version-bound references for its complete sections."""
    value = context_response(await runs.get(ctx.runtime.storage, ctx.actor, ctx.workspace_id, run_id))
    return content.ContentView(
        summary=await runs.compact_view(ctx.runtime.storage, value),
        content={
            section: content.value_reference(
                ctx, content.RunSource(run_id=run_id, section=section), getattr(value, section)
            )
            for section in get_args(content.RunSection)
        },
    )


async def list_run_items(
    ctx: ToolContext,
    *,
    run_id: str,
    before: Annotated[int | None, Field(ge=1)] = None,
    after: Annotated[int | None, Field(ge=0)] = None,
    limit: Annotated[int, Field(ge=1, le=500)] = 10,
    kind: ItemKind | None = None,
    state: ItemState | None = None,
    tool_name: Annotated[str | None, Field(min_length=1, max_length=256)] = None,
) -> ItemReadPage:
    """Discover a bounded ordinal window and ready-to-use content handles. Continue next_before/next_after even for empty filtered windows."""
    page = await item_reads.list_items(
        ctx.runtime,
        ctx.actor,
        ctx.workspace_id,
        run_id,
        view="compact",
        before=before,
        after=after,
        limit=limit,
        kind=kind,
        state=state,
        tool_name=tool_name,
    )
    references = {}
    selected = []
    ordered = list(reversed(page.items)) if before is not None else page.items
    for summary in ordered:
        item = await item_reads.get_item(ctx.runtime, ctx.actor, ctx.workspace_id, run_id, summary.id, summary.ordinal)
        refs = {
            "all": content.value_reference(
                ctx,
                content.ItemSource(run_id=run_id, item_id=item.id, ordinal=item.ordinal),
                await content.item_value(ctx, run_id, item),
            )
        }
        projected = item_reads.summary(item)
        proposed = {**references, item.id: refs}
        if (
            selected
            and reads.size({"run": page.run, "items": [*selected, projected], "content": proposed}) > reads.BODY_CHARS
        ):
            boundary = selected[-1].ordinal
            page = page.model_copy(update={"next_before" if before is not None else "next_after": boundary})
            break
        references = proposed
        selected.append(projected)
    compact = page.model_copy(update={"items": list(reversed(selected)) if before is not None else selected})
    return ItemReadPage(page=compact, content=references)


async def get_run_configuration(
    ctx: ToolContext, *, run_id: str, part: agent_reads.ConfigSection = "all"
) -> content.ContentView[agent_reads.RunAgentSummary]:
    """Read the frozen original/effective configuration and override references. These are immutable execution facts, not editable Agent configuration."""
    value = await runs.agent(ctx.runtime.storage, ctx.actor, ctx.workspace_id, run_id)
    run = await runs.get(ctx.runtime.storage, ctx.actor, ctx.workspace_id, run_id)
    sections = {
        "original": content.value_reference(
            ctx,
            content.ConfigurationSource(run_id=run_id, section="original", part=part),
            agent_reads.config_section(value.revision.config, part),
        ),
        "effective": content.value_reference(
            ctx,
            content.ConfigurationSource(run_id=run_id, section="effective", part=part),
            agent_reads.config_section(value.config, part),
        ),
        "overrides": content.value_reference(
            ctx, content.ConfigurationSource(run_id=run_id, section="overrides"), run.options.overrides
        ),
    }
    return content.ContentView(summary=agent_reads.run_agent_summary(value.revision, value.config), content=sections)


async def get_run_lineage(
    ctx: ToolContext, *, run_id: str, cursor: str | None = None, limit: Annotated[int, Query(ge=1, le=50)] = 50
) -> RunPage:
    """The run and its ancestors, nearest first, across fork origins."""
    runtime = ctx.runtime
    workspace_id = ctx.workspace_id
    actor = ctx.actor
    return await listings.lineage(runtime.storage, actor, workspace_id, run_id, cursor=cursor, limit=limit)


async def list_run_attempts(ctx: ToolContext, *, run_id: str) -> Attempts:
    runtime = ctx.runtime
    workspace_id = ctx.workspace_id
    actor = ctx.actor
    return await runs.list_attempts(runtime.storage, actor, workspace_id, run_id)


async def get_run_attempt(ctx: ToolContext, *, run_id: str, attempt_id: str) -> AttemptView:
    runtime = ctx.runtime
    workspace_id = ctx.workspace_id
    actor = ctx.actor
    return await runs.get_attempt(runtime.storage, actor, workspace_id, run_id, attempt_id)


TOOLS = (
    definition(list_sessions, group="execution", effect="read"),
    definition(get_session, group="execution", effect="read"),
    definition(get_message_authors, group="execution", effect="read"),
    definition(list_session_threads, group="execution", effect="read"),
    definition(list_threads, group="execution", effect="read"),
    definition(get_thread, group="execution", effect="read"),
    definition(list_thread_runs, group="execution", effect="read"),
    definition(list_inbox, group="execution", effect="read"),
    definition(get_inbox_entry, group="execution", effect="read"),
    definition(list_runs, group="execution", effect="read"),
    definition(get_run, group="execution", effect="read"),
    definition(list_run_items, group="execution", effect="read"),
    definition(get_run_configuration, group="execution", effect="read"),
    definition(get_run_lineage, group="execution", effect="read"),
    definition(list_run_attempts, group="execution", effect="read"),
    definition(get_run_attempt, group="execution", effect="read"),
)
