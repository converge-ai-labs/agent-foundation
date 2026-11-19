"""A workspace-scoped App using Service-owned authentication and admission."""

from a13n_service.app_definitions import AppDefinition
{% if cookiecutter.surface != 'backend-only' -%}
from a13n_service.app_plugins import Frontend
{% endif -%}
{% if cookiecutter.surface != 'frontend-only' -%}
from a13n_service.tenancy.app_plugins import app_router
from a13n_service.tenancy.requests import WorkspaceId
from pydantic import BaseModel

router = app_router({{ cookiecutter.app_id | tojson }})


class Summary(BaseModel):
    workspace_id: str
    message: str


@router.get("/summary", name="read_{{ cookiecutter.module_name }}_summary", openapi_extra={"x-a13n-mcp": True})
async def summary(workspace_id: WorkspaceId) -> Summary:
    return Summary(workspace_id=workspace_id, message="Your App backend is connected.")
{% endif %}

def definition() -> AppDefinition:
    return AppDefinition(
        app_id={{ cookiecutter.app_id | tojson }},
        name={{ cookiecutter.project_name | tojson }},
{% if cookiecutter.surface != 'frontend-only' %}        routers=(router,),
{% endif %}{% if cookiecutter.surface != 'backend-only' %}        frontend=Frontend("{{ cookiecutter.module_name }}", "static", entry="index.js", styles=("index.css",)),
{% endif %}    )
