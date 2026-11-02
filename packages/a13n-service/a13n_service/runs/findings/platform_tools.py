"""Explicit execution platform tools, independent of HTTP operation admission."""

from a13n_service.infra.http import PageLimit
from a13n_service.platform_tools import ToolContext as PlatformContext
from a13n_service.platform_tools import definition
from a13n_service.runs.findings import analysis, service
from a13n_service.runs.findings.schemas import (
    AnalysisPage,
    Assessment,
    Category,
    Finding,
    FindingCreate,
    FindingPage,
    Severity,
)
from a13n_service.runs.runtime import Runtime

type ToolContext = PlatformContext[Runtime]


async def list_finding_analyses(ctx: ToolContext, *, limit: PageLimit = 20, cursor: str | None = None) -> AnalysisPage:
    """List recent finding-analysis runs and their selected trace scope; continue with the returned cursor."""
    return await analysis.list_analyses(ctx.runtime, ctx.actor, ctx.workspace_id, limit=limit, cursor=cursor)


async def create_finding(ctx: ToolContext, *, body: FindingCreate) -> Finding:
    """Record an unconfirmed finding with evidence and a suggestion; a stable source_key makes a retry safe.
    A Run's own submission names that Run as its source; an analysis cites only selected traces it has read."""
    return await service.create_finding(
        ctx.runtime.storage, ctx.actor, ctx.workspace_id, body, source_run_id=ctx.origin
    )


async def list_findings(
    ctx: ToolContext,
    *,
    agent_id: str | None = None,
    category: Category | None = None,
    severity: Severity | None = None,
    assessment: Assessment | None = None,
    closed: bool | None = None,
    limit: PageLimit = 50,
    cursor: str | None = None,
) -> FindingPage:
    """Find recorded findings by agent, category, severity, assessment or closed state; continue with the returned cursor."""
    return await service.list_findings(
        ctx.runtime.storage,
        ctx.actor,
        ctx.workspace_id,
        agent_id=agent_id,
        category=category,
        severity=severity,
        assessment=assessment,
        closed=closed,
        limit=limit,
        cursor=cursor,
    )


async def get_finding(ctx: ToolContext, *, finding_id: str) -> Finding:
    """Read one recorded finding, including its evidence, suggestion and assessment."""
    return await service.get_finding(ctx.runtime.storage, ctx.actor, ctx.workspace_id, finding_id)


TOOLS = (
    definition(list_finding_analyses, group="execution", effect="read"),
    definition(create_finding, group="execution", effect="write"),
    definition(list_findings, group="execution", effect="read"),
    definition(get_finding, group="execution", effect="read"),
)
