"""Aggregate Environment entry, dynamic routing, leases, and lifecycle ownership."""

from __future__ import annotations

import asyncio
import math
import secrets
import sys
from collections.abc import AsyncGenerator, Coroutine, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, NoReturn, cast

from pydantic import JsonValue

from a13n_harness._json import dump_json_bytes
from a13n_harness.identity import AgentInstanceContext
from a13n_harness.providers.environment.commands import (
    BoundProcessHandle,
    CommandRequest,
    ProcessIdentity,
)
from a13n_harness.providers.environment.files import FileOperator
from a13n_harness.providers.environment.models import (
    DEFAULT_ENVIRONMENT_CLEANUP_TIMEOUT_SECONDS,
    DEFAULT_ENVIRONMENT_OPERATION_TIMEOUT_SECONDS,
    ENVIRONMENT_ACTION_DISPATCH,
    FILE_ACTIONS,
    FILE_READ_ACTIONS,
    EnvironmentAction,
    EnvironmentAvailability,
    EnvironmentChange,
    EnvironmentDescriptor,
    EnvironmentError,
    EnvironmentMountInfo,
    EnvironmentMountObservation,
    EnvironmentOperationFamily,
    EnvironmentPath,
    EnvironmentPermissionSet,
    EnvironmentReadinessRequirement,
    EnvironmentSnapshot,
    EnvironmentState,
)
from a13n_harness.providers.environment.operations import EnvironmentOperations as EnvironmentProviderOperations

from ._mount_path import (
    mount_path_from_provider_path,
    normalize_operation_path,
    parse_mount_path,
    provider_path_from_suffix,
)
from .changes import EnvironmentChangeJournal
from .extensions import EnvironmentRunExtension, EnvironmentRunExtensionContext
from .providers import (
    BoundEnvironment,
    BoundEnvironmentProvider,
    BoundOutputOperations,
    BoundPortOperations,
    BoundProcessOperations,
    BoundShellOperations,
    EnvironmentProviderBinding,
    EnvironmentRuntime,
    EnvironmentRuntimeMount,
    FileScopeSelection,
)
from .sources import EnvironmentEntry, _normalize_runtime_mount
from .virtual_files import VirtualFileOperator, _PreparedFile

if TYPE_CHECKING:
    from a13n_harness.model_context import ModelContextProjection, ModelContextProjectionRequest

from ._facades import _OutputFacade, _PortFacade, _ProcessFacade, _ShellFacade
from ._mount import (
    _EnteredMount,
    _MountKey,
    _MountRequest,
    _OwnedProviderScope,
    _ResolvedPath,
    _validate_provider_artifacts,
)

_MAX_ENVIRONMENT_EXTENSION_ID_LENGTH = 200
_MAX_MODEL_CONTEXT_BINDINGS = 64
_MAX_MODEL_CONTEXT_BYTES = 64 * 1024


def _bounded_snapshot_json(payload: dict[str, JsonValue], max_bytes: int) -> str:
    mounts = cast(list[JsonValue], payload["mounts"])
    while True:
        encoded = dump_json_bytes(payload, sort_keys=True)
        if len(encoded) <= max_bytes:
            return encoded.decode("utf-8")
        if mounts:
            mounts.pop()
            payload["truncated"] = True
            continue
        minimal: dict[str, JsonValue] = {"mounts": [], "truncated": True}
        encoded = dump_json_bytes(minimal, sort_keys=True)
        if len(encoded) > max_bytes:
            raise EnvironmentError(
                "Environment snapshot byte limit cannot encode a minimal value.",
                code="environment_projection_limit_invalid",
            )
        return encoded.decode("utf-8")


