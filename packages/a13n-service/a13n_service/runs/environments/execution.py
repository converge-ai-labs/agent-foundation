"""Host-owned mount sources: inert registration, first-use lifecycle and fixed-target connectors."""

import asyncio
from collections.abc import AsyncGenerator, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass

import anyio
from a13n_harness import EnvironmentMount as HarnessMount
from a13n_harness import RunConfiguration
from a13n_harness.environment.execution import EnvironmentConnector, EnvironmentExecution
from a13n_harness.environment.files import FileOperator
from a13n_harness.environment.models import (
    EnvironmentAction,
    EnvironmentAvailability,
    EnvironmentDescriptor,
    EnvironmentError,
    EnvironmentOperationFamily,
    EnvironmentState,
)
from a13n_harness.environment.operations import EnvironmentOperations
from a13n_harness.environment.session_recovery import EnvdSessionRecovery
from a13n_harness.errors import EnvironmentActivationError
from a13n_harness.providers.environment.remote_envd.http import HTTP_ENVD
from a13n_harness.tool_execution import current_execution, execution_cleanup_active
from a13n_logging import exception_details, get_logger
from sqlalchemy import func, update

from a13n_service.infra.db import lock, now, transaction
from a13n_service.infra.errors import ServiceError, conflict, not_found
from a13n_service.resources.environment_templates.service import read_template
from a13n_service.resources.providers.service import resolve_provider
from a13n_service.resources.providers.tables import EnvironmentProviderRow
from a13n_service.runs.attempts import AuthorityRevoked, Lease, LeaseLost, lock_lease
from a13n_service.runs.desktop_assistance import admission as desktop_admission
from a13n_service.runs.desktop_assistance.service import verify_continuation
from a13n_service.runs.environments import external
from a13n_service.runs.environments.adapters import Target, execution_connector, provider_identity
from a13n_service.runs.environments.lifecycle import Operation, begin, claim_locked, dispatch
from a13n_service.runs.environments.mounts import PRIMARY, require_usable
from a13n_service.runs.environments.schemas import Handle
from a13n_service.runs.environments.tables import EnvironmentRow
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.schemas import EnvironmentMount
from a13n_service.runs.skill_cache import Cache
from a13n_service.runs.tool_executions.authority import lock_owner
from a13n_service.tenancy.authorize import ExecutionAuthority, Principal, WorkspaceScope

logger = get_logger(__name__)
_POLL_SECONDS = 1


@dataclass(frozen=True, slots=True)
class PreparedMount:
    """One ready mount of the run, detached from the database."""

    name: str
    working_directory: str | None
    target: Target


class EnvironmentUnavailable(Exception):
    """A selected mount can no longer be used by this attempt."""


@dataclass(slots=True)
class Source:
    """Service authority and detached metadata for one frozen mount."""

    runtime: Runtime
    lease: Lease
    principal: Principal
    authority: ExecutionAuthority
    mount: EnvironmentMount
    descriptor: EnvironmentDescriptor
    provider_key: str
    state: EnvironmentState | None
    configuration: RunConfiguration | None = None
    activated: bool = False
    skill_cache: Cache | None = None
    session_recovery: EnvdSessionRecovery | None = None

    @property
    def environment_id(self) -> str:
        return self.mount.environment_id

    @asynccontextmanager
    async def operation_guard(self, action: EnvironmentAction) -> AsyncGenerator[None]:
        async with AsyncExitStack() as stack:
            try:
                await stack.enter_async_context(
                    desktop_admission.operation(self.runtime, self.lease, self.environment_id, self.state, action)
                )
            except (LeaseLost, AuthorityRevoked) as exc:
                if current_execution() is not None and not execution_cleanup_active():
                    # Authority loss cancels owned work; provider observation errors do not.
                    raise asyncio.CancelledError from exc
                raise
            yield

    async def ensure_ready(self) -> EnvironmentConnector:
        seconds = self.runtime.settings.environments.wait_seconds
        try:
            try:
                with anyio.fail_after(seconds):
                    prepared = await prepare(
                        self.runtime,
                        self.lease,
                        self.principal,
                        self.authority,
                        self.mount,
                    )
                    await verify_continuation(self.runtime, self.lease, prepared.target)
                    connector = await execution_connector(
                        self.runtime,
                        prepared.target,
                        configuration=self.configuration,
                        session_recovery=self.session_recovery,
                    )
            except TimeoutError as error:
                raise _not_ready(self.mount.environment_id) from error
        except ServiceError as error:
            if error.code == "unavailable":
                raise
            raise EnvironmentUnavailable(error.message) from error
        self.state = prepared.target.state
        self.activated = True
        if self.mount.working_directory is not None or self.skill_cache is not None:
            return _DirectoryConnector(connector, prepared, seconds, self.skill_cache)
        return connector


