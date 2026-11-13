"""Host-authoritative Project Environment preparation and state publication."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import os
import stat
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Mapping, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MappingProxyType
from typing import Literal
from weakref import WeakValueDictionary

from a13n_envd_client import EIPDeviceConnection
from a13n_harness import AgentIdentityRef, AgentInstanceContext
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
from a13n_harness.environment.execution import EnvironmentConnector
from a13n_harness.environment.models import (
    COMPUTER_ACTIONS,
    FILE_EXECUTION_ACTIONS,
    EnvironmentDescriptor,
    EnvironmentError,
    EnvironmentState,
)
from a13n_harness.environment.providers import BoundEnvironment, EnvironmentRuntime
from a13n_harness.environment.session_recovery import EnvdSessionBorrower, EnvdSessionState
from a13n_harness.providers.environment.definition import EnvironmentProviderDefinition
from a13n_harness.providers.environment.direct_local.provider import DIRECT_LOCAL
from a13n_harness.providers.environment.local_envd.configuration import local_envd_path
from a13n_harness.providers.environment.local_envd.runtime import LocalEnvdProviderRuntime
from anyio import CancelScope, fail_after, to_thread

from a13n_harness_ui.builtin_guidance import instruction_reference_root
from a13n_harness_ui.composition import ResolvedEnvironmentProfile, ResolvedRunComposition
from a13n_harness_ui.composition.models import ResolvedEnvironmentBinding, ResolvedEnvironmentTarget
from a13n_harness_ui.configuration.models import DeviceResource, HttpDeviceTransport, canonical_digest
from a13n_harness_ui.devices import DeviceConnections
from a13n_harness_ui.envd_runtime import EnvdRuntimeManager, EnvdStatus
from a13n_harness_ui.envd_sessions import AdmittedSession, SessionBinding
from a13n_harness_ui.environment_monitoring import MonitoringSource
from a13n_harness_ui.environment_paths import BUILTIN_SKILLS_PATH, BUILTIN_SKILLS_ROOT, EnvironmentPathLayout
from a13n_harness_ui.errors import CompositionError, EnvironmentLifecycleError, HarnessUiError
from a13n_harness_ui.extensions import (
    LOCAL_ENVD_PROVIDER_KEY,
    NATIVE_PROVIDER_KEY,
    EnvironmentProjectAdapter,
    HarnessUiExtensionCatalog,
)
from a13n_harness_ui.extensions.environment_adapters import (
    LocalEnvdProfileConfiguration,
    LocalEnvdProjectAdapter,
    NativeProjectAdapter,
    observed_binding_state,
)
from a13n_harness_ui.settings import EnvdRuntimeSettings
from a13n_harness_ui.storage import EnvironmentBindingKey, LocalStore, ObjectRef, StoredEnvironmentState
from a13n_harness_ui.storage.envd_sessions import SessionTarget
from a13n_harness_ui.storage.objects import ObjectKind
from a13n_harness_ui.storage.ownership import OwnershipLocks
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

    @property
    def provider_key(self) -> str:
        return self.environment.provider_key

    @property
    def environment_id(self) -> str:
        return self.environment.environment_id

    @property
    def descriptor(self) -> EnvironmentDescriptor:
        return self.environment.descriptor

    @property
    def state(self) -> EnvironmentState | None:
        return self.environment.state

    async def ensure_ready(self) -> EnvironmentConnector:
        # Project preparation and state publication already completed in this Host.
        return self.environment


class EnvironmentSnapshotReconstructor:
    """Resolve current trusted Provider/adapter implementations for a composition."""

    def __init__(
        self,
        *,
        catalog: HarnessUiExtensionCatalog | None = None,
        envd_settings: EnvdRuntimeSettings | None = None,
        envd: EnvdRuntimeManager | None = None,
        runtime_factories: Mapping[str, ProviderRuntimeFactory] | None = None,
        local_runtime_parent: Path | None = None,
        protected_roots: tuple[Path, ...] = (),
    ) -> None:
        self._catalog = catalog or HarnessUiExtensionCatalog()
        self._runtime_factories = MappingProxyType(dict(runtime_factories or {}))
        self.envd = envd or EnvdRuntimeManager(
            settings=envd_settings or EnvdRuntimeSettings(),
            runtime_parent=local_runtime_parent,
            protected_roots=protected_roots,
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
        session_recovery: EnvdSessionBorrower | None = None,
    ) -> EnvironmentConnector:
        collaborator = (
            local_runtime or await self.sandbox_runtime(profile=reconstructed.profile)
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
                session_recovery=session_recovery,
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

    async def envd_status(self) -> EnvdStatus:
        return await self.envd.status()

    async def resolve_sandbox_executable(self) -> Path:
        return await self.envd.resolve_executable()

    async def sandbox_runtime(
        self,
        *,
        profile: ResolvedEnvironmentProfile | None = None,
    ) -> LocalEnvdProviderRuntime:
        return await self.envd.runtime(profile=profile)

    def monitoring_sources(self) -> Iterable[MonitoringSource]:
        return self.envd.monitoring_sources()

    async def close(self) -> None:
        await self.envd.close()

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


def _local_actions(profile: ResolvedEnvironmentProfile) -> frozenset[EnvironmentAction]:
    if profile.provider_key == LOCAL_ENVD_PROVIDER_KEY:
        selected = LocalEnvdProfileConfiguration.model_validate(profile.provider_configuration)
        if selected.launch.computer_use:
            return FILE_EXECUTION_ACTIONS | COMPUTER_ACTIONS
    return FILE_EXECUTION_ACTIONS


class EnvironmentRunPlan:
    """Inert connectors and the outcomes already published during Host preparation."""

    def __init__(
        self,
        *,
        publications: Sequence[EnvironmentStatePublication],
        path_layout: EnvironmentPathLayout,
        profile: ResolvedEnvironmentProfile,
        mounts: Sequence[_PreparedMount],
        runtime: EnvironmentRuntime,
        tool_result_directory: str,
        retained_history_directory: str,
        default_environment: str,
        instruction_reference_root: str | None = None,
    ) -> None:
        self._publications = tuple(publications)
        self.path_layout = path_layout
        self.profile = profile
        self._mounts = tuple(mounts)
        self.runtime = runtime
        self.tool_result_directory = tool_result_directory
        self.retained_history_directory = retained_history_directory
        self.environments: Mapping[str, EnvironmentConnector] = MappingProxyType(
            {item.alias: item.environment for item in mounts}
        )
        self.default_environment = default_environment
        self.instruction_reference_root = instruction_reference_root

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
        self._sessions: WeakValueDictionary[str, SessionBinding] = WeakValueDictionary()
        self._session_lock = asyncio.Lock()
        self._owners = OwnershipLocks(store.layout.root / "envd-session-locks")

    async def close(self) -> None:
        """Release binding ownership after all Run and retained scopes have joined."""
        self._sessions.clear()
        await self._owners.close()

    async def admit(self, composition: ResolvedRunComposition) -> ResolvedRunComposition:
        """Freeze Session selections before scheduling, without opening Device connections."""
        profile = composition.environment_profile
        if profile.adapter_key == LocalEnvdProjectAdapter.key:
            roots = await normalize_project_roots(composition.project_roots) if composition.project_roots else ()
            thread_root = await self._thread_files.touch(composition.thread_id)
            profile = await self._reconstructor.envd.session_profile(profile, roots, thread_files_root=thread_root)
            composition = composition.model_copy(
                update={
                    "environment_profile": profile,
                    "project_roots": tuple(str(root) for root in roots),
                }
            )
        digests: dict[str, str] = {}
        if profile.adapter_key == LocalEnvdProjectAdapter.key:
            digests["local"] = canonical_digest(profile.provider_configuration)
        for binding in composition.environment_bindings:
            key = binding.device.id
            digest = canonical_digest(
                {
                    "device": binding.device.model_dump(mode="json", exclude={"name", "id"}),
                    "session": binding.selection.session.model_dump(mode="json"),
                    "expected_boundary": None
                    if binding.selection.expected_boundary is None
                    else binding.selection.expected_boundary.model_dump(mode="json"),
                }
            )
            if key in digests and digests[key] != digest:
                raise EnvironmentLifecycleError(
                    "Directory views of one Device must select the same Session configuration.",
                    code="envd_session_conflict",
                )
            digests[key] = digest
        versions: dict[str, int] = {}
        previous = await self._store.envd_sessions.list(composition.thread_id)
        prefix = _session_prefix(composition)
        previous = (
            {key: value for key, value in previous.items() if key.startswith(prefix)}
            if prefix
            else {key: value for key, value in previous.items() if not key.startswith("bot/")}
        )
        digests = {f"{prefix}{key}": value for key, value in digests.items()}
        async with AsyncExitStack() as scope:
            changes = {}
            for key in sorted(previous.keys() | digests.keys()):
                owner = await self._session_owner(composition.thread_id, key)
                changes[key] = await scope.enter_async_context(owner.selection(digests.get(key)))
                if key in digests:
                    versions[key] = changes[key][1].version
            with CancelScope(shield=True), fail_after(30):
                await self._store.envd_sessions.select_many(
                    composition.thread_id, changes, complete=composition.root.source_kind != "bot"
                )
        composition = composition.model_copy(update={"session_versions": versions})
        targets = []
        for target in composition.environment_targets:
            try:
                admitted = await self.admit(_target_composition(composition, target))
                target = target.model_copy(
                    update={
                        "project_roots": admitted.project_roots,
                        "environment_profile": admitted.environment_profile,
                        "session_versions": admitted.session_versions,
                    }
                )
            except (HarnessUiError, EnvironmentError, OSError) as error:
                # One unavailable Project must not prevent Bot from using other targets.
                target = target.model_copy(update={"unavailable": str(error)})
            targets.append(target)
        return composition.model_copy(update={"environment_targets": tuple(targets)})

    async def validate_admission(self, composition: ResolvedRunComposition) -> None:
        expected = {binding.device.id for binding in composition.environment_bindings}
        if composition.environment_profile.adapter_key == LocalEnvdProjectAdapter.key:
            expected.add("local")
        expected = {f"{_session_prefix(composition)}{key}" for key in expected}
        if expected != composition.session_versions.keys():
            raise EnvironmentLifecycleError("Session selection was not admitted.", code="envd_session_conflict")
        for key, version in composition.session_versions.items():
            await (await self._session_owner(composition.thread_id, key)).validate(version)

    async def prepare(self, composition: ResolvedRunComposition) -> EnvironmentRunPlan:
        await self.validate_admission(composition)
        thread_root = await self._thread_files.touch(composition.thread_id)
        return await self._assemble(composition, thread_root=thread_root, preview=False)

    @asynccontextmanager
    async def preview(
        self, composition: ResolvedRunComposition
    ) -> AsyncIterator[tuple[BoundEnvironment, EnvironmentPathLayout]]:
        """Bind an ephemeral Environment without admitting or publishing a Run."""
        with TemporaryDirectory(prefix="a13n-skill-preview-") as directory:
            thread_root = Path(directory)
            (thread_root / "tmp").mkdir()
            profile = composition.environment_profile
            if profile.adapter_key == LocalEnvdProjectAdapter.key:
                roots = await normalize_project_roots(composition.project_roots) if composition.project_roots else ()
                profile = await self._reconstructor.envd.session_profile(profile, roots, thread_files_root=thread_root)
                composition = composition.model_copy(update={"environment_profile": profile})
            plan = await self._assemble(composition, thread_root=thread_root, preview=True)
            async with plan.runtime.bind(
                thread_id=composition.thread_id,
                run_id=f"skill-preview-{thread_root.name}",
                instance=AgentInstanceContext(
                    identity=AgentIdentityRef(issuer="harness-ui", subject="skill-preview"),
                    agent_instance_id="skill-preview",
                ),
                host_refs={},
            ) as environment:
                await plan.runtime._activate()
                yield environment, plan.path_layout

    async def _assemble(
        self, composition: ResolvedRunComposition, *, thread_root: Path, preview: bool
    ) -> EnvironmentRunPlan:
        live = composition.root.source_kind == "live"
        profile = composition.environment_profile
        reconstructed = self._reconstructor.reconstruct(profile)
        roots = await normalize_project_roots(composition.project_roots) if composition.project_roots else ()
        canonical_host_paths = reconstructed.adapter.preserves_host_paths
        content_plugins = tuple((item.plugin_id, item.path, item.skills_path) for item in composition.content_plugins)
        path_layout = EnvironmentPathLayout.resolve(
            canonical_host_paths=canonical_host_paths,
            project_roots=roots,
            device_bindings=tuple(
                (item.selection.alias, item.selection.working_directory) for item in composition.environment_bindings
            ),
            user_skills_root=self._user_skills_root,
            content_plugins=content_plugins,
        )
        local_runtime = (
            await self._reconstructor.sandbox_runtime(profile=profile)
            if isinstance(reconstructed.adapter, LocalEnvdProjectAdapter)
            else None
        )
        mounts: list[_PreparedMount] = []
        publications: list[EnvironmentStatePublication] = []
        for index, root in enumerate(roots, start=1):
            mount, publication = await self._prepare_project_mount(
                composition,
                reconstructed,
                root=root,
                alias="workspace" if index == 1 else f"workspace-{index}",
                mount_path=path_layout.project_mounts[index - 1] if canonical_host_paths else None,
                local_runtime=local_runtime,
                persistent=not preview,
            )
            mounts.append(mount)
            if publication is not None:
                publications.append(publication)
        for binding in composition.environment_bindings:
            if isinstance(binding.device.transport, HttpDeviceTransport):
                composition.run_configuration.authorize_url(binding.device.transport.configuration.endpoint)
            mount, publication = await self._prepare_device_mount(composition, binding, persistent=not preview)
            mounts.append(mount)
            if publication is not None:
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
                user_mount = await self._prepare_user_skills_mount(
                    mount_path=(path_layout.user_skills if canonical_host_paths else None), create=not preview
                )
                if user_mount is not None:
                    mounts.append(user_mount)
        if self._configuration_root is not None and not live:
            root = self._configuration_root.expanduser().resolve()
            if not (canonical_host_paths and any(mount.mount_path == root.as_posix() for mount in mounts)):
                mounts.append(
                    await self._prepare_host_files_mount(
                        root=root,
                        alias="configuration",
                        mount_path=root.as_posix() if canonical_host_paths else None,
                    )
                )
        thread_mount = await self._prepare_thread_files_mount(
            reconstructed,
            thread_root,
            local_runtime=local_runtime,
            session_recovery=await self._session_context(composition, "local", local_runtime.configuration)
            if local_runtime is not None and not preview
            else None,
        )
        if live:
            thread_mount.permission_ceiling = EnvironmentPermissionSet(operations=FILE_ACTIONS)
        if roots:
            mounts.append(thread_mount)
        else:
            mounts.insert(0, thread_mount)
        extensions = await self._reconstructor.create_extensions(composition)
        runtime = create_environment_runtime(
            mounts={
                item.alias: EnvironmentMount(
                    source=item,
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
            instruction_reference_root=instruction_reference_root(composition.root),
            publications=publications,
            path_layout=path_layout,
            profile=profile,
            mounts=mounts,
            runtime=runtime,
            default_environment=composition.default_environment or mounts[0].alias,
            tool_result_directory=(
                f"{(thread_mount.mount_path or '/environment/thread-files').rstrip('/')}/tmp/tool-results"
            ),
            retained_history_directory=(
                f"{(thread_mount.mount_path or '/environment/thread-files').rstrip('/')}/history"
            ),
        )

    async def mount_target(
        self,
        composition: ResolvedRunComposition,
        target: ResolvedEnvironmentTarget,
        runtime: EnvironmentRuntime,
        *,
        existing_aliases: set[str],
    ) -> None:
        """Add captured Project mounts to Bot's active native runtime on demand."""
        if target.unavailable is not None:
            raise EnvironmentLifecycleError(target.unavailable, code="bot_environment_unavailable")
        selected = _target_composition(composition, target)
        await self.validate_admission(selected)
        aliases = target_mount_aliases(target)
        pending = {local: alias for local, alias in aliases.items() if alias not in existing_aliases}
        if not pending:
            return
        reconstructed = self._reconstructor.reconstruct(selected.environment_profile)
        roots = await normalize_project_roots(selected.project_roots) if selected.project_roots else ()
        local_runtime = (
            await self._reconstructor.sandbox_runtime(profile=selected.environment_profile)
            if roots and isinstance(reconstructed.adapter, LocalEnvdProjectAdapter)
            else None
        )
        prepared = []
        for index, root in enumerate(roots, start=1):
            local = "workspace" if index == 1 else f"workspace-{index}"
            if local in pending:
                mount, _ = await self._prepare_project_mount(
                    selected,
                    reconstructed,
                    root=root,
                    alias=pending[local],
                    mount_path=f"/environment/{pending[local]}",
                    local_runtime=local_runtime,
                )
                prepared.append(mount)
        for binding in selected.environment_bindings:
            local = binding.selection.alias
            if local not in pending:
                continue
            if isinstance(binding.device.transport, HttpDeviceTransport):
                composition.run_configuration.authorize_url(binding.device.transport.configuration.endpoint)
            mount, _ = await self._prepare_device_mount(selected, binding)
            mount.alias = pending[local]
            mount.mount_path = f"/environment/{mount.alias}"
            prepared.append(mount)
        for mount in prepared:
            # Each native mutation publishes atomically. A retry reads the live
            # snapshot and skips mounts already published by a cancelled call.
            await runtime.mount(
                mount.alias,
                EnvironmentMount(
                    source=mount,
                    permission_ceiling=mount.permission_ceiling,
                    mount_path=mount.mount_path,
                    provider_root=mount.provider_root,
                ),
            )

    async def prepare_recovery(
        self,
        composition: ResolvedRunComposition,
        alias: str,
        *,
        original_session_id: str,
        _target_alias: str | None = None,
    ) -> EnvironmentRuntime:
        """Reconstruct only a retained Shell's captured mount, without admitting a Run."""
        for target in composition.environment_targets:
            selected_alias = next(
                (local for local, mounted in target_mount_aliases(target).items() if mounted == alias), None
            )
            if selected_alias is not None:
                return await self.prepare_recovery(
                    _target_composition(composition, target),
                    selected_alias,
                    original_session_id=original_session_id,
                    _target_alias=alias,
                )
        binding = next((item for item in composition.environment_bindings if item.selection.alias == alias), None)
        if binding is not None:
            if isinstance(binding.device.transport, HttpDeviceTransport):
                composition.run_configuration.authorize_url(binding.device.transport.configuration.endpoint)
            mount, _ = await self._prepare_device_mount(composition, binding, original_session_id=original_session_id)
        else:
            roots = {
                "workspace" if index == 1 else f"workspace-{index}": Path(root)
                for index, root in enumerate(composition.project_roots, start=1)
            }
            thread_root = self._thread_files.directory(composition.thread_id)
            roots["thread-files"] = thread_root
            if alias not in roots:
                raise EnvironmentLifecycleError("The captured Shell mount is unavailable.", code="shell_mount_missing")
            root = (await normalize_project_roots((roots[alias],)))[0]
            profile = composition.environment_profile
            reconstructed = self._reconstructor.reconstruct(profile)
            local_runtime = (
                await self._reconstructor.sandbox_runtime(profile=profile)
                if isinstance(reconstructed.adapter, LocalEnvdProjectAdapter)
                else None
            )
            if alias == "thread-files":
                profile = composition.environment_profile
                mount = await self._prepare_thread_files_mount(
                    reconstructed,
                    root,
                    local_runtime=local_runtime,
                    session_recovery=await self._session_context(
                        composition,
                        "local",
                        local_runtime.configuration,
                        original_session_id=original_session_id,
                    )
                    if local_runtime is not None
                    else None,
                )
                if composition.root.source_kind == "live":
                    mount.permission_ceiling = EnvironmentPermissionSet(operations=FILE_ACTIONS)
            else:
                mount, _ = await self._prepare_project_mount(
                    composition,
                    reconstructed,
                    root=root,
                    alias=alias,
                    mount_path=root.as_posix() if reconstructed.adapter.preserves_host_paths else None,
                    local_runtime=local_runtime,
                    original_session_id=original_session_id,
                )
        if _target_alias is not None:
            alias = _target_alias
            mount.mount_path = f"/environment/{alias}"
        return create_environment_runtime(
            mounts={
                alias: EnvironmentMount(
                    source=mount,
                    permission_ceiling=mount.permission_ceiling,
                    mount_path=mount.mount_path,
                    provider_root=mount.provider_root,
                )
            },
            default_mount=alias,
        )

    async def _prepare_project_mount(
        self,
        composition: ResolvedRunComposition,
        reconstructed: ReconstructedEnvironmentProfile,
        *,
        root: Path,
        alias: str,
        mount_path: str | None,
        local_runtime: LocalEnvdProviderRuntime | None,
        original_session_id: str | None = None,
        persistent: bool = True,
    ) -> tuple[_PreparedMount, EnvironmentStatePublication | None]:
        profile = reconstructed.profile
        key = EnvironmentBindingKey(
            thread_id=composition.thread_id,
            environment_profile_id=profile.profile_id,
            profile_digest=(
                canonical_digest({"profile": profile.behavior_digest, "scope": composition.environment_scope})
                if composition.environment_scope is not None
                else profile.behavior_digest
            ),
            adapter_key=profile.adapter_key,
            normalized_root=os.fspath(root),
        )
        recovery = (
            await self._session_context(
                composition, "local", local_runtime.configuration, original_session_id=original_session_id
            )
            if local_runtime is not None and persistent
            else None
        )
        head = await self._store.environment_states.get(key) if persistent else None
        expected = None if head is None else head.state
        state = (
            await self._load_state(key=key, reference=expected, provider_key=profile.provider_key)
            if persistent
            else None
        )
        try:
            environment = await self._reconstructor.bind(
                reconstructed,
                root=root,
                state=state,
                local_runtime=local_runtime,
                session_recovery=recovery,
            )
        except BaseException as error:
            if persistent:
                with CancelScope(shield=True):
                    try:
                        await self._publish_state(key, expected, state, observed_binding_state(error, state))
                    except Exception as publication_error:
                        error.add_note(f"Environment state publication also failed: {publication_error!r}")
            raise
        publication = await self._publish_state(key, expected, state, environment.state) if persistent else None
        return _PreparedMount(
            alias=alias,
            environment=environment,
            permission_ceiling=EnvironmentPermissionSet(
                operations=FILE_ACTIONS if composition.root.source_kind == "live" else _local_actions(profile)
            ),
            mount_path=mount_path,
            provider_root=local_envd_path(root) if isinstance(reconstructed.adapter, LocalEnvdProjectAdapter) else "/",
        ), publication

    async def _prepare_device_mount(
        self,
        composition: ResolvedRunComposition,
        binding: ResolvedEnvironmentBinding,
        *,
        original_session_id: str | None = None,
        persistent: bool = True,
    ) -> tuple[_PreparedMount, EnvironmentStatePublication | None]:
        if self._devices is None:
            raise EnvironmentLifecycleError(
                "Device connections are unavailable.", code="device_connections_unavailable"
            )
        http = isinstance(binding.device.transport, HttpDeviceTransport)
        provider_key = "http_envd" if http else "websocket_envd"
        adapter_key = provider_key
        selection = binding.selection
        key = EnvironmentBindingKey(
            thread_id=composition.thread_id,
            environment_profile_id="",
            device_id=binding.device.id,
            alias=selection.alias,
            profile_digest=canonical_digest(
                {
                    "device": binding.device.model_dump(mode="json", exclude={"name", "id"}),
                    "selection": selection.model_dump(mode="json"),
                    **({"scope": composition.environment_scope} if composition.environment_scope is not None else {}),
                }
            ),
            adapter_key=adapter_key,
            normalized_root=selection.working_directory,
        )
        recovery = (
            await self._session_context(
                composition,
                binding.device.id,
                binding.device,
                original_session_id=original_session_id,
            )
            if persistent
            else None
        )
        head = await self._store.environment_states.get(key) if persistent else None
        expected = None if head is None else head.state
        state = await self._load_state(key=key, reference=expected, provider_key=provider_key) if persistent else None
        environment = await self._devices.bind(
            binding,
            environment_id=f"device-{key.profile_digest[:20]}",
            state=state,
            session_recovery=recovery,
        )
        publication = await self._publish_state(key, expected, state, environment.state) if persistent else None
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
        session_recovery: EnvdSessionBorrower | None = None,
    ) -> _PreparedMount:
        if isinstance(reconstructed.adapter, (NativeProjectAdapter, LocalEnvdProjectAdapter)):
            environment = await self._reconstructor.bind(
                reconstructed,
                root=root,
                state=None,
                local_runtime=local_runtime,
                session_recovery=session_recovery,
            )
            operations = _local_actions(reconstructed.profile)
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
            provider_root=local_envd_path(root) if isinstance(reconstructed.adapter, LocalEnvdProjectAdapter) else "/",
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

    async def _prepare_user_skills_mount(self, *, mount_path: str | None, create: bool = True) -> _PreparedMount | None:
        root = self._user_skills_root or Path.home() / ".agents" / "skills"
        if not create and not await to_thread.run_sync(root.exists):
            return None
        try:
            normalized = await to_thread.run_sync(_prepare_user_skills_root, root, create)
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

    async def _session_owner(self, thread_id: str, device_key: str) -> SessionBinding:
        identity = f"{thread_id}:{device_key}"
        async with self._session_lock:
            existing = self._sessions.get(identity)
            if existing is not None:
                return existing

            async def require_idle(exclude_execution_id: str | None) -> None:
                # Nonterminal records sort first; two suffice when excluding one caller.
                records = await self._store.tool_executions.list(thread_id, offset=0, limit=2)
                if any(not record.terminal and record.execution_id != exclude_execution_id for record in records):
                    raise EnvironmentError(
                        "Retained tools must finish before changing the Session configuration.", code="environment_busy"
                    )

            owner = SessionBinding(
                thread_id=thread_id,
                device_key=device_key,
                repository=self._store.envd_sessions,
                owners=self._owners,
                require_idle=require_idle,
                retirement_device=self._retirement_device,
            )
            self._sessions[identity] = owner
            return owner

    async def _retirement_device(
        self,
        target: SessionTarget,
        previous: EnvdSessionState,
    ) -> EIPDeviceConnection | None:
        if isinstance(target, DeviceResource):
            if self._devices is None:
                raise EnvironmentLifecycleError(
                    "Device connections are unavailable.", code="device_connections_unavailable"
                )
            return await self._devices.acquire_device(target)
        device = self._reconstructor.envd.running_device(target)
        return device if device is not None and device.descriptor.device_id == previous.descriptor.device_id else None

    async def _session_context(
        self,
        composition: ResolvedRunComposition,
        device_key: str,
        recipe: SessionTarget,
        *,
        original_session_id: str | None = None,
    ) -> EnvdSessionBorrower:
        device_key = f"{_session_prefix(composition)}{device_key}"
        version = composition.session_versions.get(device_key)
        if version is None:
            raise EnvironmentLifecycleError("Session selection was not admitted.", code="envd_session_conflict")
        owner = await self._session_owner(composition.thread_id, device_key)
        await owner.validate(version)
        return AdmittedSession(owner, version, recipe, original_session_id)

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
        if value.binding != key or (value.state is not None and value.state.provider_key != provider_key):
            raise EnvironmentLifecycleError(
                "Stored Environment state is incompatible with its binding.",
                code="environment_state_incompatible",
                details={"adapter_key": key.adapter_key, "provider_key": provider_key},
            )
        return value.state