class CompositeBoundEnvironment(BoundEnvironment):
    """One stable read-only facade over a mutable run-local mount set."""

    def __init__(
        self,
        *,
        thread_id: str,
        run_id: str,
        instance: AgentInstanceContext,
        host_refs: Mapping[str, str],
        snapshot: EnvironmentSnapshot,
        entered: Mapping[str, _EnteredMount],
        owned_scopes: Mapping[_MountKey, _OwnedProviderScope],
        journal: EnvironmentChangeJournal,
        runtime: ManagedEnvironmentRuntime,
        extensions: tuple[tuple[str, EnvironmentRunExtension], ...],
    ) -> None:
        self._thread_id = thread_id
        self._run_id = run_id
        self._instance = instance
        self._host_refs = dict(host_refs)
        self._snapshot = snapshot
        self._entered = dict(entered)
        self._entered_by_id = {item.mount_id: item for item in entered.values()}
        self._owned_scopes = dict(owned_scopes)
        self._journal = journal
        self._runtime = runtime
        self._extensions = extensions
        self._extension_scopes: list[tuple[str, AbstractAsyncContextManager[None]]] = []
        self._activation_state = "not_started"
        self._readiness_lock = asyncio.Lock()
        self._operation_lock = asyncio.Lock()
        self._mutation_lock = asyncio.Lock()
        self._closed = False
        self._operation_tasks: dict[asyncio.Task[Any], int] = {}
        self._mount_tasks: dict[_MountKey, dict[asyncio.Task[Any], int]] = {}
        self._mount_drained: dict[_MountKey, asyncio.Event] = {}
        self._active_process_handles: dict[_MountKey, set[BoundProcessHandle]] = {}
        self._readiness_tasks: dict[tuple[str, str, str], asyncio.Task[None]] = {}
        self._readiness_waiters: dict[asyncio.Task[None], int] = {}
        self._retirement_tasks: set[asyncio.Task[None]] = set()
        self._retirement_failures: list[BaseException] = []
        self._files = VirtualFileOperator(
            self.select_files,
            self._prepare_file,
        )
        self._shell = _ShellFacade(self)
        self._processes = _ProcessFacade(self)
        self._ports = _PortFacade(self)
        self._outputs = _OutputFacade(self)

    @property
    def snapshot(self) -> EnvironmentSnapshot:
        return self._snapshot

    @property
    def files(self) -> FileOperator:
        return self._files

    async def project_model_context(
        self,
        request: ModelContextProjectionRequest,
    ) -> ModelContextProjection:
        from a13n_harness.model_context import (
            ModelContextBlock,
            ModelContextPlacement,
            ModelContextProjection,
            ModelContextRequestKind,
        )

        if request.kind is not ModelContextRequestKind.INPUT:
            return ModelContextProjection()
        snapshot = self.snapshot
        selected = snapshot.mounts[:_MAX_MODEL_CONTEXT_BINDINGS]
        mounts: list[dict[str, JsonValue]] = []
        for mount in selected:
            availability = "unavailable"
            ready: list[str] = []
            reason: str | None = None
            try:
                observation = await self.describe(mount.name)
                availability = observation.availability.status
                ready = sorted(observation.availability.ready_families)
                reason = observation.availability.reason_code
            except EnvironmentError:
                pass
            operations = sorted(
                {
                    ENVIRONMENT_ACTION_DISPATCH[action].family
                    for action in mount.permission_ceiling.operations
                    if ENVIRONMENT_ACTION_DISPATCH[action].family != "state"
                }
            )
            projected: dict[str, JsonValue] = {
                "name": mount.name,
                "root": _preferred_mount_path(mount, snapshot.default_mount),
                "operations": cast(JsonValue, operations),
                "availability": availability,
                "ready": cast(JsonValue, ready),
                "read_only": not mount.permission_ceiling.operations & (FILE_ACTIONS - FILE_READ_ACTIONS),
            }
            if reason is not None:
                projected["reason"] = reason
            mounts.append(projected)
        payload: dict[str, JsonValue] = {
            "default_mount": snapshot.default_mount,
            "mounts": cast(JsonValue, mounts),
            "truncated": len(selected) < len(snapshot.mounts),
        }
        prefix = "Current Environment mounts (trusted dynamic context):\n"
        content = prefix + _bounded_snapshot_json(
            payload,
            _MAX_MODEL_CONTEXT_BYTES - len(prefix.encode("utf-8")),
        )
        return ModelContextProjection(
            blocks=(
                ModelContextBlock(
                    source_id="a13n.environment-mounts",
                    placement=ModelContextPlacement.INPUT_PREAMBLE,
                    content=content,
                ),
            )
        )

    def select_files(self, path: str, *, alias: str | None = None) -> FileScopeSelection:
        route = self._resolve_path(path, alias=alias)
        return FileScopeSelection(
            logical_path=path,
            resolved_path=EnvironmentPath(mount_id=route.entered.mount_id, path=route.provider_path),
            observed_generation=route.entered.public.descriptor.generation,
            mount_path=route.mount_path,
        )

    @asynccontextmanager
    async def open_files(self, selection: FileScopeSelection) -> AsyncGenerator[FileOperator]:
        if not isinstance(selection, FileScopeSelection) or not isinstance(selection.mount_path, str):
            raise EnvironmentError("File scope selection is invalid.", code="environment_request_invalid")
        selected = selection.resolved_path
        mount_path = selection.mount_path
        entered = self._entered_by_id.get(selected.mount_id)
        if (
            entered is None
            or self._entered.get(entered.public.name) is not entered
            or entered.public.descriptor.generation != selection.observed_generation
        ):
            raise EnvironmentError("File scope selection is stale.", code="environment_stale_mount")
        async with self._mount_slot(entered):
            await self._ensure_provider_family(entered, "files")
            entered = self._current_publication(entered)
            if selection.observed_generation not in {"unprepared", entered.public.descriptor.generation}:
                raise EnvironmentError("File scope selection is stale.", code="environment_stale_mount")
            self._validate_live_observation(entered, entered.provider.availability, frozenset({"files"}))
            if entered.operations.files is None:
                raise EnvironmentError("File operation facet is unavailable.", code="environment_unsupported")
            scoped = VirtualFileOperator(
                lambda path: FileScopeSelection(
                    logical_path=path,
                    resolved_path=self._resolve_scoped_file_path(path, entered, mount_path),
                    observed_generation=entered.public.descriptor.generation,
                    mount_path=mount_path,
                ),
                lambda selection, action: self._prepare_scoped_file(
                    entered, mount_path, selection.resolved_path, action
                ),
            )
            yield scoped

    @property
    def shell(self) -> BoundShellOperations:
        return self._shell

    @property
    def processes(self) -> BoundProcessOperations:
        return self._processes

    @property
    def ports(self) -> BoundPortOperations:
        return self._ports

    @property
    def outputs(self) -> BoundOutputOperations:
        return self._outputs

    async def _activate(self) -> None:
        self._assert_open()
        if self._activation_state == "active":
            return
        if self._activation_state == "failed":
            raise EnvironmentError(
                "Environment run extension activation previously failed.",
                code="environment_extension_bind_failed",
            )
        if self._activation_state != "not_started":
            raise EnvironmentError("The Environment is closing.", code="environment_closed")
        self._activation_state = "activating"
        context = EnvironmentRunExtensionContext(
            run_id=self._run_id,
            instance=self._instance,
            environment=self,
        )
        try:
            for extension_id, extension in self._extensions:
                try:
                    scope = extension.bind(context=context)
                    if not isinstance(scope, AbstractAsyncContextManager):
                        raise TypeError("extension bind did not return an async context manager")
                    await scope.__aenter__()
                except Exception:
                    raise EnvironmentError(
                        "Environment run extension setup failed.",
                        code="environment_extension_bind_failed",
                        details={"extension_id": extension_id},
                    ) from None
                self._extension_scopes.append((extension_id, scope))
        except BaseException as primary:
            self._activation_state = "failed"
            extension_scopes = tuple(self._extension_scopes)
            self._extension_scopes.clear()
            try:
                await _await_cleanup_shielded(_close_extension_scopes(extension_scopes))
            except BaseException as cleanup_error:
                if cleanup_error is not primary:
                    raise BaseExceptionGroup(
                        "Environment run extension activation and cleanup failed",
                        [primary, cleanup_error],
                    ) from None
            finally:
                self._runtime._activation_failed()
            raise
        self._activation_state = "active"
        self._runtime._activated(self)

    async def _read_changes(
        self,
        *,
        after_sequence: int,
        wait: bool = False,
    ) -> tuple[EnvironmentChange, ...]:
        return await self._journal.read(after_sequence=after_sequence, wait=wait)

    @property
    def _change_sequence(self) -> int:
        return self._journal.current_sequence

    @staticmethod
    def _mount_key(entered: _EnteredMount) -> _MountKey:
        return entered.mount_id

    def _track_process_handle(self, handle: BoundProcessHandle, *, added: bool) -> None:
        key = handle.mount_id
        if added:
            self._active_process_handles.setdefault(key, set()).add(handle)
        else:
            handles = self._active_process_handles.get(key)
            if handles is not None:
                handles.discard(handle)
                if not handles:
                    self._active_process_handles.pop(key, None)
        self._refresh_mount_drained(key)

    async def _mount(
        self,
        name: str,
        mount: EnvironmentRuntimeMount,
        *,
        make_default: bool,
    ) -> EnvironmentChange:
        _validate_mount_name(name)
        if not isinstance(mount, EnvironmentRuntimeMount):
            raise EnvironmentError("Environment mount input is invalid.", code="environment_request_invalid")
        async with self._mutation_lock:
            self._assert_mutable()
            if name in self._entered:
                raise EnvironmentError("Environment mount already exists.", code="environment_conflict")
            previous_default = self._snapshot.default_mount
            current_default = name if make_default else previous_default
            _validate_route_paths(
                (
                    *((item.public.name, item.public.mount_path) for item in self._entered.values()),
                    (name, mount.mount_path),
                ),
                current_default,
            )
            owned = await self._prepare_runtime_mount(name, mount)
            try:
                async with self._operation_lock:
                    self._assert_mutable()
                    self._owned_scopes[self._mount_key(owned.entered)] = owned
                    self._entered[name] = owned.entered
                    self._entered_by_id[owned.entered.mount_id] = owned.entered
                    self._snapshot = EnvironmentSnapshot(
                        mounts=tuple(item.public for item in self._entered.values()),
                        default_mount=current_default,
                    )
                    change = EnvironmentChange(
                        sequence=self._journal.current_sequence + 1,
                        kind="mounted",
                        name=name,
                        previous_default=previous_default,
                        current_default=current_default,
                    )
                    self._journal.publish(change)
            except BaseException as primary:
                await _raise_with_provider_cleanup(primary, owned.scope)
            return change

    async def _replace(self, name: str, mount: EnvironmentRuntimeMount) -> EnvironmentChange:
        _validate_mount_name(name)
        if not isinstance(mount, EnvironmentRuntimeMount):
            raise EnvironmentError("Environment mount input is invalid.", code="environment_request_invalid")
        async with self._mutation_lock:
            self._assert_mutable()
            current = self._entered.get(name)
            if current is None:
                raise EnvironmentError("Environment mount does not exist.", code="environment_not_found")
            _validate_route_paths(
                (
                    *(
                        (item.public.name, item.public.mount_path)
                        for item in self._entered.values()
                        if item is not current
                    ),
                    (name, mount.mount_path),
                ),
                self._snapshot.default_mount,
            )
            owned = await self._prepare_runtime_mount(name, mount)
            old_key = self._mount_key(current)
            try:
                async with self._operation_lock:
                    self._assert_mutable()
                    old_owned = self._owned_scopes.pop(old_key)
                    self._owned_scopes[self._mount_key(owned.entered)] = owned
                    self._entered[name] = owned.entered
                    self._entered_by_id[owned.entered.mount_id] = owned.entered
                    self._snapshot = EnvironmentSnapshot(
                        mounts=tuple(item.public for item in self._entered.values()),
                        default_mount=self._snapshot.default_mount,
                    )
                    change = EnvironmentChange(
                        sequence=self._journal.current_sequence + 1,
                        kind="replaced",
                        name=name,
                        previous_default=self._snapshot.default_mount,
                        current_default=self._snapshot.default_mount,
                    )
                    self._journal.publish(change)
            except BaseException as primary:
                await _raise_with_provider_cleanup(primary, owned.scope)
            self._schedule_retirement(old_key, old_owned)
            return change

    async def _unmount(self, name: str) -> EnvironmentChange:
        _validate_mount_name(name)
        async with self._mutation_lock:
            self._assert_mutable()
            current = self._entered.get(name)
            if current is None:
                raise EnvironmentError("Environment mount does not exist.", code="environment_not_found")
            key = self._mount_key(current)
            previous_default = self._snapshot.default_mount
            current_default = None if previous_default == name else previous_default
            async with self._operation_lock:
                self._assert_mutable()
                old_owned = self._owned_scopes.pop(key)
                self._entered.pop(name)
                self._snapshot = EnvironmentSnapshot(
                    mounts=tuple(item.public for item in self._entered.values()),
                    default_mount=current_default,
                )
                change = EnvironmentChange(
                    sequence=self._journal.current_sequence + 1,
                    kind="unmounted",
                    name=name,
                    previous_default=previous_default,
                    current_default=current_default,
                )
                self._journal.publish(change)
            self._schedule_retirement(key, old_owned)
            return change

    async def _set_default(self, name: str | None) -> EnvironmentChange:
        if name is not None:
            _validate_mount_name(name)
        async with self._mutation_lock:
            self._assert_mutable()
            if name is not None and name not in self._entered:
                raise EnvironmentError("Environment mount does not exist.", code="environment_not_found")
            _validate_route_paths(
                tuple((item.public.name, item.public.mount_path) for item in self._entered.values()),
                name,
            )
            previous_default = self._snapshot.default_mount
            async with self._operation_lock:
                self._assert_mutable()
                self._snapshot = EnvironmentSnapshot(mounts=self._snapshot.mounts, default_mount=name)
                change = EnvironmentChange(
                    sequence=self._journal.current_sequence + 1,
                    kind="default_changed",
                    previous_default=previous_default,
                    current_default=name,
                )
                self._journal.publish(change)
            return change

    async def _prepare_runtime_mount(
        self,
        name: str,
        mount: EnvironmentRuntimeMount,
    ) -> _OwnedProviderScope:
        if not isinstance(mount, EnvironmentRuntimeMount):
            raise EnvironmentError("Environment mount input is invalid.", code="environment_request_invalid")
        candidate = mount.binding
        if not candidate._claim_transfer():
            raise EnvironmentError(
                "Environment provider binding was already transferred.",
                code="environment_provider_binding_reused",
            )
        request = _MountRequest(
            name=name,
            permission_ceiling=mount.permission_ceiling.model_copy(deep=True),
            default_working_directory=mount.working_directory,
            mount_path=mount.mount_path,
            provider_root=mount.provider_root,
            candidate=candidate,
        )
        mount_id = _new_mount_id()
        scope = candidate.bind(
            thread_id=self._thread_id,
            run_id=self._run_id,
            instance=self._instance,
            mount_id=mount_id,
            host_refs=self._host_refs,
        )
        try:
            async with asyncio.timeout(DEFAULT_ENVIRONMENT_OPERATION_TIMEOUT_SECONDS):
                provider = await scope.__aenter__()
        except BaseException as primary:
            try:
                await _await_cleanup_shielded(_discard_candidate_instances((candidate,)))
            except BaseException as cleanup:
                raise BaseExceptionGroup(
                    "Environment mount entry and candidate cleanup failed",
                    [primary, cleanup],
                ) from None
            raise
        try:
            entered = _validate_entered(request, mount_id, candidate, provider)
        except BaseException as primary:
            try:
                await _await_cleanup_shielded(_close_provider_scopes([scope]))
            except BaseException as cleanup:
                raise BaseExceptionGroup(
                    "Environment provider validation and cleanup failed",
                    [primary, cleanup],
                ) from None
            raise
        return _OwnedProviderScope(entered=entered, scope=scope)

    def _assert_mutable(self) -> None:
        self._assert_open()
        if self._activation_state != "active" or not self._runtime._accepting_mutations:
            raise EnvironmentError("The Environment run is not active.", code="run_not_active")

    def _schedule_retirement(self, key: _MountKey, owned: _OwnedProviderScope) -> None:
        task = asyncio.create_task(self._retire_scope(key, owned))
        self._retirement_tasks.add(task)
        task.add_done_callback(self._retirement_finished)

    def _retirement_finished(self, task: asyncio.Task[None]) -> None:
        self._retirement_tasks.discard(task)
        if not task.cancelled():
            error = task.exception()
            if error is not None:
                self._retirement_failures.append(error)

    async def _retire_scope(self, key: _MountKey, owned: _OwnedProviderScope) -> None:
        while True:
            async with self._operation_lock:
                drained = self._mount_drained.setdefault(key, asyncio.Event())
                self._refresh_mount_drained(key)
                ready = self._mount_retirement_ready(key)
                if ready:
                    self._entered_by_id.pop(owned.entered.mount_id, None)
            if ready:
                break
            await drained.wait()
        await _close_provider_scopes([owned.scope])
        async with self._operation_lock:
            self._mount_drained.pop(key, None)
            self._active_process_handles.pop(key, None)

    def resolve_path(self, path: str, *, alias: str | None = None) -> EnvironmentPath:
        """Resolve one aggregate or relative path to a provider-local path."""
        route = self._resolve_path(path, alias=alias)
        return EnvironmentPath(mount_id=route.entered.mount_id, path=route.provider_path)

    def _resolve_path(self, path: str, *, alias: str | None = None) -> _ResolvedPath:
        self._assert_open()
        path = _normalize_operation_path(path)

        if _is_absolute_path(path):
            try:
                parsed = parse_mount_path(path)
            except ValueError as exc:
                raise _invalid_operation_path(
                    "Use an absolute POSIX, Windows drive, or UNC path with forward-slash separators."
                ) from exc
            matches: list[tuple[int, _EnteredMount, str, tuple[str, ...]]] = []
            for entered in self._entered.values():
                for root in _mount_paths(entered.public, self._snapshot.default_mount):
                    parsed_root = parse_mount_path(root)
                    suffix = parsed.suffix_below(parsed_root)
                    if suffix is not None:
                        matches.append((parsed_root.depth, entered, root, suffix))
            if not matches:
                raise EnvironmentError(
                    "The absolute path is outside the available Environment mounts.",
                    code="environment_selection_invalid",
                    details={
                        "field": "path",
                        "reason": "path_outside_mounts",
                        "hint": (
                            "Path is outside mounted roots; existence was not checked. Check the active Environment roots. "
                            "If Shell access to this location is authorized, use Shell with cwd inside a mounted root. "
                            "Do not move files or worktrees just to make file-tool routing succeed."
                        ),
                    },
                )
            best_depth = max(item[0] for item in matches)
            selected_matches = [item for item in matches if item[0] == best_depth]
            selected_ids = {item[1].mount_id for item in selected_matches}
            if len(selected_ids) != 1:
                raise EnvironmentError(
                    "The absolute path matches multiple Environment mounts.",
                    code="environment_selection_invalid",
                )
            _depth, selected, mount_path, suffix = selected_matches[0]
            if alias is not None and alias != selected.public.name:
                raise EnvironmentError(
                    "The mount name and absolute path select different mounts.",
                    code="environment_selection_invalid",
                )
            return _ResolvedPath(
                entered=selected,
                provider_path=provider_path_from_suffix(suffix, selected.public.provider_root),
                mount_path=mount_path,
            )

        selected = self._select_entered(alias)
        base = selected.public.default_working_directory or "/"
        if not base.startswith("/") or any(segment in {".", ".."} for segment in base.split("/")):
            raise EnvironmentError(
                "The mount working directory is invalid.",
                code="environment_provider_failure",
            )
        provider_path = base if path == "." else normalize_operation_path(f"{base.rstrip('/')}/{path}")
        return _ResolvedPath(
            entered=selected,
            provider_path=provider_path,
            mount_path=_preferred_mount_path(selected.public, self._snapshot.default_mount),
        )

    def _select_entered(self, alias: str | None) -> _EnteredMount:
        self._assert_open()
        if alias is None:
            entered = self._entered.get(self._snapshot.default_mount or "")
        else:
            entered = next(
                (item for item in self._entered.values() if item.public.name == alias),
                None,
            )
        if entered is None:
            raise EnvironmentError(
                "The selected Environment mount is unavailable.",
                code="environment_selection_invalid",
                details={
                    "field": "alias",
                    "reason": "mount_selection_unavailable",
                    "hint": (
                        "Select an existing mount name from the active Environment context, not a process label. "
                        "Omit alias to use the default mount when one is available."
                    ),
                },
            )
        return entered

    def _select_command_actions(
        self,
        request: CommandRequest,
        *,
        alias: str | None,
        actions: frozenset[EnvironmentAction],
    ) -> tuple[str, bool]:
        entered, _ = self._prepare_command(request, alias=alias)
        return entered.mount_id, actions <= entered.public.permission_ceiling.operations

    def _prepare_command(
        self,
        request: CommandRequest,
        *,
        alias: str | None,
    ) -> tuple[_EnteredMount, CommandRequest]:
        if request.cwd is None:
            entered = self._select_entered(alias)
            cwd = entered.public.default_working_directory or "/"
        else:
            selected = self.resolve_path(request.cwd, alias=alias)
            entered = self._entered_by_id[selected.mount_id]
            cwd = selected.path
        limits = request.limits
        ceiling = entered.public.descriptor.limits.get("max_wall_time_seconds")
        ceiling_value: float | None = None
        if not isinstance(ceiling, bool) and isinstance(ceiling, int | float):
            try:
                candidate = float(ceiling)
            except OverflowError:
                pass
            else:
                if math.isfinite(candidate) and candidate > 0:
                    ceiling_value = candidate
        if ceiling_value is not None:
            requested = limits.wall_time_seconds
            effective = ceiling_value if requested is None else min(requested, ceiling_value)
            limits = limits.model_copy(update={"wall_time_seconds": effective})
        return entered, request.model_copy(update={"cwd": cwd, "limits": limits})

    def _entered_for_process_identity(self, identity: ProcessIdentity) -> _EnteredMount:
        if not isinstance(identity, ProcessIdentity):
            raise EnvironmentError("Process identity is invalid.", code="environment_request_invalid")
        matches = tuple(
            item
            for item in self._entered.values()
            if item.public.provider_type == identity.provider_type and item.environment_id == identity.environment_id
        )
        if not matches:
            raise EnvironmentError(
                "The process Environment instance is not attached.",
                code="environment_selection_invalid",
            )
        if len(matches) != 1:
            raise EnvironmentError(
                "The process Environment instance is attached more than once.",
                code="environment_conflict",
            )
        entered = matches[0]
        if entered.public.descriptor.generation != identity.generation:
            raise EnvironmentError(
                "The process belongs to another Environment generation.",
                code="environment_process_generation_mismatch",
            )
        return entered

    def _entered_for_handle(self, handle: BoundProcessHandle) -> _EnteredMount:
        if not isinstance(handle, BoundProcessHandle):
            raise EnvironmentError("Process handle is invalid.", code="environment_request_invalid")
        entered = self._entered_by_id.get(handle.mount_id)
        if entered is None:
            raise EnvironmentError("Process handle refers to a stale mount.", code="environment_stale_mount")
        if (
            handle.observed_generation != entered.public.descriptor.generation
            or handle.identity.provider_type != entered.public.provider_type
            or handle.identity.environment_id != entered.environment_id
            or handle.identity.generation != entered.public.descriptor.generation
        ):
            raise EnvironmentError("Process handle is stale.", code="environment_stale_mount")
        return entered

    def require_action(self, mount_id: str, action: EnvironmentAction) -> _EnteredMount:
        """Fail closed unless one exact catalog action is effective for the mount."""
        self._assert_open()
        if not isinstance(action, EnvironmentAction):
            raise EnvironmentError(
                "Environment authorization requires an exact catalog action.",
                code="environment_request_invalid",
            )
        entered = self._entered_by_id.get(mount_id)
        if entered is None:
            raise EnvironmentError("Unknown Environment mount.", code="environment_selection_invalid")
        if action not in entered.public.permission_ceiling.operations:
            raise EnvironmentError(
                "Environment operation is denied by the mount permission ceiling.",
                code="environment_denied",
                details={"action": action.value, "mount_id": mount_id},
            )
        return entered

    def _resolve_scoped_file_path(
        self,
        path: str,
        entered: _EnteredMount,
        mount_path: str,
    ) -> EnvironmentPath:
        path = _normalize_operation_path(path)
        if _is_absolute_path(path):
            try:
                suffix = parse_mount_path(path).suffix_below(parse_mount_path(mount_path))
            except ValueError as exc:
                raise _invalid_operation_path(
                    "Use an absolute POSIX, Windows drive, or UNC path with forward-slash separators."
                ) from exc
            if suffix is None:
                raise EnvironmentError(
                    "The scoped file path selects another mount.",
                    code="environment_selection_invalid",
                )
            provider_path = provider_path_from_suffix(suffix, entered.public.provider_root)
        else:
            base = entered.public.default_working_directory or "/"
            if not base.startswith("/") or any(segment in {".", ".."} for segment in base.split("/")):
                raise EnvironmentError(
                    "The mount working directory is invalid.",
                    code="environment_provider_failure",
                )
            provider_path = base if path == "." else normalize_operation_path(f"{base.rstrip('/')}/{path}")
        return EnvironmentPath(mount_id=entered.mount_id, path=provider_path)

    @asynccontextmanager
    async def _prepare_scoped_file(
        self,
        entered: _EnteredMount,
        mount_path: str,
        selected: EnvironmentPath,
        action: EnvironmentAction,
    ) -> AsyncGenerator[_PreparedFile]:
        if selected.mount_id != entered.mount_id:
            raise EnvironmentError(
                "The scoped file path selects another mount incarnation.",
                code="environment_selection_invalid",
            )
        current = self._current_publication(entered)
        if current.public.descriptor.generation != entered.public.descriptor.generation:
            raise EnvironmentError("File scope selection is stale.", code="environment_stale_mount")
        try:
            async with self._operation_lease(current, action, "files", allow_retired=True) as entered:
                if entered.operations.files is None:
                    raise EnvironmentError("File operation facet is unavailable.", code="environment_unsupported")
                yield _PreparedFile(
                    selected=selected,
                    backend=entered.operations.files,
                    validate_result=lambda value: _validate_provider_artifacts(entered, value),
                    virtualize_path=lambda provider_path: self._virtualize_scoped_file_path(
                        entered,
                        mount_path,
                        selected,
                        provider_path,
                    ),
                )
        except TimeoutError as exc:
            raise EnvironmentError(
                "Environment operation timed out.",
                code="environment_timeout",
                details={"timeout_seconds": DEFAULT_ENVIRONMENT_OPERATION_TIMEOUT_SECONDS},
                retry_hint="dependency_change",
            ) from exc

    @staticmethod
    def _virtualize_scoped_file_path(
        entered: _EnteredMount,
        mount_path: str,
        selected: EnvironmentPath,
        provider_path: str,
    ) -> str:
        if selected.mount_id != entered.mount_id:
            raise EnvironmentError(
                "Provider returned a path for another scoped mount incarnation.",
                code="environment_provider_failure",
            )
        try:
            return mount_path_from_provider_path(mount_path, provider_path, entered.public.provider_root)
        except ValueError as exc:
            raise EnvironmentError(
                "Provider returned an invalid file path.",
                code="environment_provider_failure",
            ) from exc

    @asynccontextmanager
    async def _prepare_file(
        self,
        selection: FileScopeSelection,
        action: EnvironmentAction,
    ) -> AsyncGenerator[_PreparedFile]:
        selected = selection.resolved_path
        entered = self._entered_by_id.get(selected.mount_id)
        if entered is None or selection.observed_generation not in {"unprepared", entered.public.descriptor.generation}:
            raise EnvironmentError(
                "Environment mount incarnation is unavailable.",
                code="environment_stale_mount",
            )
        async with self._operation_lease(entered, action, "files") as entered:
            if entered.operations.files is None:
                raise EnvironmentError("File operation facet is unavailable.", code="environment_unsupported")
            root = _preferred_mount_path(entered.public, self._snapshot.default_mount)
            yield _PreparedFile(
                selected=selected,
                backend=entered.operations.files,
                validate_result=lambda value: _validate_provider_artifacts(entered, value),
                virtualize_path=lambda provider_path: _virtualize_path(
                    root, provider_path, entered.public.provider_root
                ),
            )

    def _assert_open(self) -> None:
        if self._closed:
            raise EnvironmentError("The Environment is closed.", code="environment_closed")

    def _validate_live_observation(
        self,
        entered: _EnteredMount,
        availability: object,
        requested: frozenset[EnvironmentOperationFamily] = frozenset(),
    ) -> None:
        if not isinstance(availability, EnvironmentAvailability):
            raise EnvironmentError(
                "Provider returned an invalid availability observation.",
                code="environment_provider_failure",
                details={"mount_id": entered.mount_id},
            )
        if not availability.ready_families <= entered.public.descriptor.operation_families:
            raise EnvironmentError(
                "Provider readiness drifted beyond its published descriptor.",
                code="environment_provider_failure",
                details={"mount_id": entered.mount_id},
            )
        if requested and availability.status == "unavailable":
            raise EnvironmentError(
                "Provider is unavailable for the requested operation.",
                code="environment_unavailable",
                details={"mount_id": entered.mount_id, "reason_code": availability.reason_code},
                retry_hint="dependency_change",
            )
        _validate_operation_facets(entered.public.descriptor, availability, entered.operations)
        if not requested <= availability.ready_families:
            raise EnvironmentError(
                "Provider returned before the requested families became ready.",
                code="environment_unavailable",
                details={"mount_id": entered.mount_id},
                retry_hint="dependency_change",
            )

    async def describe(self, name: str) -> EnvironmentMountObservation:
        self._assert_open()
        entered = self._entered.get(name)
        if entered is None:
            raise EnvironmentError(
                f"Unknown Environment mount: {name!r}.",
                code="environment_selection_invalid",
                details={"name": name},
            )
        availability = entered.provider.availability
        self._validate_live_observation(entered, availability)
        return EnvironmentMountObservation(mount=entered.public, availability=availability)

    async def ensure_ready(self, requirement: EnvironmentReadinessRequirement) -> None:
        async with self._operation_slot():
            await self._ensure_ready(requirement)

    async def _ensure_ready(self, requirement: EnvironmentReadinessRequirement) -> None:
        requested = requirement.operations
        if requirement.mounts is None:
            selected = [
                entered
                for entered in self._entered.values()
                if entered.public.descriptor.operation_families & requested
            ]
        else:
            selected = []
            for name in requirement.mounts:
                entered = self._entered.get(name)
                if entered is None:
                    raise EnvironmentError(
                        f"Unknown Environment mount: {name!r}.",
                        code="environment_selection_invalid",
                        details={"name": name},
                    )
                if not entered.public.descriptor.operation_families & requested:
                    raise EnvironmentError(
                        "An explicitly selected mount advertises none of the requested families.",
                        code="environment_readiness_invalid",
                        details={"name": name},
                    )
                selected.append(entered)

        covered = frozenset().union(*(entered.public.descriptor.operation_families & requested for entered in selected))
        if not selected or covered != requested:
            raise EnvironmentError(
                "The selected mount set does not cover every requested operation family.",
                code="environment_unavailable",
                details={"missing": [str(item) for item in sorted(requested - covered)]},
                retry_hint="dependency_change",
            )

        async def wait_one(entered: _EnteredMount) -> None:
            async with self._mount_slot(entered):
                families = frozenset(entered.public.descriptor.operation_families & requested)
                await asyncio.gather(*(self._ensure_provider_family(entered, family) for family in families))
                entered = self._current_publication(entered)
                self._validate_live_observation(entered, entered.provider.availability, families)

        timeout = requirement.timeout_seconds or DEFAULT_ENVIRONMENT_OPERATION_TIMEOUT_SECONDS
        try:
            async with asyncio.timeout(timeout):
                await asyncio.gather(*(wait_one(entered) for entered in selected))
        except TimeoutError as exc:
            raise EnvironmentError(
                "Environment readiness timed out.",
                code="environment_timeout",
                details={"timeout_seconds": timeout},
                retry_hint="dependency_change",
            ) from exc
        except asyncio.CancelledError:
            raise
        except EnvironmentError:
            raise
        except Exception as exc:
            raise EnvironmentError(
                "Environment provider readiness failed.",
                code="environment_provider_failure",
                retry_hint="dependency_change",
            ) from exc

    @asynccontextmanager
    async def _operation_slot(self) -> AsyncGenerator[None]:
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("Environment operations require an asyncio task")
        async with self._operation_lock:
            if self._closed:
                raise EnvironmentError("The Environment is closed.", code="environment_closed")
            self._operation_tasks[task] = self._operation_tasks.get(task, 0) + 1
        try:
            yield
        finally:
            async with self._operation_lock:
                remaining = self._operation_tasks.get(task, 0) - 1
                if remaining > 0:
                    self._operation_tasks[task] = remaining
                else:
                    self._operation_tasks.pop(task, None)

    @asynccontextmanager
    async def _mount_slot(
        self,
        entered: _EnteredMount,
        *,
        allow_retired: bool = False,
    ) -> AsyncGenerator[None]:
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("Environment operations require an asyncio task")
        key = self._mount_key(entered)
        async with self._operation_lock:
            if self._closed:
                raise EnvironmentError("The Environment is closed.", code="environment_closed")
            current = self._current_publication(entered)
            if current.public.descriptor.generation != entered.public.descriptor.generation or (
                not allow_retired and self._entered.get(entered.public.name) is not current
            ):
                raise EnvironmentError("Environment mount is stale.", code="environment_stale_mount")
            self._operation_tasks[task] = self._operation_tasks.get(task, 0) + 1
            self._register_mount_task_locked(key, task)
        try:
            yield
        finally:
            async with self._operation_lock:
                self._release_task_count(self._operation_tasks, task)
                self._release_mount_task_locked(key, task)

    def _register_mount_task_locked(self, key: _MountKey, task: asyncio.Task[Any]) -> None:
        task_counts = self._mount_tasks.setdefault(key, {})
        task_counts[task] = task_counts.get(task, 0) + 1
        self._refresh_mount_drained(key)

    def _release_mount_task_locked(self, key: _MountKey, task: asyncio.Task[Any]) -> None:
        task_counts = self._mount_tasks.get(key)
        if task_counts is None:
            return
        self._release_task_count(task_counts, task)
        if not task_counts:
            self._mount_tasks.pop(key, None)
        self._refresh_mount_drained(key)

    def _mount_retirement_ready(self, key: _MountKey) -> bool:
        return not self._mount_tasks.get(key) and (self._closed or not self._active_process_handles.get(key))

    def _refresh_mount_drained(self, key: _MountKey) -> None:
        drained = self._mount_drained.setdefault(key, asyncio.Event())
        if self._mount_retirement_ready(key):
            drained.set()
        else:
            drained.clear()

    @staticmethod
    def _release_task_count(tasks: dict[asyncio.Task[Any], int], task: asyncio.Task[Any]) -> None:
        remaining = tasks.get(task, 0) - 1
        if remaining > 0:
            tasks[task] = remaining
        else:
            tasks.pop(task, None)

    @asynccontextmanager
    async def _operation_lease(
        self,
        entered: _EnteredMount,
        action: EnvironmentAction,
        family: EnvironmentOperationFamily,
        *,
        timeout_seconds: float | None = None,
        timeout_code: str = "environment_timeout",
        allow_retired: bool = False,
    ) -> AsyncGenerator[_EnteredMount]:
        timeout = (
            DEFAULT_ENVIRONMENT_OPERATION_TIMEOUT_SECONDS
            if timeout_seconds is None
            else max(
                DEFAULT_ENVIRONMENT_OPERATION_TIMEOUT_SECONDS,
                timeout_seconds + DEFAULT_ENVIRONMENT_OPERATION_TIMEOUT_SECONDS,
            )
        )
        if action not in entered.public.permission_ceiling.operations:
            raise EnvironmentError(
                "Environment operation is denied by the mount permission ceiling.",
                code="environment_denied",
                details={"action": action.value, "mount_id": entered.mount_id},
            )
        async with self._mount_slot(entered, allow_retired=allow_retired):
            try:
                async with asyncio.timeout(timeout):
                    previous_generation = entered.public.descriptor.generation
                    await self._ensure_provider_family(entered, family)
                    entered = self._current_publication(entered)
                    if previous_generation not in {"unprepared", entered.public.descriptor.generation}:
                        raise EnvironmentError("Environment changed before dispatch", code="environment_stale_mount")
                    self.require_action(entered.mount_id, action)
                    self._validate_live_observation(
                        entered,
                        entered.provider.availability,
                        frozenset({family}),
                    )
                    yield entered
            except TimeoutError as exc:
                raise EnvironmentError(
                    "Environment operation timed out.",
                    code=timeout_code,
                    details={"timeout_seconds": timeout},
                    retry_hint="dependency_change",
                ) from exc

    def _current_publication(self, entered: _EnteredMount) -> _EnteredMount:
        current = self._entered_by_id.get(entered.mount_id)
        if current is None or current.provider is not entered.provider:
            raise EnvironmentError("Environment mount is stale.", code="environment_stale_mount")
        return current

    async def _prepare_provider(self, entered: _EnteredMount, family: EnvironmentOperationFamily) -> None:
        try:
            await entered.provider.ensure_ready(frozenset({family}))
        finally:
            # Recovery can publish a new target and then report its replacement to
            # the caller. Publish that observation even when readiness raises.
            async with self._operation_lock:
                current = self._current_publication(entered)
                refreshed = _EnteredMount(
                    current.mount_id, current.configured, current.provider, current.environment_id
                )
                self._validate_live_observation(refreshed, refreshed.provider.availability)
                self._entered_by_id[current.mount_id] = refreshed
                if self._entered.get(current.public.name) is current:
                    self._entered[current.public.name] = refreshed
                    self._snapshot = EnvironmentSnapshot(
                        mounts=tuple(item.public for item in self._entered.values()),
                        default_mount=self._snapshot.default_mount,
                    )
                    if current.public != refreshed.public:
                        self._journal.publish(
                            EnvironmentChange(
                                sequence=self._journal.current_sequence + 1,
                                kind="replaced",
                                name=current.public.name,
                                previous_default=self._snapshot.default_mount,
                                current_default=self._snapshot.default_mount,
                            )
                        )
                if current.public.descriptor.generation != refreshed.public.descriptor.generation:
                    self._active_process_handles.pop(current.mount_id, None)
                    self._refresh_mount_drained(current.mount_id)

    async def _ensure_provider_family(
        self,
        entered: _EnteredMount,
        family: EnvironmentOperationFamily,
    ) -> None:
        key = (entered.mount_id, entered.public.descriptor.generation, family)
        mount_key = self._mount_key(entered)
        async with self._readiness_lock:
            if self._closed:
                raise EnvironmentError("The Environment is closed.", code="environment_closed")
            task = self._readiness_tasks.get(key)
            if task is None:
                async with self._operation_lock:
                    if self._closed:
                        raise EnvironmentError("The Environment is closed.", code="environment_closed")
                    self._current_publication(entered)
                    task = asyncio.create_task(self._prepare_provider(entered, family))
                    self._register_mount_task_locked(mount_key, task)
                    task.add_done_callback(
                        lambda completed, captured=mount_key: self._readiness_worker_finished(captured, completed)
                    )
                self._readiness_tasks[key] = task
            self._readiness_waiters[task] = self._readiness_waiters.get(task, 0) + 1
        try:
            await asyncio.shield(task)
        finally:
            async with self._readiness_lock:
                remaining = self._readiness_waiters.get(task, 0) - 1
                if remaining > 0:
                    self._readiness_waiters[task] = remaining
                else:
                    self._readiness_waiters.pop(task, None)

                if self._readiness_tasks.get(key) is task:
                    if task.done():
                        self._readiness_tasks.pop(key, None)
                        if not task.cancelled():
                            task.exception()
                    elif remaining <= 0:
                        self._readiness_tasks.pop(key, None)
                        task.cancel()

    def _readiness_worker_finished(self, key: _MountKey, task: asyncio.Task[Any]) -> None:
        release = asyncio.create_task(self._release_readiness_mount(key, task))
        _supervise_cleanup_task(release)

    async def _release_readiness_mount(self, key: _MountKey, task: asyncio.Task[Any]) -> None:
        async with self._operation_lock:
            self._release_mount_task_locked(key, task)

    async def _close(self) -> None:
        current = asyncio.current_task()
        failures: list[BaseException] = []
        self._runtime._begin_close()
        async with self._mutation_lock:
            self._activation_state = "closing"
            extension_scopes = tuple(self._extension_scopes)
            self._extension_scopes.clear()
        try:
            await _close_extension_scopes(extension_scopes)
        except BaseException as exc:
            failures.append(exc)
        async with self._mutation_lock:
            async with self._operation_lock:
                self._closed = True
                for key in tuple(self._mount_drained):
                    self._refresh_mount_drained(key)
                operation_tasks = tuple(task for task in self._operation_tasks if task is not current)
        async with self._readiness_lock:
            readiness_tasks = tuple(self._readiness_tasks.values())
            self._readiness_tasks.clear()
            self._readiness_waiters.clear()
        tasks = tuple(dict.fromkeys((*operation_tasks, *readiness_tasks)))
        for task in tasks:
            if not task.done():
                task.cancel()
        pending: set[asyncio.Task[Any]] = set()
        if tasks:
            done, pending = await asyncio.wait(tasks, timeout=DEFAULT_ENVIRONMENT_CLEANUP_TIMEOUT_SECONDS)
            for task in done:
                if not task.cancelled():
                    task.exception()
            if pending:
                failures.append(
                    EnvironmentError(
                        "Environment operations did not stop before cleanup deadline.",
                        code="environment_cleanup_timeout",
                        details={"pending_operations": len(pending)},
                    )
                )

        active_scopes = [owned.scope for owned in self._owned_scopes.values()]
        self._owned_scopes.clear()
        if pending:
            continuation = asyncio.create_task(_close_provider_scopes_after_tasks(tuple(pending), active_scopes))
            _supervise_cleanup_task(continuation)
        else:
            retirement = tuple(self._retirement_tasks)
            if retirement:
                await asyncio.gather(*retirement, return_exceptions=True)
            try:
                await _close_provider_scopes(active_scopes)
            except BaseException as exc:
                failures.append(exc)
        failures.extend(self._retirement_failures)
        if failures:
            raise BaseExceptionGroup("Environment aggregate cleanup failed", failures)

    def dump_states(self) -> Mapping[str, EnvironmentState]:
        """Read each entered adapter's last validated portable state without I/O."""
        states: dict[str, EnvironmentState] = {}
        for entered in self._entered.values():
            state = entered.provider.dump_state()
            if state is None:
                continue
            if state.provider_key != entered.public.provider_type:
                raise EnvironmentError(
                    "Provider state has an incompatible provider key.",
                    code="state_invalid",
                    details={"name": entered.public.name},
                )
            states[entered.public.name] = state
        return states