async def prepare(
    runtime: Runtime, lease: Lease, principal: Principal, authority: ExecutionAuthority, mount: EnvironmentMount
) -> PreparedMount:
    """Create or start an instance and publish its state, without building or opening an execution connector."""
    seconds = runtime.settings.environments.wait_seconds
    logger.info("Environment instance preparation requested", extra={"environment_id": mount.environment_id})
    try:
        try:
            with anyio.fail_after(seconds):
                prepared = await _ready(runtime, lease, principal, authority, mount, anyio.current_time() + seconds)
        except TimeoutError as error:
            raise _not_ready(mount.environment_id) from error
    except ServiceError as error:
        if error.code == "unavailable":
            raise
        raise EnvironmentUnavailable(error.message) from error
    logger.info("Environment instance ready", extra={"environment_id": mount.environment_id})
    return prepared


def _not_ready(environment_id: str) -> ServiceError:
    return ServiceError(
        "unavailable",
        "The environment did not become ready in time",
        {"dependency": "environment", "id": environment_id, "reason": "environment_not_ready"},
    )


async def _ready(
    runtime: Runtime,
    lease: Lease,
    principal: Principal,
    authority: ExecutionAuthority,
    mount: EnvironmentMount,
    deadline: float,
) -> PreparedMount:
    while True:
        target, operation = await _inspect(runtime, lease, principal, authority, mount.environment_id)
        if target is not None:
            return PreparedMount(mount.name, mount.working_directory, target)
        if operation is not None:
            logger.info(
                "Environment operation claimed",
                extra={
                    "environment_id": mount.environment_id,
                    "operation_id": operation.operation_id,
                    "phase": operation.phase,
                },
            )
            await dispatch(runtime, operation)
            # Publication has completed here; inspect immediately, without a polling delay.
            continue
        remaining = deadline - anyio.current_time()
        if remaining <= 0:
            raise _not_ready(mount.environment_id)
        # Another dispatcher owns the operation, or maintenance owns its retry.
        # Losing a claim is never a successful readiness result.
        await anyio.sleep(min(_POLL_SECONDS, remaining))


async def _inspect(
    runtime: Runtime, lease: Lease, principal: Principal, authority: ExecutionAuthority, environment_id: str
) -> tuple[Target | None, Operation | None]:
    """Inspect readiness, beginning and claiming new work atomically under the lease and instance locks."""
    async with transaction(runtime.storage) as session:
        _, principal = await lock_owner(session, runtime, lease)
        environment = await lock(session, EnvironmentRow, environment_id)
        if environment is None:
            raise not_found("environment", environment_id)
        require_usable(environment, principal.id)
        await desktop_admission.admit_mounts(session, [environment], execution_run_id=lease.run_id)
        if environment.template_id is None:
            # An external target is always ready; it is reached with its own endpoint and token.
            environment.last_used_at = await now(session)
            return Target(environment.id, external.account(environment), {}, None), None
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
            return Target(environment.id, provider, handle.recipe, handle.state), None
        if environment.status == "reserved":
            await begin(session, environment, "creating")
        elif environment.status == "stopped":
            await begin(session, environment, "starting")
        if environment.failure is None and environment.status != "stopping":
            return None, await claim_locked(runtime, session, environment, owner=lease.worker_id, provider=provider)
        return None, None


@asynccontextmanager
async def open_mounts(
    runtime: Runtime,
    lease: Lease,
    principal: Principal,
    authority: ExecutionAuthority,
    mounts: Sequence[EnvironmentMount],
    *,
    configuration: RunConfiguration | None = None,
    skill_cache: Cache | None = None,
    envd_sessions: dict[str, EnvdSessionRecovery] | None = None,
) -> AsyncGenerator[dict[str, HarnessMount]]:
    """Publish inert sources. Only activated instances are marked used on completion."""
    sources: list[Source] = []
    async with transaction(runtime.storage) as session:
        await lock_lease(session, lease)
        for mount in mounts:
            environment = await session.get(EnvironmentRow, mount.environment_id)
            if environment is None:
                raise not_found("environment", mount.environment_id)
            require_usable(environment, principal.id)
            if environment.template_id is None:
                definition = HTTP_ENVD
                recipe = {}
                state = None
            else:
                assert environment.provider_id is not None
                provider = await resolve_provider(
                    session,
                    principal,
                    EnvironmentProviderRow,
                    WorkspaceScope(environment.organization_id, environment.workspace_id),
                    environment.provider_id,
                    authority=authority,
                )
                definition = runtime.registry.get("environment", provider.type)
                if environment.handle is None:
                    template = await read_template(session, environment.template_id)
                    recipe, state = template.config.recipe, None
                else:
                    handle = Handle.model_validate(environment.handle)
                    recipe, state = handle.recipe, handle.state
            descriptor = definition.describe_environment(definition.validate_environment(recipe))
            # Service Local uses the shared Direct Local execution provider key.
            provider_key = "direct_local" if definition.type == "local" else definition.type
            sources.append(
                Source(
                    runtime,
                    lease,
                    principal,
                    authority,
                    mount,
                    descriptor,
                    provider_key,
                    state,
                    configuration,
                    skill_cache=skill_cache if mount.name == PRIMARY else None,
                    session_recovery=envd_sessions.setdefault(mount.name, EnvdSessionRecovery())
                    if envd_sessions is not None
                    else None,
                )
            )
    try:
        yield {
            source.mount.name: HarnessMount(
                source=source,
                operation_guard=source.operation_guard,
                permission_ceiling=source.mount.permission_ceiling,
                working_directory=source.mount.working_directory,
                mount_path=None if source.mount.name == PRIMARY else f"/mnt/{source.mount.name}",
                provider_root=source.mount.working_directory or source.descriptor.working_directory,
            )
            for source in sources
        }
    finally:
        await _mark_used(runtime, [source.mount.environment_id for source in sources if source.activated])


