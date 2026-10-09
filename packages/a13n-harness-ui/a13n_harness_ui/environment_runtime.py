"""Host-authoritative Project Environment preparation and state publication."""

from __future__ import annotations

import hashlib
import inspect
import os
import stat
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from types import MappingProxyType
from typing import Literal

from a13n_environment.definition import EnvironmentProviderDefinition
from a13n_environment.direct_local.provider import DIRECT_LOCAL
from a13n_environment.errors import observed_environment_state
from a13n_environment.execution import EnvironmentConnector
from a13n_environment.local_envd.runtime import (
    LocalEnvdProviderRuntime,
    resolve_a13n_envd_executable,
)
from a13n_environment.models import FILE_EXECUTION_ACTIONS, EnvironmentState
from a13n_harness.environment import (
    FILE_ACTIONS,
    FILE_READ_ACTIONS,
    EnvironmentAction,
    EnvironmentMount,
    EnvironmentPermissionSet,
    EnvironmentRunExtension,
    EnvironmentRunExtensionFactoryContext,
)
from a13n_harness.environment.advanced import create_environment_runtime
from a13n_harness.environment.providers import EnvironmentRuntime
from anyio import CancelScope, Lock, fail_after, to_thread

from a13n_harness_ui.composition import ResolvedEnvironmentProfile, ResolvedRunComposition
from a13n_harness_ui.composition.models import ResolvedEnvironmentBinding
from a13n_harness_ui.configuration.models import HttpDeviceTransport, canonical_digest
from a13n_harness_ui.devices import DeviceConnections
from a13n_harness_ui.environment_paths import BUILTIN_SKILLS_PATH, BUILTIN_SKILLS_ROOT, EnvironmentPathLayout
from a13n_harness_ui.errors import CompositionError, EnvironmentLifecycleError
from a13n_harness_ui.extensions import (
    LOCAL_ENVD_PROVIDER_KEY,
    NATIVE_PROVIDER_KEY,
    EnvironmentProjectAdapter,
    HarnessUiExtensionCatalog,
)
from a13n_harness_ui.extensions.environment_adapters import (
    LocalEnvdProfileConfiguration,
    LocalEnvdProjectAdapter,
    LocalEnvdProjectConfiguration,
    NativeProjectAdapter,
)
from a13n_harness_ui.managed_runtime import ManagedEnvdRuntime
from a13n_harness_ui.sandbox import create_sandbox_runtime
from a13n_harness_ui.settings import EnvdRuntimeSettings
from a13n_harness_ui.storage import EnvironmentBindingKey, LocalStore, ObjectRef, StoredEnvironmentState
from a13n_harness_ui.storage.objects import ObjectKind
from a13n_harness_ui.thread_files import ThreadFiles

type ProviderRuntimeFactory = Callable[[EnvironmentProviderDefinition], object | Awaitable[object | None] | None]


@dataclass(frozen=True, slots=True)
class ReconstructedEnvironmentProfile:
    """A resolved profile paired with current trusted process-local implementations."""

    profile: ResolvedEnvironmentProfile
    adapter: EnvironmentProjectAdapter
    provider: EnvironmentProviderDefinition


@dataclass(frozen=True, slots=True)
class EnvironmentStatePublication:
    key: EnvironmentBindingKey
    previous: ObjectRef | None
    replacement: ObjectRef | None
    status: Literal["unchanged", "published", "failed"]
    error: Exception | None = None


@dataclass(frozen=True, slots=True)
class EnvironmentFinalization:
    cleanup_errors: tuple[Exception, ...]
    state_publications: tuple[EnvironmentStatePublication, ...]


@dataclass(slots=True)
class _PreparedMount:
    alias: str
    environment: EnvironmentConnector
    permission_ceiling: EnvironmentPermissionSet
    mount_path: str | None
    provider_root: str = "/"


