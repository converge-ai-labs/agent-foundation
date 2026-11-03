"""Explicit execution platform tools, independent of HTTP operation admission."""

from a13n_service.infra import reads
from a13n_service.infra.http import PageLimit
from a13n_service.platform_tools import ToolContext as PlatformContext
from a13n_service.platform_tools import definition
from a13n_service.runs import content_tools as content
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.tool_executions import views

type ToolContext = PlatformContext[Runtime]


async def list_tool_executions(
    ctx: ToolContext,
    *,
    thread_id: str,
    run_id: str | None = None,
    active: bool | None = None,
    limit: PageLimit = 50,
    cursor: str | None = None,
) -> views.ExecutionPage:
    """List durable tool executions in a thread, optionally restricted to one run or active state."""
    return await views.list_executions(
        ctx.runtime, ctx.actor, ctx.workspace_id, thread_id, run_id=run_id, active=active, limit=limit, cursor=cursor
    )


async def get_tool_execution(ctx: ToolContext, *, thread_id: str, execution_id: str) -> views.ExecutionView:
    """Inspect a durable tool execution and its current status; use get_tool_execution_result for retained output."""
    return await views.get_execution(ctx.runtime, ctx.actor, ctx.workspace_id, thread_id, execution_id)


async def get_tool_execution_result(
    ctx: ToolContext,
    *,
    thread_id: str,
    execution_id: str,
    limit: reads.TextLimit = 4000,
) -> content.ContentRead:
    """Read the retained result with a version-bound handle for continuation and literal search."""
    value = await views.result(
        ctx.runtime, ctx.actor, ctx.workspace_id, thread_id, execution_id, offset=0, limit=limit, version=None
    )
    source = content.ExecutionSource(thread_id=thread_id, execution_id=execution_id)
    return content.ContentRead(
        fragment=value,
        continuation=content.encode_reference(ctx, source, value.version, value.next_offset)
        if value.next_offset is not None
        else None,
        reference=content.encode_reference(ctx, source, value.version),
    )


TOOLS = (
    definition(list_tool_executions, group="execution", effect="read"),
    definition(get_tool_execution, group="execution", effect="read"),
    definition(get_tool_execution_result, group="execution", effect="read"),
)
