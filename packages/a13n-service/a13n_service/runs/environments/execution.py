"""Host-owned mount sources: inert registration, first-use lifecycle and fixed-target connectors."""

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal, overload

import anyio
from a13n_harness import EnvironmentMount as HarnessMount
from a13n_harness import RunConfiguration
from a13n_harness.environment._mount_path import (
    mount_path_from_provider_path,
    parse_mount_path,
    provider_path_from_suffix,
)
from a13n_harness.environment.bootstrap import (
    BootstrapCapture,
    EnvironmentBootstrapSnapshot,
    FileBootstrapObservation,
    ProjectGuidanceObservation,
    WorkspaceOutlineObservation,
)
from a13n_harness.environment.commands import BoundProcessHandle
from a13n_harness.environment.computer import ComputerCall
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
from a13n_harness.environment.mounts import EnvironmentPath
from a13n_harness.environment.operations import EnvironmentOperations
from a13n_harness.environment.providers import FileScopeSelection
from a13n_harness.environment.session_recovery import EnvdSessionBorrower
from a13n_harness.environment.virtual_files import VirtualFileOperator, _PreparedFile
from a13n_harness.errors import EnvironmentActivationError
from a13n_harness.providers.environment.remote_envd.http import HTTP_ENVD
from a13n_harness.providers.environment.remote_envd.websocket import WEBSOCKET_ENVD
from a13n_harness.tool_execution import current_execution, execution_cleanup_active
from a13n_logging import exception_details, get_logger
from sqlalchemy import func, select, update

from a13n_service.infra.db import lock, now, transaction
from a13n_service.infra.errors import ServiceError, conflict, not_found
from a13n_service.providers.environment import envd_policy
from a13n_service.resources.environment_instances.tables import EnvironmentBootstrapSnapshotRow
from a13n_service.resources.environment_templates.service import read_template
from a13n_service.resources.providers.service import resolve_provider
from a13n_service.resources.providers.tables import EnvironmentProviderRow
from a13n_service.runs.attempts import AuthorityRevoked, Lease, LeaseLost, lock_lease
from a13n_service.runs.environments import bootstrap_snapshots, external
from a13n_service.runs.environments.adapters import Target, execution_connector, managed_target, provider_identity
from a13n_service.runs.environments.clients.observations import Incarnation
from a13n_service.runs.environments.clients.worker import Target as ClientTarget
from a13n_service.runs.environments.desktop import admission as desktop_admission
from a13n_service.runs.environments.lifecycle import Operation, begin, claim_locked, dispatch, in_use
from a13n_service.runs.environments.mounts import PRIMARY, require_usable
from a13n_service.runs.environments.schemas import Handle
from a13n_service.runs.environments.tables import EnvironmentRow
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.schemas import EnvironmentMount
from a13n_service.runs.skill_cache import Cache
from a13n_service.runs.tool_executions.authority import lock_execution, lock_owner, prove
from a13n_service.tenancy.authorize import ExecutionAuthority, Principal, WorkspaceScope

logger = get_logger(__name__)
_POLL_SECONDS = 1