class EnvironmentSnapshotReconstructor:
    """Resolve current trusted Provider/adapter implementations for a composition."""

    def __init__(
        self,
        *,
        catalog: HarnessUiExtensionCatalog | None = None,
        envd_settings: EnvdRuntimeSettings | None = None,
        runtime_factories: Mapping[str, ProviderRuntimeFactory] | None = None,
        local_runtime_parent: Path | None = None,
        protected_roots: tuple[Path, ...] = (),
    ) -> None:
        self._catalog = catalog or HarnessUiExtensionCatalog()
        self._envd_settings = envd_settings or EnvdRuntimeSettings()
        self._runtime_factories = MappingProxyType(dict(runtime_factories or {}))
        self._local_runtime_parent = local_runtime_parent
        self._protected_roots = protected_roots
        self._local_runtimes: dict[tuple[tuple[Path, ...], str], LocalEnvdProviderRuntime] = {}
        self._local_lock = Lock()
        self._managed_envd = (
            None
            if local_runtime_parent is None
            else ManagedEnvdRuntime(
                cache_root=local_runtime_parent / "managed",
                staging_root=local_runtime_parent / "staging",
                settings=self._envd_settings,
            )
        )

    def reconstruct(self, profile: ResolvedEnvironmentProfile) -> ReconstructedEnvironmentProfile:
        provider = self._catalog.provider_catalog((profile.provider_key,)).require(profile.provider_key)
        adapter = self._catalog.environment_adapter(profile.adapter_key, profile.provider_key)
        adapter.validate_profile(
            provider_configuration=profile.provider_configuration,
            adapter_configuration=profile.adapter_configuration,
            provider=provider,
        )
        return ReconstructedEnvironmentProfile(profile=profile, adapter=adapter, provider=provider)

    async def bind(
        self,
        reconstructed: ReconstructedEnvironmentProfile,
        *,
        root: Path,
        state: EnvironmentState | None,
        local_runtime: LocalEnvdProviderRuntime | None = None,
    ) -> EnvironmentConnector:
        collaborator = (
            local_runtime or await self.sandbox_runtime((root,), profile=reconstructed.profile)
            if reconstructed.provider.type == LOCAL_ENVD_PROVIDER_KEY
            else await self._runtime_collaborator(reconstructed.provider)
        )
        try:
            environment = await reconstructed.adapter.bind(
                profile=reconstructed.profile,
                root=root,
                state=state,
                provider=reconstructed.provider,
                runtime=collaborator,
            )
        except CompositionError:
            raise
        except Exception as exc:
            raise EnvironmentLifecycleError(
                "The Environment Project adapter could not construct an Environment.",
                code="environment_binding_failed",
                details={"adapter_key": reconstructed.profile.adapter_key},
            ) from exc
        if (
            not isinstance(environment, EnvironmentConnector)
            or environment.provider_key != reconstructed.profile.provider_key
        ):
            raise EnvironmentLifecycleError(
                "The Environment Project adapter returned an incompatible Environment.",
                code="environment_binding_invalid",
                details={"adapter_key": reconstructed.profile.adapter_key},
            )
        return environment

    async def create_extensions(self, composition: ResolvedRunComposition) -> tuple[EnvironmentRunExtension, ...]:
        recipes = composition.environment_run_extensions
        catalog = self._catalog.run_extension_catalog(tuple(item.extension_key for item in recipes))
        return tuple(
            catalog.create_extension(
                EnvironmentRunExtensionFactoryContext(
                    extension_key=item.extension_key,
                    extension_id=item.extension_id,
                    configuration=item.configuration,
                )
            )
            for item in recipes
        )

    async def resolve_sandbox_executable(self) -> Path:
        """Resolve the exact Host-selected runtime for preparation and preflight."""
        executable = self._envd_settings.executable
        if executable is not None:
            return await to_thread.run_sync(partial(resolve_a13n_envd_executable, executable))
        if self._managed_envd is None:
            raise EnvironmentLifecycleError(
                "Local Envd requires the Harness UI runtime cache or an executable override.",
                code="local_envd_runtime_unavailable",
            )
        return await self._managed_envd.resolve()

    async def sandbox_runtime(
        self,
        roots: tuple[Path, ...],
        *,
        thread_files_root: Path | None = None,
        profile: ResolvedEnvironmentProfile | None = None,
    ) -> LocalEnvdProviderRuntime:
        # Each immutable grant set gets its own Device; Sessions share only that
        # Device, never a union of grants from different Threads or Projects.
        selected = LocalEnvdProfileConfiguration.model_validate(
            {} if profile is None else profile.provider_configuration
        )
        adapter = LocalEnvdProjectConfiguration.model_validate({} if profile is None else profile.adapter_configuration)
        roots = tuple(root.resolve(strict=True) for root in roots)
        # Destinations and credential references are Session inputs, not Device identity.
        key = (
            roots,
            canonical_digest(
                {"launch": selected.launch.model_dump(mode="json"), "adapter": adapter.model_dump(mode="json")}
            ),
        )
        async with self._local_lock:
            if key not in self._local_runtimes:
                executable = await self.resolve_sandbox_executable()
                self._local_runtimes[key] = await to_thread.run_sync(
                    partial(
                        create_sandbox_runtime,
                        executable,
                        roots=roots,
                        runtime_parent=self._local_runtime_parent,
                        protected_roots=self._protected_roots,
                        thread_files_root=thread_files_root,
                        launch=selected.launch,
                        project_access=adapter.project_access,
                    )
                )
            return self._local_runtimes[key]

    async def close(self) -> None:
        async with AsyncExitStack() as stack:
            for runtime in self._local_runtimes.values():
                stack.push_async_callback(runtime.close)

    async def _runtime_collaborator(self, provider: EnvironmentProviderDefinition) -> object | None:
        if provider.type == NATIVE_PROVIDER_KEY:
            return None
        factory = self._runtime_factories.get(provider.type)
        if factory is None:
            raise EnvironmentLifecycleError(
                "No trusted runtime factory is registered for the selected Environment Provider.",
                code="provider_runtime_unsupported",
                details={"provider_key": provider.type},
            )
        value = factory(provider)
        return await value if inspect.isawaitable(value) else value


