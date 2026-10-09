"""Findings, managed analyst preparation, and bounded analysis commands."""

from fastapi import APIRouter, Response

from a13n_service.infra.http import IdempotencyKey, IfMatch, PageLimit, tagged
from a13n_service.resources.agents import presets
from a13n_service.resources.agents.schemas import Agent
from a13n_service.runs.findings import analysis, preset, service
from a13n_service.runs.findings.schemas import (
    Analysis,
    AnalysisCreate,
    AnalysisPage,
    Assessment,
    Finding,
    FindingCreate,
    FindingPage,
    FindingUpdate,
    Severity,
)
from a13n_service.runs.requests import CurrentRuntime
from a13n_service.tenancy.requests import Actor, WorkspaceId

router = APIRouter(prefix="/api/v1", tags=["findings"])


@router.post("/finding-agent", response_model=Agent)
async def prepare_finding_agent(
    response: Response, runtime: CurrentRuntime, workspace_id: WorkspaceId, actor: Actor
) -> Agent:
    result = await presets.prepare(
        runtime.storage,
        actor,
        workspace_id,
        kind="finding",
        name=preset.NAME,
        description=preset.DESCRIPTION,
        configuration=preset.configuration,
        preferred=(),
        registry=runtime.registry,
        plugins=runtime.plugins,
    )
    return tagged(response, result)


@router.post("/finding-analyses", response_model=Analysis, status_code=201)
async def start_analysis(
    response: Response,
    runtime: CurrentRuntime,
    workspace_id: WorkspaceId,
    actor: Actor,
    body: AnalysisCreate,
    key: IdempotencyKey,
) -> Analysis:
    result, created = await analysis.start(runtime, actor, workspace_id, body, request_key=key)
    response.status_code = 201 if created else 200
    return result


@router.get("/finding-analyses", response_model=AnalysisPage)
async def list_analyses(
    runtime: CurrentRuntime, workspace_id: WorkspaceId, actor: Actor, limit: PageLimit = 20, cursor: str | None = None
) -> AnalysisPage:
    return await analysis.list_analyses(runtime, actor, workspace_id, limit=limit, cursor=cursor)


@router.post("/findings", response_model=Finding, status_code=201)
async def create_finding(
    response: Response, runtime: CurrentRuntime, workspace_id: WorkspaceId, actor: Actor, body: FindingCreate
) -> Finding:
    return tagged(response, await service.create_finding(runtime.storage, actor, workspace_id, body))


@router.get("/findings", response_model=FindingPage)
async def list_findings(
    runtime: CurrentRuntime,
    workspace_id: WorkspaceId,
    actor: Actor,
    agent_id: str | None = None,
    severity: Severity | None = None,
    assessment: Assessment | None = None,
    closed: bool | None = None,
    limit: PageLimit = 50,
    cursor: str | None = None,
) -> FindingPage:
    return await service.list_findings(
        runtime.storage,
        actor,
        workspace_id,
        agent_id=agent_id,
        severity=severity,
        assessment=assessment,
        closed=closed,
        limit=limit,
        cursor=cursor,
    )


@router.get("/findings/{finding_id}", response_model=Finding)
async def get_finding(
    response: Response, runtime: CurrentRuntime, workspace_id: WorkspaceId, actor: Actor, finding_id: str
) -> Finding:
    return tagged(response, await service.get_finding(runtime.storage, actor, workspace_id, finding_id))


@router.patch("/findings/{finding_id}", response_model=Finding)
async def update_finding(
    response: Response,
    runtime: CurrentRuntime,
    workspace_id: WorkspaceId,
    actor: Actor,
    finding_id: str,
    body: FindingUpdate,
    if_match: IfMatch = None,
) -> Finding:
    return tagged(
        response,
        await service.update_finding(runtime.storage, actor, workspace_id, finding_id, body, if_match=if_match),
    )