@dataclass(frozen=True, slots=True)
class PreparedMount:
    """One ready mount of the run, detached from the database."""

    name: str
    working_directory: str | None
    target: Target | ClientTarget
    generation: int


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
    management_state: EnvironmentState | None
    configuration: RunConfiguration | None = None
    activated: bool = False
    skill_cache: Cache | None = None
    session_recovery: EnvdSessionBorrower | None = None
    client: Incarnation | None = None
    defer_bootstrap: bool = True
    bootstrap_snapshot: EnvironmentBootstrapSnapshot | None = None
    captures: dict[tuple[str, ...], BootstrapCapture] = field(default_factory=dict)
    observation: tuple[int, datetime, EnvironmentBootstrapSnapshot] | None = None
    skill_materializer: Callable[..., Awaitable[None]] | None = None

    @property
    def state(self) -> EnvironmentState | None:
        state = self.management_state
        # Outbound mounts use an Envd execution adapter over a different provisioning provider.
        return state if state is not None and state.provider_key == self.provider_key else None

    @property
    def working_directory(self) -> str:
        return (self.mount.working_directory or self.descriptor.working_directory).rstrip("/") or "/"

    def observe_bootstrap(self, key: tuple[str, ...], capture: BootstrapCapture) -> None:
        self.captures[key] = capture

    def defer_skills(self, materializer: Callable[..., Awaitable[None]]) -> None:
        self.skill_materializer = materializer

    async def materialize_skills(self, execution: EnvironmentExecution) -> None:
        if self.skill_materializer is None:
            return
        backend = execution.operations.files
        if backend is None:
            raise EnvironmentError("Skill placement requires files.", code="environment_unsupported")
        root = parse_mount_path("/workspace")
        generation = execution.descriptor.generation

        def select(path: str) -> FileScopeSelection:
            suffix = parse_mount_path(path).suffix_below(root)
            if suffix is None:
                raise EnvironmentError(
                    "Skill placement is outside the primary mount.", code="environment_selection_invalid"
                )
            return FileScopeSelection(
                path,
                EnvironmentPath(
                    mount_id=self.mount.name,
                    execution_id=execution.execution_id,
                    path=provider_path_from_suffix(suffix, self.working_directory),
                ),
                generation,
            )

        @asynccontextmanager
        async def prepare(selection: FileScopeSelection, action: EnvironmentAction) -> AsyncGenerator[_PreparedFile]:
            if (
                action not in self.mount.permission_ceiling.operations
                or action not in execution.descriptor.permissions.operations
            ):
                raise EnvironmentError("Skill placement is not permitted.", code="environment_permission_denied")
            async with self.operation_guard(action):
                yield _PreparedFile(
                    selection.resolved_path,
                    backend,
                    lambda _: None,
                    lambda path: mount_path_from_provider_path("/workspace", path, self.working_directory),
                )

        await self.skill_materializer(files=VirtualFileOperator(select, prepare))

    async def capture_bootstrap(self, execution: EnvironmentExecution, generation: int) -> None:
        if not self.captures:
            return
        observed_at = datetime.now(UTC)
        files: list[FileBootstrapObservation] = []
        guidance = None
        outlines: list[WorkspaceOutlineObservation] = []
        with anyio.move_on_after(2, shield=True):
            for capture in self.captures.values():
                try:
                    observation = await capture(execution)
                except Exception as error:
                    logger.warning(
                        "Environment bootstrap observation failed",
                        extra={"exception_details": exception_details(error)},
                    )
                    continue
                if isinstance(observation, FileBootstrapObservation):
                    files.append(observation)
                elif isinstance(observation, ProjectGuidanceObservation):
                    guidance = observation
                elif isinstance(observation, WorkspaceOutlineObservation):
                    outlines.append(observation)
        if files or guidance is not None or outlines:
            self.observation = (
                generation,
                observed_at,
                EnvironmentBootstrapSnapshot(
                    files=tuple(files), project_guidance=guidance, workspace_outline=tuple(outlines)
                ),
            )

    @property
    def environment_id(self) -> str:
        return self.mount.environment_id

    @asynccontextmanager
    async def operation_guard(self, action: EnvironmentAction) -> AsyncGenerator[None]:
        try:
            if execution_cleanup_active() and action in {
                EnvironmentAction.PROCESS_KILL,
                EnvironmentAction.PROCESS_WAIT,
                EnvironmentAction.PROCESS_RELEASE,
            }:
                record = current_execution()
                assert record is not None
                async with transaction(self.runtime.storage) as session:
                    await lock_execution(session, self.lease, record.execution_id, cleanup=True)
            else:
                await prove(self.runtime, self.lease)
        except (LeaseLost, AuthorityRevoked) as exc:
            record = current_execution()
            if record is not None and not execution_cleanup_active():
                if isinstance(exc, LeaseLost):
                    try:
                        async with transaction(self.runtime.storage) as session:
                            await lock_execution(session, self.lease, record.execution_id, cleanup=True)
                    except LeaseLost:
                        self.runtime.executions.suspend_shell(record.execution_id)
                raise asyncio.CancelledError from exc
            raise
        yield

    @asynccontextmanager
    async def computer_guard(self, call: ComputerCall) -> AsyncGenerator[None]:
        async with desktop_admission.operation(self.runtime, self.lease, self.mount, self.management_state, call):
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
                    if isinstance(prepared.target, ClientTarget):
                        assert self.client is not None
                        connector = prepared.target.connector(
                            self.runtime, self.lease, self.session_recovery, self.client
                        )
                    else:
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
        self.management_state = prepared.target.state
        return _DirectoryConnector(connector, prepared, seconds, self)


@overload
async def prepare(
    runtime: Runtime,
    lease: Lease,
    principal: Principal,
    authority: ExecutionAuthority,
    mount: EnvironmentMount,
    *,
    warmup: Literal[False] = False,
) -> PreparedMount: ...


@overload
async def prepare(
    runtime: Runtime,
    lease: Lease,
    principal: Principal,
    authority: ExecutionAuthority,
    mount: EnvironmentMount,
    *,
    warmup: Literal[True],
) -> PreparedMount | None: ...