class NoopBoundEnvironment(CompositeBoundEnvironment):
    """The complete aggregate facade with an empty initial mount set."""


@dataclass(slots=True)
class ManagedEnvironmentRuntime(EnvironmentRuntime):
    """Single-use Host authority over one run's Environment mount set."""

    _initial_mounts: tuple[_MountRequest, ...]
    _default_mount: str | None
    _extensions: tuple[tuple[str, EnvironmentRunExtension], ...] = ()
    _noop: bool = False
    _used: bool = field(default=False, init=False, repr=False)
    _bound: CompositeBoundEnvironment | None = field(default=None, init=False, repr=False)
    _activation_changed: asyncio.Event = field(default_factory=asyncio.Event, init=False, repr=False)
    _activation_error: BaseException | None = field(default=None, init=False, repr=False)
    _accepting_mutations: bool = field(default=False, init=False, repr=False)
    _closed: bool = field(default=False, init=False, repr=False)

    async def wait_until_active(self) -> None:
        await self._activation_changed.wait()
        if self._activation_error is not None:
            raise EnvironmentError(
                "The Environment failed before activation.",
                code="environment_activation_failed",
            ) from self._activation_error
        if not self._accepting_mutations:
            raise EnvironmentError("The Environment run is not active.", code="run_not_active")

    async def mount(
        self,
        name: str,
        mount: EnvironmentEntry | EnvironmentRuntimeMount,
        *,
        make_default: bool = False,
    ) -> EnvironmentChange:
        return await self._require_active_bound()._mount(
            name, _normalize_runtime_mount(mount), make_default=make_default
        )

    async def replace(self, name: str, mount: EnvironmentEntry | EnvironmentRuntimeMount) -> EnvironmentChange:
        return await self._require_active_bound()._replace(name, _normalize_runtime_mount(mount))

    async def unmount(self, name: str) -> EnvironmentChange:
        return await self._require_active_bound()._unmount(name)

    async def set_default(self, name: str | None) -> EnvironmentChange:
        return await self._require_active_bound()._set_default(name)

    def _require_active_bound(self) -> CompositeBoundEnvironment:
        bound = self._bound
        if bound is None or not self._accepting_mutations:
            raise EnvironmentError("The Environment run is not active.", code="run_not_active")
        return bound

    async def _activate(self) -> None:
        bound = self._bound
        if bound is None:
            raise EnvironmentError("The Environment is not entered.", code="run_not_active")
        await bound._activate()

    def _activated(self, bound: CompositeBoundEnvironment) -> None:
        if self._bound is not bound or self._closed:
            raise EnvironmentError("The Environment is closing.", code="environment_closed")
        self._accepting_mutations = True
        self._activation_changed.set()

    def _activation_failed(self) -> None:
        self._activation_error = EnvironmentError(
            "Environment activation failed.",
            code="environment_activation_failed",
        )
        self._activation_changed.set()

    def _begin_close(self) -> None:
        self._accepting_mutations = False
        self._activation_changed.set()

    @asynccontextmanager
    async def bind(
        self,
        *,
        thread_id: str,
        run_id: str,
        instance: AgentInstanceContext,
        host_refs: Mapping[str, str],
    ) -> AsyncGenerator[BoundEnvironment]:
        if (
            not isinstance(thread_id, str)
            or not thread_id.strip()
            or not isinstance(run_id, str)
            or not run_id.strip()
            or not isinstance(instance, AgentInstanceContext)
            or not isinstance(host_refs, Mapping)
        ):
            raise EnvironmentError("Environment run identity is invalid.", code="environment_request_invalid")
        if self._used:
            raise EnvironmentError(
                "EnvironmentRuntime instances are single-use.",
                code="environment_runtime_reused",
            )
        self._used = True
        claimed, reused = _claim_candidate_transfers(self._initial_mounts)
        if reused:
            primary = EnvironmentError(
                "Environment provider binding was already transferred.",
                code="environment_provider_binding_reused",
            )
            try:
                await _await_cleanup_shielded(_discard_candidate_instances(claimed))
            except BaseException as cleanup:
                raise BaseExceptionGroup(
                    "Environment initial candidate reuse rejection and cleanup failed",
                    [primary, cleanup],
                ) from None
            finally:
                self._activation_error = primary
                self._activation_changed.set()
            raise primary

        scopes: list[AbstractAsyncContextManager[BoundEnvironmentProvider]] = []
        entered: dict[str, _EnteredMount] = {}
        successfully_entered: set[int] = set()
        journal = EnvironmentChangeJournal()
        try:
            for requested in self._initial_mounts:
                candidate = requested.candidate
                mount_id = _new_mount_id()
                scope = candidate.bind(
                    thread_id=thread_id,
                    run_id=run_id,
                    instance=instance,
                    mount_id=mount_id,
                    host_refs=host_refs,
                )
                async with asyncio.timeout(DEFAULT_ENVIRONMENT_OPERATION_TIMEOUT_SECONDS):
                    provider = await scope.__aenter__()
                scopes.append(scope)
                successfully_entered.add(id(candidate))
                entered[requested.name] = _validate_entered(requested, mount_id, candidate, provider)

            snapshot = EnvironmentSnapshot(
                mounts=tuple(item.public for item in entered.values()),
                default_mount=self._default_mount,
            )
            owned_scopes = {
                CompositeBoundEnvironment._mount_key(item): _OwnedProviderScope(entered=item, scope=scope)
                for item, scope in zip(entered.values(), scopes, strict=True)
            }
            bound_type = NoopBoundEnvironment if self._noop else CompositeBoundEnvironment
            bound = bound_type(
                thread_id=thread_id,
                run_id=run_id,
                instance=instance,
                host_refs=host_refs,
                snapshot=snapshot,
                entered=entered,
                owned_scopes=owned_scopes,
                journal=journal,
                runtime=self,
                extensions=self._extensions,
            )
            self._bound = bound
            scopes.clear()
            try:
                yield bound
            finally:
                self._begin_close()
                active_error = sys.exception()
                try:
                    await _await_cleanup_shielded(_teardown_entered_environment(bound=bound, journal=journal))
                except BaseException as cleanup_error:
                    if active_error is not None and active_error is not cleanup_error:
                        raise BaseExceptionGroup(
                            "Environment operation and aggregate cleanup failed",
                            [active_error, cleanup_error],
                        ) from None
                    raise
        except BaseException as primary:
            self._activation_error = primary
            self._activation_changed.set()
            try:
                await _discard_candidates(self._initial_mounts, successfully_entered)
            except BaseException as cleanup:
                raise BaseExceptionGroup(
                    "Environment entry and candidate cleanup failed",
                    [primary, cleanup],
                ) from None
            raise
        finally:
            self._accepting_mutations = False
            self._closed = True
            self._activation_changed.set()
            active_error = sys.exception()
            try:
                await _await_cleanup_shielded(_close_provider_scopes(scopes))
            except BaseException as cleanup_error:
                if active_error is not None and active_error is not cleanup_error:
                    raise BaseExceptionGroup(
                        "Environment operation and provider scope cleanup failed",
                        [active_error, cleanup_error],
                    ) from None
                raise


