"""Authorized Kubernetes instance observations without touching its idle clock."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import anyio
from a13n_harness.environment.errors import EnvironmentProviderError
from pydantic import BaseModel

from a13n_service.infra.db import short_session
from a13n_service.infra.errors import ServiceError, conflict
from a13n_service.providers.environment.kubernetes import Diagnostics, KubernetesObserver, LogQuery, Logs
from a13n_service.resources.providers.service import resolve_provider
from a13n_service.resources.providers.tables import EnvironmentProviderRow
from a13n_service.runs.environments.adapters import Target, close, managed_target, open_provider, provider_identity
from a13n_service.runs.environments.schemas import EnvironmentView, Handle
from a13n_service.runs.environments.service import _find
from a13n_service.runs.runtime import Runtime
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import Principal


class InstanceDiagnostics(BaseModel):
    environment: EnvironmentView
    native: Diagnostics


@asynccontextmanager
async def observation(
    runtime: Runtime,
    actor: Principal,
    workspace_id: str,
    environment_id: str,
) -> AsyncIterator[tuple[EnvironmentView, KubernetesObserver, Target]]:
    async with short_session(runtime.storage) as session:
        # Native status and logs can contain guest content, not only resource metadata.
        scope = await workspace_scope(session, actor, workspace_id, "run")
        row = await _find(session, scope, environment_id)
        if row.provider_id is None or not row.provider_identity or row.provider_identity.get("type") != "kubernetes":
            raise conflict("environment", environment_id, "kubernetes_required")
        if row.handle is None:
            raise conflict("environment", environment_id, "environment_state_unavailable")
        account = await resolve_provider(session, actor, EnvironmentProviderRow, scope, row.provider_id)
        if row.provider_identity != provider_identity(runtime.registry, account):
            raise conflict("environment", environment_id, "provider_identity_changed")
        handle = Handle.model_validate(row.handle)
        target = managed_target(runtime.keys, row, account, handle)
        view = EnvironmentView.model_validate(row)
    manager = None
    try:
        with anyio.fail_after(runtime.settings.providers.operation_seconds):
            manager = await open_provider(runtime, target)
            if not isinstance(manager, KubernetesObserver):
                raise conflict("environment", environment_id, "kubernetes_required")
            if target.state is None:
                raise conflict("environment", environment_id, "environment_state_unavailable")
            yield view, manager, target
    except (EnvironmentProviderError, TimeoutError) as error:
        raise ServiceError(
            "unavailable",
            "Kubernetes observation failed",
            {
                "reason": "environment_timeout" if isinstance(error, TimeoutError) else error.code,
            },
        ) from None
    finally:
        if manager is not None:
            await close(manager)


async def inspect(runtime: Runtime, actor: Principal, workspace_id: str, environment_id: str) -> InstanceDiagnostics:
    async with observation(runtime, actor, workspace_id, environment_id) as (view, manager, target):
        assert target.state is not None
        native = await manager.kubernetes_diagnostics(
            target.recipe, environment_id=target.environment_id, state=target.state
        )
        return InstanceDiagnostics(environment=view, native=native)


async def logs(
    runtime: Runtime,
    actor: Principal,
    workspace_id: str,
    environment_id: str,
    query: LogQuery,
) -> Logs:
    async with observation(runtime, actor, workspace_id, environment_id) as (_, manager, target):
        assert target.state is not None
        return await manager.kubernetes_logs(
            target.recipe, environment_id=target.environment_id, state=target.state, query=query
        )
