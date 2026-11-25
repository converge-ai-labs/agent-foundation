"""Managed outbound admission and private bootstrap, separate from client enrollment."""

import asyncio
import hmac
import secrets
from datetime import datetime
from urllib.parse import urlsplit, urlunsplit

from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.crypto import SecretLocation, secret_hash
from a13n_service.infra.db import now
from a13n_service.infra.errors import ServiceError
from a13n_service.providers.environment.websocket import WebSocketBootstrap
from a13n_service.providers.registry import Registry
from a13n_service.resources.providers.service import ResolvedProvider, read_provider, resolve_provider
from a13n_service.resources.providers.tables import EnvironmentProviderRow
from a13n_service.runs.environments.clients.routing import LifecycleCaller
from a13n_service.runs.environments.schemas import Handle, OutboundBinding
from a13n_service.runs.environments.tables import EnvironmentRow
from a13n_service.runs.runtime import Runtime
from a13n_service.tenancy.access import require_active_workspace
from a13n_service.tenancy.authorize import ExecutionAuthority, Principal, WorkspaceScope

PATH = "/api/v1/environments/{environment_id}/connect"


def location(environment: EnvironmentRow) -> SecretLocation:
    return SecretLocation(environment.organization_id, "environments", "outbound", environment.id)


def freeze(runtime: Runtime, environment: EnvironmentRow) -> OutboundBinding:
    origin = urlsplit(runtime.settings.server.public_url)
    loopback = origin.hostname in {"localhost", "127.0.0.1", "::1"}
    if origin.scheme != "https" and not (
        origin.scheme == "http" and loopback and runtime.settings.client_environments.allow_loopback_ws
    ):
        raise ServiceError(
            "invalid_argument",
            "Managed outbound execution requires an HTTPS server.public_url reachable by the sandbox",
        )
    token = secrets.token_urlsafe(32)
    return OutboundBinding(
        device_id=environment.id,
        url=urlunsplit(
            (
                "wss" if origin.scheme == "https" else "ws",
                origin.netloc,
                PATH.format(environment_id=environment.id),
                "",
                "",
            )
        ),
        credential=runtime.keys.protect(token.encode(), location(environment)),
        token_hash=secret_hash(token),
    )


def restore(runtime: Runtime, environment: EnvironmentRow, binding: OutboundBinding) -> WebSocketBootstrap:
    environment_id = environment.id

    async def wait_ready() -> None:
        while True:
            online = await runtime.client_connections.presence(environment_id)
            if (
                online is not None
                and online.descriptor.device_id == binding.device_id
                and online.credential_version == binding.credential_version
            ):
                return
            await asyncio.sleep(0.1)

    return WebSocketBootstrap(
        binding.device_id,
        binding.url,
        SecretStr(runtime.keys.reveal(binding.credential, location(environment)).decode()),
        wait_ready,
    )


async def admitted(
    session: AsyncSession, runtime: Runtime, environment: EnvironmentRow, *, executing: bool = False
) -> OutboundBinding:
    await require_active_workspace(session, environment.workspace_id)
    if environment.provider_id is None:
        raise ServiceError("unauthenticated", "Managed environment has no provider")
    provider = await read_provider(session, EnvironmentProviderRow, environment.provider_id)
    phase = "before_stop" if environment.status == "stopping" else "before_delete"
    awaiting_hook = (
        not executing
        and not environment.operation_hook_blocked
        and (
            environment.operation_hook_active
            or (
                environment.failure is None
                and environment.lease_owner is None
                and environment.next_operation_at is None
            )
        )
        and (
            environment.status == "stopping" or (environment.status == "deleting" and environment.operation_live_delete)
        )
        and any(
            hook.phase == phase and (hook.scope == "organization" or app_id == environment.app_id)
            for app_id, hook in runtime.environment_hooks
        )
    )
    return binding_for(
        runtime.registry, environment, provider, executing=executing, at=await now(session), awaiting_hook=awaiting_hook
    )


def binding_for(
    registry: Registry,
    environment: EnvironmentRow,
    provider: ResolvedProvider,
    *,
    executing: bool = False,
    lifecycle: LifecycleCaller | None = None,
    at: datetime | None = None,
    awaiting_hook: bool = False,
) -> OutboundBinding:
    """Validate already-read instance/provider facts without repeating database queries."""
    allowed = {"ready"} if executing else {"creating", "starting", "ready"}
    if environment.kind != "managed" or environment.status == "deleted" or environment.handle is None:
        raise ServiceError("unauthenticated", "Managed environment connection is no longer authorized")
    handle = Handle.model_validate(environment.handle)
    if handle.recipe.get("execution") != "envd_ws" or handle.outbound is None or environment.provider_id != provider.id:
        raise ServiceError("unauthenticated", "Managed environment has no outbound connection binding")
    from a13n_service.runs.environments.adapters import provider_identity

    if not provider.enabled or environment.provider_identity != provider_identity(registry, provider):
        raise ServiceError("unauthenticated", "Managed environment provider is unavailable or changed")
    live_hook = (
        at is not None
        and environment.operation_hook_active
        and environment.lease_expires_at is not None
        and environment.lease_expires_at > at
        and environment.status in {"creating", "starting", "stopping", "deleting"}
    )
    if lifecycle is not None:
        if (
            not live_hook
            or environment.operation_id != lifecycle.operation_id
            or environment.generation != lifecycle.generation
            or environment.lease_token_hash is None
            or not hmac.compare_digest(environment.lease_token_hash, secret_hash(lifecycle.token))
        ):
            raise ServiceError("unauthenticated", "Managed Environment operation is no longer authorized")
    elif environment.status not in allowed and (executing or not (live_hook or awaiting_hook)):
        raise ServiceError("unavailable", "Managed environment is not ready for an outbound connection")
    return handle.outbound


async def authorize_use(
    session: AsyncSession,
    runtime: Runtime,
    environment: EnvironmentRow,
    principal: Principal,
    authority: ExecutionAuthority | None = None,
) -> OutboundBinding:
    await require_active_workspace(session, environment.workspace_id)
    if environment.provider_id is None:
        raise ServiceError("unauthenticated", "Managed environment has no provider")
    provider = await resolve_provider(
        session,
        principal,
        EnvironmentProviderRow,
        WorkspaceScope(environment.organization_id, environment.workspace_id),
        environment.provider_id,
        verb="run",
        authority=authority,
    )
    return binding_for(runtime.registry, environment, provider, executing=True)
