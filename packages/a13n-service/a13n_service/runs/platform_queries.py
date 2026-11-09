"""Explicit execution platform tools, independent of HTTP operation admission."""

from typing import Annotated, Literal, get_args

from a13n_stream_protocol.display import ItemKind, ItemState
from pydantic import BaseModel, Field
from pydantic import Field as Query

from a13n_service.infra import reads
from a13n_service.infra.errors import invalid
from a13n_service.infra.http import PageLimit
from a13n_service.platform_tools import ToolContext as PlatformContext
from a13n_service.platform_tools import definition
from a13n_service.resources.agents import read_views as agent_reads
from a13n_service.runs import authors, entries, item_reads, listings, read_views, runs, sessions, threads
from a13n_service.runs import content_tools as content
from a13n_service.runs.memories import organization_history
from a13n_service.runs.memories.organization_schemas import OrganizationSourcePage
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

type ToolContext = PlatformContext[Runtime]


class ItemReadPage(BaseModel):
    page: item_reads.ItemPage
    content: dict[str, dict[str, str]]


async def list_sessions(ctx: ToolContext, *, filters: Annotated[SessionQuery, Query()]) -> SessionPage:
    """List accessible sessions with the supplied filters and cursor; use get_session for a selected session."""
    return await sessions.list_sessions(ctx.runtime.storage, ctx.actor, ctx.workspace_id, filters)


async def get_session(ctx: ToolContext, *, session_id: str) -> SessionView:
    """Read one session and its navigation metadata in the current workspace."""
    return await sessions.get_session(ctx.runtime.storage, ctx.actor, ctx.workspace_id, session_id)


async def get_message_authors(
    ctx: ToolContext, *, session_id: str, filters: Annotated[MessageAuthorQuery, Query()]
) -> MessageAuthors:
    """Get message senders' current display names, emails, account kinds, status and avatar URLs.

    Supply the session ID and 1 to 100 entry IDs from the thread inbox. Requires workspace read permission;
    only message entries in that session are returned. This is not an arbitrary user or member-directory lookup.
    Use returned names to identify senders in answers; retain IDs when needed for precise references.
    """
    return await authors.message_authors(ctx.runtime.storage, ctx.actor, ctx.workspace_id, session_id, filters)


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
    return await threads.navigation(
        ctx.runtime.storage,
        ctx.actor,
        ctx.workspace_id,
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
    """Find threads by session, schedule occurrence, origin, text or labels; continue with the returned cursor."""
    return await threads.list_threads(
        ctx.runtime.storage,
        ctx.actor,
        ctx.workspace_id,
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
    """Read one thread and its current execution and lineage metadata."""
    return await threads.get(ctx.runtime.storage, ctx.actor, ctx.workspace_id, thread_id)


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
    return await listings.list_thread_runs(
        ctx.runtime.storage,
        ctx.actor,
        ctx.workspace_id,
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
    """List compact inbox entries in inbox order, filtered by status or kind.

    Use get_inbox_entry for payload references and retain the returned cursor for the next page.
    """
    result = await entries.list_entries(
        ctx.runtime.storage,
        ctx.actor,
        ctx.workspace_id,
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
    """Find runs by agent, thread, status and time filters; continue with the returned cursor."""
    return await listings.list_runs(ctx.runtime.storage, ctx.actor, ctx.workspace_id, filters)


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
    """Discover compact item summaries and lazy content references in a bounded ordinal window.

    Read or search a returned reference to bind its current content version, then use the returned continuation.
    Continue next_before/next_after with the same filters even for empty filtered windows.
    """
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
        refs = {
            "all": content.encode_reference(
                ctx, content.ItemSource(run_id=run_id, item_id=summary.id, ordinal=summary.ordinal), None
            )
        }
        proposed = {**references, summary.id: refs}
        if (
            selected
            and reads.size({"run": page.run, "items": [*selected, summary], "content": proposed}) > reads.BODY_CHARS
        ):
            boundary = selected[-1].ordinal
            page = page.model_copy(update={"next_before" if before is not None else "next_after": boundary})
            break
        references = proposed
        selected.append(summary)
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
            agent_reads.config_section((value.revision.config if value.revision else value.config), part),
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
    return await listings.lineage(ctx.runtime.storage, ctx.actor, ctx.workspace_id, run_id, cursor=cursor, limit=limit)


async def list_run_attempts(ctx: ToolContext, *, run_id: str) -> Attempts:
    """List execution attempts for a run, including their status and worker ownership metadata."""
    return await runs.list_attempts(ctx.runtime.storage, ctx.actor, ctx.workspace_id, run_id)


async def get_run_attempt(ctx: ToolContext, *, run_id: str, attempt_id: str) -> AttemptView:
    """Read one attempt belonging to the selected run; use list_attempt_spans for its observations."""
    return await runs.get_attempt(ctx.runtime.storage, ctx.actor, ctx.workspace_id, run_id, attempt_id)


async def list_organization_sources(
    ctx: ToolContext,
    *,
    offset: Annotated[int, Field(ge=0)] = 0,
    limit: Annotated[int, Field(ge=1, le=100)] = 50,
) -> OrganizationSourcePage:
    """List this organization's frozen source Runs with Session and Thread provenance, oldest seal first.

    Read selected Runs with get_run and list_run_items, then read/search their returned content references.
    An empty page supplies no conversation evidence. Continue with next_offset when present.
    """
    selected = await organization_history.sources(ctx.runtime, ctx.actor, ctx.workspace_id, ctx.origin)
    if selected is None:
        raise invalid("origin", "This tool requires a memory organization Run")
    end = offset + limit
    return OrganizationSourcePage(items=selected[offset:end], next_offset=end if end < len(selected) else None)


TOOLS = (
    definition(list_organization_sources, group="execution", effect="read"),
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