def _session_prefix(composition: ResolvedRunComposition) -> str:
    scope = composition.environment_scope
    return "" if scope is None else f"bot/{hashlib.sha256(scope.encode()).hexdigest()[:20]}/"


def _target_composition(
    composition: ResolvedRunComposition, target: ResolvedEnvironmentTarget
) -> ResolvedRunComposition:
    return composition.model_copy(
        update={
            "project_id": target.project_id,
            "project_roots": target.project_roots,
            "environment_profile": target.environment_profile,
            "environment_bindings": target.environment_bindings,
            "default_environment": target.default_environment,
            "environment_scope": target.target_id,
            "environment_targets": (),
            "session_versions": target.session_versions,
        }
    )


def target_mount_aliases(target: ResolvedEnvironmentTarget) -> dict[str, str]:
    """Map captured local aliases to stable, collision-free Bot mount routes."""
    local = ["workspace" if index == 1 else f"workspace-{index}" for index in range(1, len(target.project_roots) + 1)]
    local.extend(binding.selection.alias for binding in target.environment_bindings)
    prefix = hashlib.sha256(target.target_id.encode()).hexdigest()[:20]
    return {alias: f"bot-{prefix}-{index}" for index, alias in enumerate(local, start=1)}


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


def _prepare_user_skills_root(value: Path, create: bool = True) -> Path:
    if "\x00" in os.fspath(value):
        raise ValueError("root contains NUL")
    if create:
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
