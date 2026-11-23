"""Workspace usage tools reuse the HTTP query owners without replaying execution history."""

from a13n_service.platform_tools import ToolContext as PlatformContext
from a13n_service.platform_tools import definition
from a13n_service.runs.runtime import Runtime
from a13n_service.usage import service
from a13n_service.usage.context import read_context_usage
from a13n_service.usage.schemas import (
    AgentUsagePage,
    BreakdownQuery,
    ContextUsage,
    ContextUsageQuery,
    MemberUsagePage,
    ModelRequestMetrics,
    ModelUsagePage,
    OverviewQuery,
    RunUsagePage,
    RunUsageQuery,
    SessionUsagePage,
    SessionUsageQuery,
    UsageFilter,
    UsageOverview,
    UsageSummary,
)

type ToolContext = PlatformContext[Runtime]


async def summarize_usage(ctx: ToolContext, *, filters: UsageFilter) -> UsageSummary:
    """Read current per-model usage for a Run, Thread, Session or ingestion window. Costs are known USD subtotals; check unpriced_requests."""
    return await service.summarize(ctx.runtime.storage, ctx.actor, ctx.workspace_id, filters)


async def list_run_usage(ctx: ToolContext, *, filters: RunUsageQuery) -> RunUsagePage:
    """Page whole Runs and their model usage; totals belong to summarize_usage, not a page sum."""
    return await service.run_usage(ctx.runtime.storage, ctx.actor, ctx.workspace_id, filters)


async def usage_overview(ctx: ToolContext, *, filters: OverviewQuery) -> UsageOverview:
    """Read totals and daily usage in an explicit bounded window and timezone. Consumption follows first ingestion; Run metrics follow start time."""
    return await service.overview(ctx.runtime.storage, ctx.actor, ctx.workspace_id, filters)


async def usage_agents(ctx: ToolContext, *, filters: BreakdownQuery) -> AgentUsagePage:
    """Rank Agents by recorded cost in the selected window."""
    return await service.agents(ctx.runtime.storage, ctx.actor, ctx.workspace_id, filters)


async def usage_models(ctx: ToolContext, *, filters: BreakdownQuery) -> ModelUsagePage:
    """Rank models by recorded cost in the selected window."""
    return await service.models(ctx.runtime.storage, ctx.actor, ctx.workspace_id, filters)


async def list_usage_members(ctx: ToolContext, *, filters: BreakdownQuery) -> MemberUsagePage:
    """Rank execution identities by usage; requires workspace admin. This is historical attribution, not a member directory."""
    return await service.members(ctx.runtime.storage, ctx.actor, ctx.workspace_id, filters)


async def list_session_usage(ctx: ToolContext, *, filters: SessionUsageQuery) -> SessionUsagePage:
    """Rank Sessions for one Agent within the selected window."""
    return await service.sessions(ctx.runtime.storage, ctx.actor, ctx.workspace_id, filters)


async def context_usage(ctx: ToolContext, *, filters: ContextUsageQuery) -> ContextUsage | None:
    """Read the selected main generation and configured context capacity; this is not cumulative usage or a pending prompt estimate."""
    return await read_context_usage(ctx.runtime.storage, ctx.actor, ctx.workspace_id, filters)


async def model_request_metrics(ctx: ToolContext, *, record_id: str) -> ModelRequestMetrics:
    """Read derived latency and throughput for one recorded model contribution."""
    return await service.request_metrics(ctx.runtime.storage, ctx.actor, ctx.workspace_id, record_id)


TOOLS = (
    definition(summarize_usage, group="usage", effect="read"),
    definition(list_run_usage, group="usage", effect="read"),
    definition(usage_overview, group="usage", effect="read"),
    definition(usage_agents, group="usage", effect="read"),
    definition(usage_models, group="usage", effect="read"),
    definition(list_usage_members, group="usage", effect="read"),
    definition(list_session_usage, group="usage", effect="read"),
    definition(context_usage, group="usage", effect="read"),
    definition(model_request_metrics, group="usage", effect="read"),
)