class _DirectoryConnector(EnvironmentConnector):
    """Check the Host's mount selection in the exact execution Harness will own."""

    def __init__(
        self, connector: EnvironmentConnector, mount: PreparedMount, seconds: float, cache: Cache | None = None
    ) -> None:
        self._connector, self._mount, self._seconds = connector, mount, seconds
        self._cache = cache

    @property
    def provider_key(self) -> str:
        return self._connector.provider_key

    @property
    def environment_id(self) -> str:
        return self._connector.environment_id

    @property
    def state(self) -> EnvironmentState | None:
        return self._connector.state

    @property
    def descriptor(self) -> EnvironmentDescriptor:
        return self._connector.descriptor

    async def open(self) -> EnvironmentExecution:
        execution = None
        try:
            with anyio.fail_after(self._seconds):
                execution = await self._connector.open()
                if self._mount.working_directory is not None:
                    await _check_directory(self._mount, execution.operations.files)
            if self._cache is not None:
                root = self._mount.working_directory or self.descriptor.working_directory
                return _CacheExecution(execution, self._cache, f"{root.rstrip('/')}/.agents/skill-cache")
            return execution
        except BaseException as error:
            if execution is not None:
                with anyio.CancelScope(shield=True):
                    try:
                        await execution.close()
                    except BaseException as cleanup_error:
                        error.add_note(f"Environment execution cleanup also failed: {cleanup_error!r}")
            if isinstance(error, ServiceError):
                raise EnvironmentActivationError(
                    "Host environment directory validation failed.", code="environment_activation_failed"
                ) from error
            raise


async def _check_directory(item: PreparedMount, files: FileOperator | None) -> None:
    assert item.working_directory is not None
    if files is None:
        raise conflict(
            "environment",
            item.target.environment_id,
            "working_directory_unsupported",
            mount=item.name,
            working_directory=item.working_directory,
        )
    try:
        await files.list(item.working_directory, max_results=1)
    except EnvironmentError as error:
        if error.code not in {
            "environment_not_found",
            "environment_denied",
            "environment_request_invalid",
        }:
            raise
        raise ServiceError(
            "invalid_argument",
            f"Working directory {item.working_directory!r} for mount {item.name!r} "
            "cannot be opened. Select an existing accessible directory in this environment.",
            {
                "environment_id": item.target.environment_id,
                "mount": item.name,
                "working_directory": item.working_directory,
                "reason": error.code,
            },
        ) from error


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


class _CacheExecution(EnvironmentExecution):
    """Release primary-cache pins while the provider file client is still open."""

    def __init__(self, execution: EnvironmentExecution, cache: Cache, root: str):
        self._execution, self._cache, self._root = execution, cache, root

    @property
    def provider_key(self) -> str:
        return self._execution.provider_key

    @property
    def environment_id(self) -> str:
        return self._execution.environment_id

    @property
    def execution_id(self) -> str:
        return self._execution.execution_id

    @property
    def state(self) -> EnvironmentState | None:
        return self._execution.state

    @property
    def descriptor(self) -> EnvironmentDescriptor:
        return self._execution.descriptor

    @property
    def availability(self) -> EnvironmentAvailability:
        return self._execution.availability

    @property
    def operations(self) -> EnvironmentOperations:
        return self._execution.operations

    async def check_ready(self, operations: frozenset[EnvironmentOperationFamily]) -> None:
        await self._execution.check_ready(operations)

    async def close(self) -> None:
        try:
            if files := self._execution.operations.files:
                await self._cache.release(files, root=self._root)
        finally:
            await self._execution.close()