class EmptyEnvironmentRuntime(ManagedEnvironmentRuntime):
    """Empty aggregate that can receive mounts after activation."""

    def __init__(self) -> None:
        super().__init__(
            _initial_mounts=(),
            _default_mount=None,
            _extensions=(),
            _noop=True,
        )


def _validate_initial_mounts(
    mounts: tuple[_MountRequest, ...],
    default_mount: str | None,
) -> None:
    names = [item.name for item in mounts]
    candidate_ids = [id(item.candidate._transfer_owner) for item in mounts]
    if len(names) != len(set(names)):
        raise EnvironmentError("Environment mount names must be unique.", code="environment_request_invalid")
    if len(candidate_ids) != len(set(candidate_ids)):
        raise EnvironmentError(
            "One provider candidate cannot occupy multiple mounts.",
            code="environment_request_invalid",
        )
    for name in names:
        _validate_mount_name(name)
    if default_mount is not None and default_mount not in names:
        raise EnvironmentError(
            "default_mount is not present in mounts.",
            code="environment_request_invalid",
        )
    _validate_route_paths(tuple((item.name, item.mount_path) for item in mounts), default_mount)


def _validate_route_paths(
    mounts: Sequence[tuple[str, str | None]],
    default_mount: str | None,
) -> None:
    owners: dict[tuple[str, ...], str] = {}
    for name, mount_path in mounts:
        for path in _mount_paths_for_values(name, mount_path, default_mount):
            try:
                key = parse_mount_path(path).comparison_key
            except ValueError as exc:
                raise EnvironmentError(
                    "Environment mount path is invalid.",
                    code="environment_request_invalid",
                    details={"name": name},
                ) from exc
            previous = owners.setdefault(key, name)
            if previous != name:
                raise EnvironmentError(
                    "Environment mount paths must be unique.",
                    code="environment_request_invalid",
                    details={"name": name, "conflict": previous},
                )


