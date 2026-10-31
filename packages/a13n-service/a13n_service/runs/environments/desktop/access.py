"""Bounded provider access for Computer control; never starts or allocates a guest."""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import anyio
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.errors import conflict
from a13n_service.providers.environment import desktop_access as wire
from a13n_service.providers.environment.management import DesktopSessionProvider
from a13n_service.resources.providers.service import read_provider, resolve_provider
from a13n_service.resources.providers.tables import EnvironmentProviderRow
from a13n_service.runs.environments.adapters import Target, open_provider, provider_identity
from a13n_service.runs.environments.desktop.identity import environment_key
from a13n_service.runs.environments.mounts import require_usable
from a13n_service.runs.environments.schemas import Handle
from a13n_service.runs.environments.tables import EnvironmentRow
from a13n_service.runs.runtime import Runtime
from a13n_service.tenancy.authorize import ExecutionAuthority, Principal, WorkspaceScope

# One complete open/discover/grant-or-revoke exchange, plus bounded client cleanup.
# A crashed claim and a replacement fit within the guest's 60-second input lease.
IO_SECONDS = 15
CLOSE_SECONDS = 3
CLAIM_SECONDS = 20
SCAN_SECONDS = 2
RENEW_SECONDS = 10


async def target(
    session: AsyncSession,
    runtime: Runtime,
    environment: EnvironmentRow,
    *,
    actor: Principal | None = None,
    authority: ExecutionAuthority | None = None,
) -> Target:
    if actor is not None:
        require_usable(environment, actor.id)
    if environment.status != "ready" or environment.provider_id is None or environment.handle is None:
        raise conflict("environment", environment.id, "desktop_not_ready")
    handle = Handle.model_validate(environment.handle)
    if not handle.recipe.get("desktop") or not handle.recipe.get("desktop_assistance"):
        raise conflict("environment", environment.id, "desktop_access_not_enabled")
    if environment_key(environment) is None:
        raise conflict("environment", environment.id, "desktop_identity_unavailable")
    if actor is None:
        account = await read_provider(session, EnvironmentProviderRow, environment.provider_id)
    else:
        account = await resolve_provider(
            session,
            actor,
            EnvironmentProviderRow,
            WorkspaceScope(environment.organization_id, environment.workspace_id),
            environment.provider_id,
            authority=authority,
        )
    if environment.provider_identity != provider_identity(runtime.registry, account):
        raise conflict("environment", environment.id, "provider_identity_changed")
    return Target(environment.id, account, handle.recipe, handle.state)


@asynccontextmanager
async def connection(
    runtime: Runtime, selected: Target, operation_id: str
) -> AsyncIterator[Callable[[wire.Request], Awaitable[wire.Reply]]]:
    provider = None
    try:
        with anyio.fail_after(IO_SECONDS):
            provider = await open_provider(runtime, selected)
            if selected.state is not None and selected.state.provider_key == "modal":
                if not isinstance(provider, DesktopSessionProvider):
                    raise ValueError("Modal desktop access requires its scoped provider")
                async with provider.desktop_session(
                    dict(selected.recipe),
                    environment_id=selected.environment_id,
                    state=selected.state,
                    operation_id=operation_id,
                ) as management:
                    yield management.desktop_access
            else:

                async def send(request: wire.Request) -> wire.Reply:
                    assert provider is not None
                    return await provider.desktop_access(
                        dict(selected.recipe),
                        environment_id=selected.environment_id,
                        state=selected.state,
                        operation_id=operation_id,
                        request=request,
                    )

                yield send
    finally:
        if provider is not None:
            with anyio.CancelScope(shield=True), anyio.move_on_after(CLOSE_SECONDS):
                await provider.close()