class EnvironmentRunPlan:
    """Inert connectors and the outcomes already published during Host preparation."""

    def __init__(
        self,
        *,
        publications: Sequence[EnvironmentStatePublication],
        profile: ResolvedEnvironmentProfile,
        mounts: Sequence[_PreparedMount],
        runtime: EnvironmentRuntime,
        tool_result_directory: str,
        default_environment: str,
    ) -> None:
        self._publications = tuple(publications)
        self.profile = profile
        self._mounts = tuple(mounts)
        self.runtime = runtime
        self.tool_result_directory = tool_result_directory
        self.environments: Mapping[str, EnvironmentConnector] = MappingProxyType(
            {item.alias: item.environment for item in mounts}
        )
        self.default_environment = default_environment

    @property
    def finalization(self) -> EnvironmentFinalization:
        """Report management publication; Harness owns execution cleanup."""
        return EnvironmentFinalization(cleanup_errors=(), state_publications=self._publications)


class EnvironmentRunService:
    """Prepare Project roots from an admitted immutable Run composition."""

    def __init__(
        self,
        store: LocalStore,
        reconstructor: EnvironmentSnapshotReconstructor,
        *,
        user_skills_root: Path | None = None,
        thread_files: ThreadFiles | None = None,
        configuration_root: Path | None = None,
        devices: DeviceConnections | None = None,
    ) -> None:
        self._store = store
        self._reconstructor = reconstructor
        self._user_skills_root = user_skills_root
        self._thread_files = thread_files or ThreadFiles(store.layout.root)
        self._configuration_root = configuration_root
        self._devices = devices

    async def prepare(self, composition: ResolvedRunComposition) -> EnvironmentRunPlan:
        profile = composition.environment_profile
        reconstructed = self._reconstructor.reconstruct(profile)
        roots = await normalize_project_roots(composition.project_roots) if composition.project_roots else ()
        canonical_host_paths = reconstructed.adapter.preserves_host_paths
        content_plugins = tuple((item.plugin_id, item.path, item.skills_path) for item in composition.content_plugins)
        path_layout = EnvironmentPathLayout.resolve(
            canonical_host_paths=canonical_host_paths,
            project_roots=roots,
            user_skills_root=self._user_skills_root,
            content_plugins=content_plugins,
        )
        thread_root = await self._thread_files.touch(composition.thread_id)
        local_runtime = (
            await self._reconstructor.sandbox_runtime(
                (*roots, thread_root), thread_files_root=thread_root, profile=profile
            )
            if isinstance(reconstructed.adapter, LocalEnvdProjectAdapter)
            else None
        )
        mounts: list[_PreparedMount] = []
        publications: list[EnvironmentStatePublication] = []
        for index, root in enumerate(roots, start=1):
            key = EnvironmentBindingKey(
                thread_id=composition.thread_id,
                environment_profile_id=profile.profile_id,
                profile_digest=profile.behavior_digest,
                adapter_key=profile.adapter_key,
                normalized_root=os.fspath(root),
            )
            head = await self._store.environment_states.get(key)
            expected = None if head is None else head.state
            state = await self._load_state(
                key=key,
                reference=expected,
                provider_key=profile.provider_key,
            )
            try:
                environment = await self._reconstructor.bind(
                    reconstructed,
                    root=root,
                    state=state,
                    local_runtime=local_runtime,
                )
            except BaseException as error:
                with CancelScope(shield=True):
                    try:
                        await self._publish_state(key, expected, state, observed_environment_state(error, state))
                    except Exception as publication_error:
                        error.add_note(f"Environment state publication also failed: {publication_error!r}")
                raise
            publications.append(await self._publish_state(key, expected, state, environment.state))
            mounts.append(
                _PreparedMount(
                    alias="workspace" if index == 1 else f"workspace-{index}",
                    environment=environment,
                    permission_ceiling=EnvironmentPermissionSet(operations=FILE_EXECUTION_ACTIONS),
                    mount_path=(path_layout.project_mounts[index - 1] if canonical_host_paths else None),
                    provider_root=root.as_posix()
                    if isinstance(reconstructed.adapter, LocalEnvdProjectAdapter)
                    else "/",
                )
            )
        for binding in composition.environment_bindings:
            if isinstance(binding.device.transport, HttpDeviceTransport):
                composition.run_configuration.authorize_url(binding.device.transport.configuration.endpoint)
            mount, publication = await self._prepare_device_mount(composition.thread_id, binding)
            mounts.append(mount)
            publications.append(publication)
        for index, ((_plugin_id, root, _skills), (_layout_id, mount_path)) in enumerate(
            zip(content_plugins, path_layout.content_plugin_roots, strict=True),
            start=1,
        ):
            if canonical_host_paths and Path(root) in roots:
                continue
            # An uninstall is a real deletion; do not recreate a captured path.
            if not await to_thread.run_sync(Path(root).exists):
                continue
            mounts.append(
                await self._prepare_host_files_mount(
                    root=Path(root),
                    alias=f"content-plugin-{index}",
                    mount_path=mount_path,
                )
            )
        if _root_selects_skills(composition):
            mounts.append(
                await self._prepare_host_files_mount(
                    root=BUILTIN_SKILLS_ROOT,
                    alias="builtin-skills",
                    mount_path=BUILTIN_SKILLS_PATH,
                    file_actions=FILE_READ_ACTIONS,
                )
            )
            if not (canonical_host_paths and Path(path_layout.user_skills) in roots):
                mounts.append(
                    await self._prepare_user_skills_mount(
                        mount_path=(path_layout.user_skills if canonical_host_paths else None)
                    )
                )
        if self._configuration_root is not None:
            root = self._configuration_root.expanduser().resolve()
            if not (canonical_host_paths and any(mount.mount_path == root.as_posix() for mount in mounts)):
                mounts.append(
                    await self._prepare_host_files_mount(
                        root=root,
                        alias="configuration",
                        mount_path=root.as_posix() if canonical_host_paths else None,
                    )
                )
        thread_mount = await self._prepare_thread_files_mount(reconstructed, thread_root, local_runtime=local_runtime)
        if roots:
            mounts.append(thread_mount)
        else:
            mounts.insert(0, thread_mount)
        extensions = await self._reconstructor.create_extensions(composition)
        runtime = create_environment_runtime(
            mounts={
                item.alias: EnvironmentMount(
                    connector=item.environment,
                    permission_ceiling=item.permission_ceiling,
                    working_directory=f"{item.provider_root.rstrip('/')}/tmp" if item.alias == "thread-files" else None,
                    mount_path=item.mount_path,
                    provider_root=item.provider_root,
                )
                for item in mounts
            },
            default_mount=composition.default_environment or mounts[0].alias,
            extensions=extensions,
        )
        return EnvironmentRunPlan(
            publications=publications,
            profile=profile,
            mounts=mounts,
            runtime=runtime,
            default_environment=composition.default_environment or mounts[0].alias,
            tool_result_directory=(
                f"{(thread_mount.mount_path or '/environment/thread-files').rstrip('/')}/tmp/tool-results"
            ),
        )

    async def _prepare_device_mount(
        self, thread_id: str, binding: ResolvedEnvironmentBinding
    ) -> tuple[_PreparedMount, EnvironmentStatePublication]:
        if self._devices is None:
            raise EnvironmentLifecycleError(
                "Device connections are unavailable.", code="device_connections_unavailable"
            )
        http = isinstance(binding.device.transport, HttpDeviceTransport)
        provider_key = "http_envd" if http else "websocket_envd"
        adapter_key = provider_key
        selection = binding.selection
        key = EnvironmentBindingKey(
            thread_id=thread_id,
            environment_profile_id="",
            device_id=binding.device.id,
            alias=selection.alias,
            profile_digest=canonical_digest(
                {
                    "device": binding.device.model_dump(mode="json", exclude={"name", "id"}),
                    "selection": selection.model_dump(mode="json"),
                }
            ),
            adapter_key=adapter_key,
            normalized_root=selection.working_directory,
        )
        head = await self._store.environment_states.get(key)
        expected = None if head is None else head.state
        state = await self._load_state(key=key, reference=expected, provider_key=provider_key)
        environment = await self._devices.bind(binding, environment_id=f"device-{key.profile_digest[:20]}", state=state)
        publication = await self._publish_state(key, expected, state, environment.state)
        return _PreparedMount(
            alias=selection.alias,
            environment=environment,
            permission_ceiling=selection.permission_ceiling,
            mount_path=f"/environment/{selection.alias}",
        ), publication

    async def _prepare_thread_files_mount(
        self,
        reconstructed: ReconstructedEnvironmentProfile,
        root: Path,
        *,
        local_runtime: LocalEnvdProviderRuntime | None = None,
    ) -> _PreparedMount:
        if isinstance(reconstructed.adapter, (NativeProjectAdapter, LocalEnvdProjectAdapter)):
            environment = await self._reconstructor.bind(
                reconstructed, root=root, state=None, local_runtime=local_runtime
            )
            operations = FILE_EXECUTION_ACTIONS
        else:
            # Host files are not silently interpreted as a remote provider root.
            provider = DIRECT_LOCAL
            configuration = provider.validate_environment(
                {
                    "root": {"path": os.fspath(root)},
                    "shell_profiles": [],
                    "allowed_executables": [],
                    "allowed_ports": [],
                    "allowed_environment_keys": [],
                },
            )
            environment = provider.execution_connector(
                environment_id=f"thread-files-{root.name}",
                environment=configuration,
                state=None,
                runtime=None,
            )
            operations = FILE_ACTIONS
        return _PreparedMount(
            alias="thread-files",
            environment=environment,
            permission_ceiling=EnvironmentPermissionSet(operations=operations),
            mount_path=root.as_posix() if reconstructed.adapter.preserves_host_paths else None,
            provider_root=root.as_posix() if isinstance(reconstructed.adapter, LocalEnvdProjectAdapter) else "/",
        )

    async def _prepare_host_files_mount(
        self,
        *,
        root: Path,
        alias: str,
        mount_path: str | None,
        file_actions: frozenset[EnvironmentAction] = FILE_ACTIONS,
    ) -> _PreparedMount:
        try:
            normalized = await to_thread.run_sync(_validate_content_plugin_root, root)
            provider = DIRECT_LOCAL
            configuration = provider.validate_environment(
                {
                    "root": {"path": os.fspath(normalized)},
                    "shell_profiles": [],
                    "allowed_executables": [],
                    "allowed_ports": [],
                    "allowed_environment_keys": [],
                },
            )
            environment = provider.execution_connector(
                environment_id=f"local-{hashlib.sha256(os.fsencode(normalized)).hexdigest()[:16]}",
                environment=configuration,
                state=None,
                runtime=None,
            )
        except Exception as exc:
            raise EnvironmentLifecycleError(
                "A Host file directory could not be prepared.",
                code="host_files_mount_failed",
                details={"mount": alias, "root": os.fspath(root)},
            ) from exc
        return _PreparedMount(
            alias=alias,
            environment=environment,
            permission_ceiling=EnvironmentPermissionSet(operations=file_actions),
            mount_path=mount_path,
        )

    async def _prepare_user_skills_mount(self, *, mount_path: str | None) -> _PreparedMount:
        root = self._user_skills_root or Path.home() / ".agents" / "skills"
        try:
            normalized = await to_thread.run_sync(_prepare_user_skills_root, root)
            provider = DIRECT_LOCAL
            configuration = provider.validate_environment(
                {
                    "root": {"path": os.fspath(normalized)},
                    "shell_profiles": [],
                    "allowed_executables": [],
                    "allowed_ports": [],
                    "allowed_environment_keys": [],
                },
            )
            environment = provider.execution_connector(
                environment_id=f"local-{hashlib.sha256(os.fsencode(normalized)).hexdigest()[:16]}",
                environment=configuration,
                state=None,
                runtime=None,
            )
        except Exception as exc:
            raise EnvironmentLifecycleError(
                "The user Skill directory could not be prepared.",
                code="user_skills_mount_failed",
                details={"root": os.fspath(root)},
            ) from exc
        return _PreparedMount(
            alias="user-skills",
            environment=environment,
            permission_ceiling=EnvironmentPermissionSet(operations=FILE_ACTIONS),
            mount_path=mount_path,
        )

    async def _publish_state(
        self,
        key: EnvironmentBindingKey,
        expected: ObjectRef | None,
        supplied: EnvironmentState | None,
        observed: EnvironmentState | None,
    ) -> EnvironmentStatePublication:
        if supplied == observed:
            return EnvironmentStatePublication(key, expected, expected, "unchanged")
        # Management has already taken effect. Finish the conditional publication even
        # when the caller is cancelled, while keeping storage failure bounded.
        replacement = None
        with CancelScope(shield=True), fail_after(30):
            if observed is not None:
                stored = StoredEnvironmentState(binding=key, state=observed, created_at=_utc_now())
                replacement = (
                    await self._store.objects.publish_model(
                        object_kind=ObjectKind.environment_state,
                        value=stored,
                    )
                ).ref
            await self._store.environment_states.select(key=key, expected=expected, replacement=replacement)
        return EnvironmentStatePublication(key, expected, replacement, "published")

    async def _load_state(
        self,
        *,
        key: EnvironmentBindingKey,
        reference: ObjectRef | None,
        provider_key: str,
    ) -> EnvironmentState | None:
        if reference is None:
            return None
        value = await self._store.objects.read_model(reference, StoredEnvironmentState)
        if value.binding != key or value.state.provider_key != provider_key:
            raise EnvironmentLifecycleError(
                "Stored Environment state is incompatible with its binding.",
                code="environment_state_incompatible",
                details={"adapter_key": key.adapter_key, "provider_key": provider_key},
            )
        return value.state