def _mount_paths(info: EnvironmentMountInfo, default_mount: str | None) -> tuple[str, ...]:
    return _mount_paths_for_values(info.name, info.mount_path, default_mount)


def _mount_paths_for_values(
    name: str,
    mount_path: str | None,
    default_mount: str | None,
) -> tuple[str, ...]:
    if mount_path is not None:
        return (mount_path,)
    named = f"/environment/{name}"
    return ("/workspace", named) if name == default_mount else (named,)


def _preferred_mount_path(info: EnvironmentMountInfo, default_mount: str | None) -> str:
    if info.mount_path is not None:
        return info.mount_path
    return "/workspace" if info.name == default_mount else f"/environment/{info.name}"


def _invalid_operation_path(hint: str) -> EnvironmentError:
    return EnvironmentError(
        "Environment path is invalid.",
        code="environment_request_invalid",
        details={"field": "path", "reason": "invalid_path", "hint": hint},
        retry_hint="request_change",
    )


def _normalize_operation_path(path: str) -> str:
    try:
        return normalize_operation_path(path)
    except ValueError as exc:
        raise _invalid_operation_path(str(exc)) from exc


def _is_absolute_path(path: str) -> bool:
    return path.startswith("/") or (len(path) >= 3 and path[0].isalpha() and path[1:3] == ":/")