async def prepare(
    runtime: Runtime,
    lease: Lease,
    principal: Principal,
    authority: ExecutionAuthority,
    mount: EnvironmentMount,
    *,
    warmup: bool = False,
) -> PreparedMount | None:
    """Create or start an instance and publish its state, without building or opening an execution connector."""
    seconds = runtime.settings.environments.wait_seconds
    logger.info("Environment instance preparation requested", extra={"environment_id": mount.environment_id})
    try:
        try:
            deadline = anyio.current_time() + seconds
            if warmup:
                # A claimed provider call has its own deadline and may publish after the Attempt seals.
                try:
                    prepared = await _ready(runtime, lease, principal, authority, mount, deadline)
                except (LeaseLost, AuthorityRevoked):
                    return None
            else:
                with anyio.fail_after(seconds):
                    prepared = await _ready(runtime, lease, principal, authority, mount, deadline)
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
        prepared, operation = await _inspect(runtime, lease, principal, authority, mount)
        if prepared is not None:
            return prepared
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
    runtime: Runtime, lease: Lease, principal: Principal, authority: ExecutionAuthority, mount: EnvironmentMount
) -> tuple[PreparedMount | None, Operation | None]:
    """Inspect readiness, beginning and claiming new work atomically under the lease and instance locks."""
    async with transaction(runtime.storage) as session:
        _, principal = await lock_owner(session, runtime, lease)
        environment_id = mount.environment_id
        environment = await lock(session, EnvironmentRow, environment_id)
        if environment is None:
            raise not_found("environment", environment_id)
        require_usable(environment, principal.id)

        def selected(target: Target | ClientTarget) -> PreparedMount:
            return PreparedMount(mount.name, mount.working_directory, target, environment.generation)

        if environment.kind == "external":
            assert environment.device_id is not None and mount.working_directory is not None
            return selected(
                ClientTarget(environment.id, environment.device_id, mount.working_directory, mount.name)
            ), None
        if environment.kind == "external_http":
            # An external target is always ready; it is reached with its own endpoint and token.
            environment.last_used_at = await now(session)
            return selected(Target(environment.id, external.account(environment), {}, None)), None
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
            from a13n_service.runs.environments.connection import resolve

            if handle.recipe.get("envd_management") == "managed" and envd_policy.update_required(handle.envd_version):
                record = current_execution()
                held = await session.scalar(
                    select(
                        in_use(
                            environment.id,
                            excluding_run_id=lease.run_id,
                            excluding_execution_id=record.execution_id if record is not None else None,
                        )
                    )
                )
                if not held:
                    await begin(session, environment, "starting")
                    return None, await claim_locked(
                        runtime, session, environment, owner=lease.worker_id, provider=provider
                    )
            plan = resolve(runtime.registry.get("environment", provider.type), handle, mount)
            if plan.mode == "envd_ws":
                from a13n_service.runs.environments import outbound

                binding = outbound.binding_for(runtime.registry, environment, provider, executing=True)
                return selected(
                    ClientTarget(
                        environment.id,
                        binding.device_id,
                        plan.working_directory,
                        mount.name,
                        handle.state,
                    )
                ), None
            return selected(managed_target(runtime.keys, environment, provider, handle)), None
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
    defer_bootstrap: bool = True,
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
            if environment.kind == "external":
                definition = WEBSOCKET_ENVD
                recipe = {"working_directory": mount.working_directory}
                state = EnvironmentState(
                    provider_key=definition.type, state_version="1", state={"device_id": environment.device_id}
                )
            elif environment.kind == "external_http":
                definition = HTTP_ENVD
                recipe = {}
                state = None
            else:
                assert environment.provider_id is not None and environment.template_id is not None
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
                    recipe, state = template.config.recipe | (environment.recipe_overrides or {}), None
                else:
                    handle = Handle.model_validate(environment.handle)
                    recipe, state = handle.recipe, handle.state
            from a13n_harness.providers.environment.execution_mode import ExecutionMode, capabilities, execution_mode

            from a13n_service.runs.environments.schemas import SessionConfiguration

            validated = definition.validate_environment(recipe)
            if (
                environment.kind == "managed"
                and execution_mode(validated) == ExecutionMode.NATIVE
                and mount.session_configuration != SessionConfiguration()
            ):
                raise ServiceError(
                    "conflict",
                    "Session configuration requires an Envd execution transport",
                    {"kind": "environment", "id": environment.id, "reason": "session_configuration_unsupported"},
                )
            transport = capabilities(validated)
            descriptor = definition.describe_environment(validated)
            # Service Local uses the shared Direct Local execution provider key.
            provider_key = "direct_local" if definition.type == "local" else definition.type
            device_id = (
                environment.device_id
                if environment.kind == "external"
                else environment.id
                if transport.host_connection
                else None
            )
            if transport.host_connection:
                provider_key = WEBSOCKET_ENVD.type
                descriptor = WEBSOCKET_ENVD.describe_environment(
                    WEBSOCKET_ENVD.validate_environment(
                        {
                            "working_directory": mount.working_directory or descriptor.working_directory,
                        }
                    )
                )
            source = Source(
                runtime,
                lease,
                principal,
                authority,
                mount,
                descriptor,
                provider_key,
                state,
                configuration,
                client=Incarnation(device_id) if device_id else None,
                skill_cache=skill_cache if mount.name == PRIMARY else None,
                defer_bootstrap=defer_bootstrap,
            )
            if defer_bootstrap:
                row = await session.get(EnvironmentBootstrapSnapshotRow, (environment.id, source.working_directory))
                if row is not None:
                    try:
                        source.bootstrap_snapshot = EnvironmentBootstrapSnapshot.model_validate(row.snapshot)
                    except ValueError:
                        logger.warning(
                            "Invalid environment bootstrap snapshot", extra={"environment_id": environment.id}
                        )
            sources.append(source)
    for source in sources:
        source.session_recovery = await runtime.environment_contexts.get(lease, source.mount)
    try:
        yield {
            source.mount.name: HarnessMount(
                source=source,
                observer=source.client.observe if source.client is not None else None,
                operation_guard=source.operation_guard,
                computer_guard=source.computer_guard,
                permission_ceiling=source.mount.permission_ceiling,
                working_directory=source.mount.working_directory,
                mount_path=None if source.mount.name == PRIMARY else f"/mnt/{source.mount.name}",
                provider_root=source.mount.working_directory or source.descriptor.working_directory,
            )
            for source in sources
        }
    finally:
        await _mark_used(runtime, [source for source in sources if source.activated])


