"""Credential-scoped Vercel choices; secret inputs and vendor bodies are never returned."""

import httpx2
from anyio import fail_after
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from a13n_service.infra.db import short_session
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.outbound import open_http
from a13n_service.resources.runtime import Runtime
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import Principal


class VercelOptionsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_key: SecretStr = Field(min_length=1, max_length=4096)
    team_id: str | None = Field(default=None, min_length=1, max_length=128)
    cursor: int | None = Field(default=None, ge=0)


class VercelOption(BaseModel):
    id: str
    name: str


class VercelOptions(BaseModel):
    items: list[VercelOption]
    cursor: int | None = None


class Pagination(BaseModel):
    next: int | None = None


class TeamPage(BaseModel):
    teams: list[VercelOption]
    pagination: Pagination


class Project(VercelOption):
    accountId: str


class ProjectPage(BaseModel):
    projects: list[Project]
    pagination: Pagination


async def choices(runtime: Runtime, actor: Principal, workspace_id: str, body: VercelOptionsRequest) -> VercelOptions:
    async with short_session(runtime.storage) as session:
        await workspace_scope(session, actor, workspace_id, "write")
    params = {"limit": "100"}
    if body.cursor is not None:
        params["until"] = str(body.cursor)
    if body.team_id:
        params["teamId"] = body.team_id
    try:
        with fail_after(runtime.settings.providers.operation_seconds):
            async with open_http(runtime.endpoint_policy, timeout=20, max_bytes=4 * 1024 * 1024) as client:
                response = await client.get(
                    "https://api.vercel.com" + ("/v10/projects" if body.team_id else "/v2/teams"),
                    params=params,
                    headers={"Authorization": "Bearer " + body.api_key.get_secret_value()},
                )
                response.raise_for_status()
                if body.team_id:
                    projects = ProjectPage.model_validate_json(response.content)
                    if any(item.accountId != body.team_id for item in projects.projects):
                        raise ValueError("Project account mismatch")
                    return VercelOptions(
                        items=[VercelOption(id=p.id, name=p.name) for p in projects.projects],
                        cursor=projects.pagination.next,
                    )
                teams = TeamPage.model_validate_json(response.content)
                return VercelOptions(items=teams.teams, cursor=teams.pagination.next)
    except (TimeoutError, httpx2.HTTPError, ValueError):
        raise ServiceError(
            "unavailable", "Vercel choices could not be loaded; check the token and its team/project permissions"
        ) from None