def _validate_mount_name(name: str) -> None:
    try:
        EnvironmentSnapshot(mounts=(), default_mount=name)
    except (TypeError, ValueError) as exc:
        raise EnvironmentError("Environment mount name is invalid.", code="environment_request_invalid") from exc


def _new_mount_id() -> str:
    return f"mount-{secrets.token_urlsafe(9)}"


def _validate_entered(
    requested: _MountRequest,
    mount_id: str,
    candidate: EnvironmentProviderBinding,
    provider: BoundEnvironmentProvider,
) -> _EnteredMount:
    descriptor = provider.descriptor
    availability = provider.availability
    operations = provider.operations
    if not isinstance(descriptor, EnvironmentDescriptor):
        raise EnvironmentError("Provider returned an invalid descriptor.", code="environment_provider_failure")
    if not isinstance(availability, EnvironmentAvailability):
        raise EnvironmentError("Provider returned invalid availability.", code="environment_provider_failure")
    if not isinstance(operations, EnvironmentProviderOperations):
        raise EnvironmentError("Provider returned invalid operations.", code="environment_provider_failure")
    if provider.provider_key != candidate.provider_type or provider.environment_id != candidate.environment_id:
        raise EnvironmentError("Provider identity changed during entry.", code="environment_provider_failure")
    if not availability.ready_families <= descriptor.operation_families:
        raise EnvironmentError("Provider readiness advertises an absent family.", code="environment_provider_failure")
    if not callable(getattr(provider, "ensure_ready", None)):
        raise EnvironmentError("Provider has no readiness path.", code="environment_provider_failure")
    if not callable(getattr(provider, "dump_state", None)):
        raise EnvironmentError("Provider has no state cache path.", code="environment_provider_failure")

    _validate_operation_facets(descriptor, availability, operations)

    effective = EnvironmentPermissionSet(
        operations=requested.permission_ceiling.operations & descriptor.permissions.operations
    )
    public = EnvironmentMountInfo(
        name=requested.name,
        provider_type=provider.provider_key,
        descriptor=descriptor,
        permission_ceiling=effective,
        default_working_directory=requested.default_working_directory,
        mount_path=requested.mount_path,
        provider_root=requested.provider_root,
    )
    return _EnteredMount(
        mount_id=mount_id,
        configured=public,
        provider=provider,
        environment_id=provider.environment_id,
    )