async def normalize_project_roots(roots: Sequence[Path | str]) -> tuple[Path, ...]:
    """Resolve and verify the ordered Project roots captured by a Run composition."""

    if not roots or len(roots) > 64:
        raise EnvironmentLifecycleError(
            "A Project must contain between one and 64 roots.",
            code="project_roots_invalid",
        )
    normalized: list[Path] = []
    seen: set[Path] = set()
    for value in roots:
        try:
            path = await to_thread.run_sync(_normalize_root, Path(value))
        except (OSError, ValueError) as exc:
            raise EnvironmentLifecycleError(
                "A Project root is missing, inaccessible, or not a directory.",
                code="project_root_invalid",
                details={"root": os.fspath(value)},
            ) from exc
        if path in seen:
            raise EnvironmentLifecycleError(
                "Project roots must be unique after normalization.",
                code="project_roots_invalid",
            )
        seen.add(path)
        normalized.append(path)
    return tuple(normalized)


def _validate_content_plugin_root(value: Path) -> Path:
    if "\x00" in os.fspath(value):
        raise ValueError("root contains NUL")
    metadata = value.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise ValueError("root must be a non-symlink directory")
    resolved = value.resolve(strict=True)
    if not os.access(resolved, os.R_OK | os.X_OK):
        raise ValueError("root must be readable")
    return resolved


def _prepare_user_skills_root(value: Path) -> Path:
    if "\x00" in os.fspath(value):
        raise ValueError("root contains NUL")
    value.mkdir(mode=0o700, parents=True, exist_ok=True)
    resolved = value.resolve(strict=True)
    if not resolved.is_dir() or not os.access(resolved, os.R_OK | os.W_OK | os.X_OK):
        raise OSError("user Skill root is not an accessible writable directory")
    return resolved


def _root_selects_skills(composition: ResolvedRunComposition) -> bool:
    return any(item.capability == "skills" for item in composition.root.capabilities)


def _normalize_root(value: Path) -> Path:
    raw = value.expanduser()
    if "\x00" in os.fspath(raw):
        raise ValueError("root contains NUL")
    resolved = raw.resolve(strict=True)
    if not resolved.is_dir() or not os.access(resolved, os.R_OK | os.X_OK):
        raise OSError("root is not an accessible directory")
    return resolved


def _utc_now() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "EnvironmentFinalization",
    "EnvironmentRunPlan",
    "EnvironmentRunService",
    "EnvironmentSnapshotReconstructor",
    "EnvironmentStatePublication",
    "ProviderRuntimeFactory",
    "ReconstructedEnvironmentProfile",
    "normalize_project_roots",
]