class _DirectoryConnector(EnvironmentConnector):
    """Check the Host's mount selection in the exact execution Harness will own."""

    def __init__(self, connector: EnvironmentConnector, mount: PreparedMount, seconds: float, source: Source) -> None:
        self._connector, self._mount, self._seconds = connector, mount, seconds
        self._source = source

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
                # Control verifies the client's fixed cwd during Session admission.
                # A shell-only client mount need not grant an extra directory listing.
                if self._mount.working_directory is not None and not isinstance(self._mount.target, ClientTarget):
                    await _check_directory(self._mount, execution.operations.files)
            await self._source.materialize_skills(execution)
            self._source.activated = True
            return _MountExecution(execution, self._source, self._mount.generation)
        except BaseException as error:
            if execution is not None:
                with anyio.CancelScope(shield=True):
                    try:
                        if (cache := self._source.skill_cache) is not None and (files := execution.operations.files):
                            await cache.release(
                                files, root=f"{self._source.working_directory.rstrip('/')}/.agents/skill-cache"
                            )
                        await execution.close()
                    except BaseException as cleanup_error:
                        error.add_note(f"Environment execution cleanup also failed: {cleanup_error!r}")
            if isinstance(error, (ServiceError, LeaseLost)):
                raise EnvironmentActivationError(
                    "Host environment activation failed.", code="environment_activation_failed"
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


async def _mark_used(runtime: Runtime, sources: list[Source]) -> None:
    """Record end of use and observations, without holding a session across file I/O."""
    with anyio.move_on_after(_POLL_SECONDS * 5, shield=True):
        for source in sources:
            try:
                async with transaction(runtime.storage) as session:
                    if source.observation is not None:
                        await bootstrap_snapshots.save(
                            session, source.environment_id, source.working_directory, *source.observation
                        )
                    await session.execute(
                        update(EnvironmentRow)
                        .where(EnvironmentRow.id == source.environment_id, EnvironmentRow.status == "ready")
                        .values(last_used_at=func.clock_timestamp())
                    )
            except Exception as error:
                logger.warning(
                    "Recording environment use failed", extra={"exception_details": exception_details(error)}
                )


class _MountExecution(EnvironmentExecution):
    """Capture final observations and release primary-cache pins before closing the file client."""

    def __init__(self, execution: EnvironmentExecution, source: Source, generation: int):
        self._execution, self._source, self._generation = execution, source, generation

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

    async def cleanup_process(self, handle: BoundProcessHandle) -> None:
        await self._execution.cleanup_process(handle)

    async def close(self) -> None:
        try:
            await self._source.capture_bootstrap(self._execution, self._generation)
            if (cache := self._source.skill_cache) is not None and (files := self._execution.operations.files):
                await cache.release(files, root=f"{self._source.working_directory.rstrip('/')}/.agents/skill-cache")
        finally:
            await self._execution.close()