def _validate_operation_facets(
    descriptor: EnvironmentDescriptor,
    availability: EnvironmentAvailability,
    operations: EnvironmentProviderOperations,
) -> None:
    if not isinstance(operations, EnvironmentProviderOperations):
        raise EnvironmentError("Provider returned invalid operations.", code="environment_provider_failure")
    facet_families = {
        family
        for family in ("files", "shell", "processes", "ports", "outputs")
        if getattr(operations, family) is not None
    }
    advertised_facets = set(descriptor.operation_families)
    required_facets = availability.ready_families if availability.status == "preparing" else advertised_facets
    if not facet_families <= advertised_facets or not required_facets <= facet_families:
        raise EnvironmentError(
            "Provider descriptor and operation facets disagree.",
            code="environment_provider_failure",
        )
    for action in descriptor.permissions.operations:
        dispatch = ENVIRONMENT_ACTION_DISPATCH[action]
        if dispatch.facet not in facet_families:
            continue
        method = getattr(getattr(operations, dispatch.facet), dispatch.method, None)
        if not callable(method):
            raise EnvironmentError(
                "Provider permission has no executable semantic method.",
                code="environment_provider_failure",
                details={"action": action.value},
            )


_SUPERVISED_CLEANUP_TASKS: set[asyncio.Task[Any]] = set()


