"""Workspace model usage, daily trends and grouped consumption."""

from typing import Annotated

from fastapi import APIRouter, Query

from a13n_service.runs.requests import CurrentRuntime
from a13n_service.tenancy.requests import Actor, WorkspaceId
from a13n_service.usage import service
from a13n_service.usage.schemas import (
    AgentUsagePage,
    BreakdownQuery,
    ModelUsagePage,
    OverviewQuery,
    UsageFilter,
    UsageOverview,
    UsageSummary,
)

router = APIRouter(prefix="/api/v1/usage", tags=["usage"])


@router.get("")
async def summarize_usage(
    runtime: CurrentRuntime, workspace_id: WorkspaceId, actor: Actor, where: Annotated[UsageFilter, Query()]
) -> UsageSummary:
    return await service.summarize(runtime.storage, actor, workspace_id, where)


@router.get("/overview")
async def usage_overview(
    runtime: CurrentRuntime, workspace_id: WorkspaceId, actor: Actor, query: Annotated[OverviewQuery, Query()]
) -> UsageOverview:
    return await service.overview(runtime.storage, actor, workspace_id, query)


@router.get("/agents")
async def usage_agents(
    runtime: CurrentRuntime, workspace_id: WorkspaceId, actor: Actor, query: Annotated[BreakdownQuery, Query()]
) -> AgentUsagePage:
    return await service.agents(runtime.storage, actor, workspace_id, query)


@router.get("/models")
async def usage_models(
    runtime: CurrentRuntime, workspace_id: WorkspaceId, actor: Actor, query: Annotated[BreakdownQuery, Query()]
) -> ModelUsagePage:
    return await service.models(runtime.storage, actor, workspace_id, query)
