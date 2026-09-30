"""What a worker attempt needs from its run's frozen mounts: ready instances, then fresh Harness adapters.

`prepare_mounts` runs before the Harness run, in short transactions that each prove the lease. A stopped
instance gets a starting operation, and a pending operation is dispatched once through the fenced lifecycle;
after a failed call the attempt only waits, leaving retries to maintenance's fixed interval. `open_mounts` then
builds adapters that connect to the ready instances and never create, start or replace one.
"""

from collections.abc import AsyncIterator, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass

import anyio
from a13n_harness import EnvironmentMount as HarnessMount
from a13n_harness import RunConfiguration
from a13n_logging import exception_details, get_logger
from sqlalchemy import func, update

from a13n_service.infra.db import lock, now, transaction
from a13n_service.infra.errors import ServiceError, conflict, not_found
from a13n_service.resources.providers.service import resolve_provider
from a13n_service.resources.providers.tables import EnvironmentProviderRow
from a13n_service.runs.attempts import Lease, lock_lease
from a13n_service.runs.environments import external
from a13n_service.runs.environments.adapters import Target, close, construct, provider_identity
from a13n_service.runs.environments.lifecycle import advance, begin
from a13n_service.runs.environments.mounts import PRIMARY, require_usable
from a13n_service.runs.environments.schemas import Handle
from a13n_service.runs.environments.tables import EnvironmentRow
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.schemas import EnvironmentMount
from a13n_service.tenancy.authorize import ExecutionAuthority, Principal, WorkspaceScope

logger = get_logger(__name__)
_POLL_SECONDS = 1


@dataclass(frozen=True, slots=True)
class PreparedMount:
    """One ready mount of the run, detached from the database."""

    name: str
    working_directory: str | None
    target: Target


async def prepare_mounts(
    runtime: Runtime,
    lease: Lease,
    principal: Principal,
    authority: ExecutionAuthority,
    mounts: Sequence[EnvironmentMount],
) -> list[PreparedMount]:
    """Wait, at most `environments.wait_seconds` in all, until every mounted instance is ready.

    Raises `LeaseLost` once the lease is gone, a `conflict`/`forbidden`/`disabled` error when a mount can no
    longer be used (retiring, lost, permanently failed, identity changed, provider disabled), and `unavailable`
    when the wait ran out. Cancelling the calling task stops the wait at once; an interrupted provider call is
    recorded as an unknown outcome that maintenance reconciles.
    """
    deadline = anyio.current_time() + runtime.settings.environments.wait_seconds
    return [await _ready(runtime, lease, principal, authority, mount, deadline) for mount in mounts]


async def _ready(
    runtime: Runtime,
    lease: Lease,
    principal: Principal,
    authority: ExecutionAuthority,
    mount: EnvironmentMount,
    deadline: float,
) -> PreparedMount:
    while True:
        target, dispatch = await _inspect(runtime, lease, principal, authority, mount.environment_id)
        if target is not None:
            return PreparedMount(mount.name, mount.working_directory, target)
        if dispatch:
            await advance(runtime, mount.environment_id, owner=lease.worker_id)
        if anyio.current_time() >= deadline:
            raise ServiceError(
                "unavailable",
                "The environment did not become ready in time",
                {"dependency": "environment", "id": mount.environment_id, "reason": "environment_not_ready"},
            )
        await anyio.sleep(_POLL_SECONDS)


async def _inspect(
    runtime: Runtime, lease: Lease, principal: Principal, authority: ExecutionAuthority, environment_id: str
) -> tuple[Target | None, bool]:
    """The target of a ready instance, or whether this attempt should dispatch the pending operation itself."""
    async with transaction(runtime.storage) as session:
        await lock_lease(session, lease)
        environment = await lock(session, EnvironmentRow, environment_id)
        if environment is None:
            raise not_found("environment", environment_id)
        require_usable(environment, principal.id)
        if environment.template_id is None:
            # An external target is always ready; it is reached with its own endpoint and token.
            environment.last_used_at = await now(session)
            return Target(environment.id, external.account(environment), {}, None), False
        assert environment.provider_id is not None, "a managed instance has a provider"
        scope = WorkspaceScope(environment.organization_id, environment.workspace_id)
        provider = await resolve_provider(
            session, principal, EnvironmentProviderRow, scope, environment.provider_id, authority=authority
        )
        if environment.status == "ready":
            # Refused without recording: the known lifecycle phase stays, and restoring the provider heals it.
            if environment.provider_identity != provider_identity(runtime.registry, provider):
                raise conflict("environment", environment.id, "provider_identity_changed")
            environment.last_used_at = await now(session)
            handle = Handle.model_validate(environment.handle)
            return Target(environment.id, provider, handle.recipe, handle.state), False
        if environment.status == "stopped":
            await begin(session, environment, "starting")
        return None, environment.failure is None and environment.status != "stopping"


@asynccontextmanager
async def open_mounts(
    runtime: Runtime, prepared: Sequence[PreparedMount], *, configuration: RunConfiguration | None = None
) -> AsyncIterator[dict[str, HarnessMount]]:
    """Unentered adapters for `agent.stream(environments=..., default_environment=PRIMARY if present)`.

    The Harness enters and closes the adapters it binds; leaving closes the rest and marks the instances used.
    `workspace` keeps the Harness's `/workspace` route; other mounts appear at `/mnt/{name}`.
    """
    async with AsyncExitStack() as stack:
        stack.push_async_callback(_mark_used, runtime, [item.target.environment_id for item in prepared])
        mounts: dict[str, HarnessMount] = {}
        for item in prepared:
            adapter = await construct(
                runtime, item.target, operation_id=None, allow_create=False, configuration=configuration
            )
            stack.push_async_callback(close, adapter)
            mounts[item.name] = HarnessMount(
                adapter,
                working_directory=item.working_directory,
                mount_path=None if item.name == PRIMARY else f"/mnt/{item.name}",
                provider_root=item.working_directory or adapter.descriptor.working_directory,
            )
        yield mounts


async def _mark_used(runtime: Runtime, environment_ids: list[str]) -> None:
    """Idle time counts from the end of use; best effort, since a stale mark only stops an instance early."""
    if not environment_ids:
        return
    with anyio.CancelScope(shield=True), anyio.move_on_after(_POLL_SECONDS * 5):
        try:
            async with transaction(runtime.storage) as session:
                await session.execute(
                    update(EnvironmentRow)
                    .where(EnvironmentRow.id.in_(sorted(environment_ids)), EnvironmentRow.status == "ready")
                    .values(last_used_at=func.clock_timestamp())
                )
        except Exception as error:
            logger.warning(
                "Marking environments used failed",
                extra={"error_type": type(error).__name__, "exception_details": exception_details(error)},
            )
