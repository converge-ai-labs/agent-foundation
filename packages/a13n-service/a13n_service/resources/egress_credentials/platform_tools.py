"""Read-only credential inventory; values and mutations stay in trusted client forms."""

from a13n_service.infra.http import PageLimit
from a13n_service.platform_tools import ToolContext as PlatformContext
from a13n_service.platform_tools import definition
from a13n_service.resources.egress_credentials import service
from a13n_service.resources.egress_credentials.schemas import Credential, CredentialPage
from a13n_service.resources.runtime import Runtime

type ToolContext = PlatformContext[Runtime]


async def list_egress_credentials(
    ctx: ToolContext, *, limit: PageLimit = 50, cursor: str | None = None
) -> CredentialPage:
    """List nonsecret credential metadata, allowed hosts and versions. Follow pagination to find a target."""
    return await service.list_credentials(ctx.runtime.storage, ctx.actor, ctx.workspace_id, limit=limit, cursor=cursor)


async def get_egress_credential(ctx: ToolContext, *, credential_id: str) -> Credential:
    """Read metadata and the original version before proposing a private credential action. No token is returned."""
    return await service.get_credential(ctx.runtime.storage, ctx.actor, ctx.workspace_id, credential_id)


TOOLS = (
    definition(list_egress_credentials, group="egress_credentials", effect="read"),
    definition(get_egress_credential, group="egress_credentials", effect="read"),
)
