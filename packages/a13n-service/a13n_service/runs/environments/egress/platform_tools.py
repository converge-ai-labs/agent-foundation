"""Read existing Session application evidence without opening or acquiring a Session."""

from a13n_service.infra.http import PageLimit
from a13n_service.platform_tools import ToolContext as PlatformContext
from a13n_service.platform_tools import definition
from a13n_service.runs.environments.egress import queries
from a13n_service.runs.environments.egress.queries import SessionCredentialPage
from a13n_service.runs.runtime import Runtime

type ToolContext = PlatformContext[Runtime]


async def list_credential_sessions(
    ctx: ToolContext, *, credential_id: str, limit: PageLimit = 50, cursor: str | None = None
) -> SessionCredentialPage:
    """Inspect credential application. Summary covers all matching bindings, not just this page. Saved does not mean applied."""
    return await queries.credential_sessions(
        ctx.runtime.storage, ctx.actor, ctx.workspace_id, credential_id, limit=limit, cursor=cursor
    )


async def list_environment_credential_sessions(
    ctx: ToolContext, *, environment_id: str, limit: PageLimit = 50, cursor: str | None = None
) -> SessionCredentialPage:
    """Inspect credential versions applied to one Environment's Sessions, preserving private Environment access checks."""
    return await queries.environment_sessions(
        ctx.runtime.storage, ctx.actor, ctx.workspace_id, environment_id, limit=limit, cursor=cursor
    )


TOOLS = (
    definition(list_credential_sessions, group="egress_credentials", effect="read"),
    definition(list_environment_credential_sessions, group="egress_credentials", effect="read"),
)
