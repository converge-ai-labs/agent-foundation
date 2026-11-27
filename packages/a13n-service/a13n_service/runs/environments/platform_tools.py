"""Explicit environment discovery and Run-local connection tools."""

from typing import Annotated

from pydantic import Field

from a13n_service.infra.errors import ServiceError
from a13n_service.platform_tools import ToolContext as PlatformContext
from a13n_service.platform_tools import definition
from a13n_service.runs.environments.connections import Connect, ConnectablePage, Connected, connect, list_connectable
from a13n_service.runs.platform_tokens import RunCredential
from a13n_service.runs.runtime import Runtime
from a13n_service.tenancy.requests import cached_credential

type ToolContext = PlatformContext[Runtime]


async def list_connectable_environments(
    ctx: ToolContext,
    *,
    q: Annotated[str | None, Field(max_length=256)] = None,
    limit: Annotated[int, Field(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Field(max_length=2048)] = None,
) -> ConnectablePage:
    """Find existing environments this execution identity may use. Listing never starts or connects them.

    Operations describe configured support, not verified readiness. Continue with next_cursor.
    """
    return await list_connectable(ctx.runtime, ctx.actor, ctx.workspace_id, q=q, limit=limit, cursor=cursor)


async def connect_environment(ctx: ToolContext, *, body: Connect) -> Connected:
    """Accept a named environment mount for this Run, optionally saving it to its Thread.

    The new alias enters environment context and tool routing only at the next root model request,
    after the entire current tool batch finishes. Do not use it in the same batch, even through
    sequential orchestration. Check the next model turn's current environment context before use.
    Acceptance does not prove remote readiness. persist_to_thread also saves the mount for later Runs;
    it requires the Thread's expected_version from get_thread and never makes the alias available earlier.
    Read accepted mounts through get_run and read_content on content.environment_mounts.
    """
    credential = cached_credential(ctx.request)
    if not isinstance(credential, RunCredential):
        raise ServiceError("forbidden", "Connecting requires a current Run credential")
    return await connect(ctx.runtime, credential.claims, body)


TOOLS = (
    definition(list_connectable_environments, group="environments", effect="read"),
    definition(connect_environment, group="environments", effect="write", version_when="persist_to_thread"),
)
