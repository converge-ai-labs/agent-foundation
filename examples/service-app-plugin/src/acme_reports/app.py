"""Trusted installed routes with host-owned organization admission."""

from a13n_service.app_definitions import AppDefinition
from a13n_service.app_plugins import Frontend
from a13n_service.infra.db import short_session
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.app_plugins import app_router
from a13n_service.tenancy.requests import Actor, CurrentRuntime, WorkspaceId
from pydantic import BaseModel, Field

router = app_router("acme.reports")


class Summary(BaseModel):
    workspace_id: str
    snapshots: int
    sources: int
    completion: int


@router.get("/summary", name="read_acme_report_summary", openapi_extra={"x-a13n-mcp": True})
async def summary(workspace_id: WorkspaceId) -> Summary:
    # Deliberately fixed demo data, not measurements of the user's deployment.
    return Summary(workspace_id=workspace_id, snapshots=12, sources=3, completion=98)


class PreviewRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)


class Preview(BaseModel):
    workspace_id: str
    title: str


@router.post("/preview", name="preview_acme_report")
async def preview(body: PreviewRequest, workspace_id: WorkspaceId, actor: Actor, runtime: CurrentRuntime) -> Preview:
    # App admission already checked read access. This action additionally requires write.
    async with short_session(runtime.storage) as session:
        await workspace_scope(session, actor, workspace_id, "write")
    return Preview(workspace_id=workspace_id, title=body.title)


def definition() -> AppDefinition:
    return AppDefinition(
        app_id="acme.reports",
        name="Acme reports",
        routers=(router,),
        frontend=Frontend("acme_reports", "static", entry="index.js", styles=("index.css",)),
    )