def _consume_background_task(task: asyncio.Task[Any]) -> None:
    _SUPERVISED_CLEANUP_TASKS.discard(task)
    if not task.cancelled():
        task.exception()


def _supervise_cleanup_task(task: asyncio.Task[Any]) -> None:
    _SUPERVISED_CLEANUP_TASKS.add(task)
    task.add_done_callback(_consume_background_task)


async def _await_cleanup_shielded(operation: Coroutine[Any, Any, None]) -> None:
    """Finish one owned teardown worker before propagating any caller cancellation."""
    cleanup = asyncio.create_task(operation)
    cancellations: list[asyncio.CancelledError] = []
    current = asyncio.current_task()
    while not cleanup.done():
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError as exc:
            cancellations.append(exc)
            if current is not None:
                current.uncancel()
    cleanup_error: BaseException | None = None
    try:
        cleanup.result()
    except BaseException as exc:
        cleanup_error = exc
    if cancellations:
        if cleanup_error is not None:
            raise BaseExceptionGroup(
                "Environment cancellation and cleanup failed",
                [cancellations[0], cleanup_error],
            ) from None
        raise cancellations[0]
    if cleanup_error is not None:
        raise cleanup_error


async def _teardown_entered_environment(
    *,
    bound: CompositeBoundEnvironment,
    journal: EnvironmentChangeJournal,
) -> None:
    failures: list[BaseException] = []
    try:
        await bound._close()
    except BaseException as exc:
        failures.append(exc)
    try:
        await journal.close()
    except BaseException as exc:
        failures.append(exc)
    if failures:
        raise BaseExceptionGroup("Environment entered-resource cleanup failed", failures)


async def _close_extension_scopes(
    scopes: tuple[tuple[str, AbstractAsyncContextManager[None]], ...],
) -> None:
    failures: list[BaseException] = []
    for extension_id, scope in reversed(scopes):
        try:
            await scope.__aexit__(None, None, None)
        except BaseException:
            failures.append(
                EnvironmentError(
                    "Environment run extension cleanup failed.",
                    code="environment_extension_cleanup_failed",
                    details={"extension_id": extension_id},
                )
            )
    if failures:
        raise BaseExceptionGroup("Environment run extension cleanup failed", failures)


async def _close_provider_scopes_after_tasks(
    tasks: tuple[asyncio.Task[Any], ...],
    scopes: list[AbstractAsyncContextManager[BoundEnvironmentProvider]],
) -> None:
    await asyncio.gather(*tasks, return_exceptions=True)
    await _close_provider_scopes(scopes)


async def _raise_with_provider_cleanup(
    primary: BaseException,
    scope: AbstractAsyncContextManager[BoundEnvironmentProvider],
) -> NoReturn:
    try:
        await _await_cleanup_shielded(_close_provider_scopes([scope]))
    except BaseException as cleanup:
        if cleanup is not primary:
            raise BaseExceptionGroup(
                "Environment mutation and prepared provider cleanup failed",
                [primary, cleanup],
            ) from None
    raise primary.with_traceback(primary.__traceback__)


async def _close_provider_scopes(
    scopes: list[AbstractAsyncContextManager[BoundEnvironmentProvider]],
) -> None:
    failures: list[BaseException] = []
    for scope in reversed(scopes):
        task = asyncio.create_task(scope.__aexit__(None, None, None))
        done, _ = await asyncio.wait(
            (task,),
            timeout=DEFAULT_ENVIRONMENT_CLEANUP_TIMEOUT_SECONDS,
        )
        if task not in done:
            task.cancel()
            _supervise_cleanup_task(task)
            failures.append(
                EnvironmentError(
                    "Environment provider scope cleanup timed out.",
                    code="environment_cleanup_timeout",
                )
            )
            continue
        try:
            task.result()
        except BaseException as exc:
            failures.append(exc)
    if failures:
        raise BaseExceptionGroup("Environment provider scope cleanup failed", failures)


def _claim_candidate_transfers(
    requests: tuple[_MountRequest, ...],
) -> tuple[tuple[EnvironmentProviderBinding, ...], tuple[EnvironmentProviderBinding, ...]]:
    candidates = {id(item.candidate): item.candidate for item in requests}
    claimed: list[EnvironmentProviderBinding] = []
    reused: list[EnvironmentProviderBinding] = []
    for candidate in candidates.values():
        transferred = candidate._claim_transfer()
        (claimed if transferred else reused).append(candidate)
    return tuple(claimed), tuple(reused)


async def _discard_candidate_instances(candidates: tuple[EnvironmentProviderBinding, ...]) -> None:
    failures: list[BaseException] = []
    for candidate in candidates:
        task = asyncio.create_task(candidate.discard())
        done, _ = await asyncio.wait((task,), timeout=DEFAULT_ENVIRONMENT_CLEANUP_TIMEOUT_SECONDS)
        if task not in done:
            task.cancel()
            _supervise_cleanup_task(task)
            failures.append(
                EnvironmentError(
                    "Environment candidate discard timed out.",
                    code="environment_cleanup_timeout",
                )
            )
            continue
        try:
            task.result()
        except BaseException as exc:
            failures.append(exc)
    if failures:
        raise BaseExceptionGroup("Environment candidate discard failed", failures)


async def _discard_candidates(
    requests: tuple[_MountRequest, ...],
    successfully_entered: set[int],
) -> None:
    candidates = {
        id(item.candidate): item.candidate for item in requests if id(item.candidate) not in successfully_entered
    }
    await _discard_candidate_instances(tuple(candidates.values()))


def _normalize_environment_run_extensions(
    extensions: Sequence[EnvironmentRunExtension],
) -> tuple[tuple[str, EnvironmentRunExtension], ...]:
    try:
        supplied = tuple(extensions)
    except TypeError:
        raise EnvironmentError(
            "Environment run extensions must be a finite sequence.",
            code="environment_extension_id_invalid",
        ) from None
    seen: set[str] = set()
    captured: list[tuple[str, EnvironmentRunExtension]] = []
    for extension in supplied:
        if not isinstance(extension, EnvironmentRunExtension):
            raise EnvironmentError(
                "Environment run extensions must implement EnvironmentRunExtension.",
                code="environment_extension_id_invalid",
            )
        try:
            extension_id = extension.extension_id
        except Exception:
            raise EnvironmentError(
                "Environment run extension ID could not be read.",
                code="environment_extension_id_invalid",
            ) from None
        if (
            not isinstance(extension_id, str)
            or not extension_id
            or extension_id != extension_id.strip()
            or len(extension_id) > _MAX_ENVIRONMENT_EXTENSION_ID_LENGTH
        ):
            raise EnvironmentError(
                "Environment run extension IDs must be bounded non-blank strings without surrounding whitespace.",
                code="environment_extension_id_invalid",
            )
        if extension_id in seen:
            raise EnvironmentError(
                "Environment run extension IDs must be unique.",
                code="environment_extension_duplicate",
                details={"extension_id": extension_id},
            )
        seen.add(extension_id)
        captured.append((extension_id, extension))
    return tuple(captured)


def _virtualize_path(root: str, provider_path: str, provider_root: str) -> str:
    try:
        return mount_path_from_provider_path(root, provider_path, provider_root)
    except ValueError as exc:
        raise EnvironmentError(
            "Provider returned an invalid file path.",
            code="environment_provider_failure",
        ) from exc


def create_environment_runtime(
    *,
    mounts: Mapping[str, EnvironmentEntry | EnvironmentRuntimeMount],
    default_mount: str | None = None,
    extensions: Sequence[EnvironmentRunExtension] = (),
) -> EnvironmentRuntime:
    """Capture one atomic initial mount set in a fresh single-use runtime."""
    try:
        normalized = {name: _normalize_runtime_mount(entry) for name, entry in dict(mounts).items()}
        captured = tuple(
            _MountRequest(
                name=name,
                permission_ceiling=mount.permission_ceiling.model_copy(deep=True),
                default_working_directory=mount.working_directory,
                mount_path=mount.mount_path,
                provider_root=mount.provider_root,
                candidate=mount.binding,
            )
            for name, mount in normalized.items()
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise EnvironmentError("Environment mounts are invalid.", code="environment_request_invalid") from exc
    captured_extensions = _normalize_environment_run_extensions(extensions)
    _validate_initial_mounts(captured, default_mount)
    return ManagedEnvironmentRuntime(
        _initial_mounts=captured,
        _default_mount=default_mount,
        _extensions=captured_extensions,
    )


def create_empty_environment_runtime() -> EnvironmentRuntime:
    """Create the empty runtime used by embedded runs without initial mounts."""
    return EmptyEnvironmentRuntime()
