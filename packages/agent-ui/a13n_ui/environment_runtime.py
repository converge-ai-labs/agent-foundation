"""Host-authoritative Project Environment preparation and state publication."""

from __future__ import annotations

import hashlib
import inspect
import os
import stat
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from types import MappingProxyType
from typing import Literal

from a13n_environment_provider import (
    DirectLocalEnvironmentProvider,
    DirectLocalProviderRuntime,
    Environment,
    EnvironmentProvider,
    EnvironmentState,
    LocalEnvdProviderRuntime,
    TemporaryLocalEnvdRuntimeAllocator,
    resolve_agent_envd_executable,
)
from a13n_harness.environment import (
    EnvironmentAction,
    EnvironmentPermissionSet,
    EnvironmentRunExtension,
    EnvironmentRunExtensionFactoryContext,
)
from a13n_harness.environment.advanced import create_environment_runtime
from a13n_harness.environment.providers import (
    BoundEnvironmentProvider,
    EnvironmentProviderBinding,
    EnvironmentRuntime,
    EnvironmentRuntimeMount,
)
from a13n_harness.identity import AgentInstanceContext
from anyio import CancelScope, move_on_after, to_thread

from a13n_ui.composition import ResolvedEnvironmentProfile, ResolvedRunComposition
from a13n_ui.environment_paths import EnvironmentPathLayout
from a13n_ui.errors import CompositionError, EnvironmentLifecycleError, StoreError
from a13n_ui.extensions import (
    LOCAL_ENVD_PROVIDER_KEY,
    NATIVE_PROVIDER_KEY,
    AgentUiExtensionCatalog,
    EnvironmentProjectAdapter,
)
from a13n_ui.managed_runtime import ManagedEnvdRuntime
from a13n_ui.settings import EnvdRuntimeSettings
from a13n_ui.storage import EnvironmentBindingKey, LocalStore, ObjectRef, StoredEnvironmentState
from a13n_ui.storage.objects import ObjectKind

type ProviderRuntimeFactory = Callable[[EnvironmentProvider], object | Awaitable[object | None] | None]


@dataclass(frozen=True, slots=True)
class ReconstructedEnvironmentProfile:
    """A resolved profile paired with current trusted process-local implementations."""

    profile: ResolvedEnvironmentProfile
    adapter: EnvironmentProjectAdapter
    provider: EnvironmentProvider


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
    key: EnvironmentBindingKey | None
    expected_state_ref: ObjectRef | None
    supplied_state: EnvironmentState | None
    environment: Environment
    permission_ceiling: EnvironmentPermissionSet
    mount_path: str | None


class _EnvironmentBinding(EnvironmentProviderBinding):
    """Transfer one fresh Provider Environment into a Harness runtime."""

    def __init__(self, environment: Environment) -> None:
        self._environment = environment
        self._used = False
        self._discarded = False

    @property
    def provider_type(self) -> str:
        return self._environment.provider_key

    @property
    def environment_id(self) -> str:
        return self._environment.environment_id

    @asynccontextmanager
    async def bind(
        self,
        *,
        thread_id: str,
        run_id: str,
        instance: AgentInstanceContext,
        mount_id: str,
        host_refs: Mapping[str, str],
    ) -> AsyncIterator[BoundEnvironmentProvider]:
        if self._used or self._discarded:
            raise EnvironmentLifecycleError(
                "An Environment binding can be used exactly once.",
                code="environment_binding_reused",
            )
        self._used = True
        try:
            await self._environment.enter(
                thread_id=thread_id,
                run_id=run_id,
                agent_instance_id=instance.agent_instance_id,
                mount_id=mount_id,
                host_refs=host_refs,
            )
            yield self._environment
        finally:
            await self._environment.close()

    async def discard(self) -> None:
        self._discarded = True
        await self._environment.close()


class EnvironmentSnapshotReconstructor:
    """Resolve current trusted Provider/adapter implementations for a composition."""

    def __init__(
        self,
        *,
        catalog: AgentUiExtensionCatalog | None = None,
        envd_settings: EnvdRuntimeSettings | None = None,
        runtime_factories: Mapping[str, ProviderRuntimeFactory] | None = None,
        local_runtime_parent: Path | None = None,
    ) -> None:
        self._catalog = catalog or AgentUiExtensionCatalog()
        self._envd_settings = envd_settings or EnvdRuntimeSettings()
        self._runtime_factories = MappingProxyType(dict(runtime_factories or {}))
        self._local_runtime_parent = local_runtime_parent
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
            provider_schema_version=profile.provider_schema_version,
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
    ) -> Environment:
        collaborator = await self._runtime_collaborator(reconstructed.provider)
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
        if not isinstance(environment, Environment) or environment.provider_key != reconstructed.profile.provider_key:
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

    async def _runtime_collaborator(self, provider: EnvironmentProvider) -> object | None:
        if provider.key == NATIVE_PROVIDER_KEY:
            return DirectLocalProviderRuntime()
        if provider.key == LOCAL_ENVD_PROVIDER_KEY:
            executable = self._envd_settings.executable
            if executable is None:
                if self._managed_envd is None:
                    raise EnvironmentLifecycleError(
                        "Local Envd requires the Agent UI runtime cache or an executable override.",
                        code="local_envd_runtime_unavailable",
                    )
                resolved = await self._managed_envd.resolve()
            else:
                resolved = await to_thread.run_sync(partial(resolve_agent_envd_executable, executable))
            return LocalEnvdProviderRuntime(
                executable=resolved,
                allocate_private_runtime=TemporaryLocalEnvdRuntimeAllocator(parent=self._local_runtime_parent),
            )
        factory = self._runtime_factories.get(provider.key)
        if factory is None:
            raise EnvironmentLifecycleError(
                "No trusted runtime factory is registered for the selected Environment Provider.",
                code="provider_runtime_unsupported",
                details={"provider_key": provider.key},
            )
        value = factory(provider)
        return await value if inspect.isawaitable(value) else value


class EnvironmentRunPlan:
    """Single-use Harness Environment runtime plus later Host-state publication."""

    def __init__(
        self,
        *,
        store: LocalStore,
        profile: ResolvedEnvironmentProfile,
        mounts: Sequence[_PreparedMount],
        runtime: EnvironmentRuntime,
    ) -> None:
        self._store = store
        self.profile = profile
        self._mounts = tuple(mounts)
        self.runtime = runtime
        self.environments: Mapping[str, Environment] = MappingProxyType(
            {item.alias: item.environment for item in mounts}
        )
        self.default_environment = mounts[0].alias
        self._finalized = False

    async def finalize(self, *, timeout_seconds: float = 30.0) -> EnvironmentFinalization:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self._finalized:
            raise EnvironmentLifecycleError(
                "An Environment Run plan can be finalized exactly once.",
                code="environment_plan_finalized",
            )
        self._finalized = True
        cleanup_errors: list[Exception] = []
        with move_on_after(timeout_seconds) as cleanup_scope:
            for mount in reversed(self._mounts):
                try:
                    await mount.environment.close()
                except Exception as exc:
                    cleanup_errors.append(exc)
        if cleanup_scope.cancel_called:
            cleanup_errors.append(
                EnvironmentLifecycleError(
                    "Environment cleanup exceeded its deadline.",
                    code="environment_cleanup_timeout",
                )
            )

        publications: list[EnvironmentStatePublication] = []
        for mount in self._mounts:
            if mount.key is None:
                continue
            try:
                final_state = mount.environment.dump_state()
            except Exception as exc:
                publications.append(
                    EnvironmentStatePublication(
                        key=mount.key,
                        previous=mount.expected_state_ref,
                        replacement=None,
                        status="failed",
                        error=exc,
                    )
                )
                continue
            if final_state == mount.supplied_state:
                publications.append(
                    EnvironmentStatePublication(
                        key=mount.key,
                        previous=mount.expected_state_ref,
                        replacement=mount.expected_state_ref,
                        status="unchanged",
                    )
                )
                continue
            replacement: ObjectRef | None = None
            try:
                if final_state is not None:
                    stored = StoredEnvironmentState(
                        binding=mount.key,
                        provider_schema_version=self.profile.provider_schema_version,
                        state=final_state,
                        created_at=_utc_now(),
                    )
                    replacement = (
                        await self._store.objects.publish_model(
                            object_kind=ObjectKind.environment_state,
                            value=stored,
                        )
                    ).ref
                await self._store.environment_states.select(
                    key=mount.key,
                    expected=mount.expected_state_ref,
                    replacement=replacement,
                )
            except Exception as exc:
                publications.append(
                    EnvironmentStatePublication(
                        key=mount.key,
                        previous=mount.expected_state_ref,
                        replacement=replacement,
                        status="failed",
                        error=exc,
                    )
                )
            else:
                publications.append(
                    EnvironmentStatePublication(
                        key=mount.key,
                        previous=mount.expected_state_ref,
                        replacement=replacement,
                        status="published",
                    )
                )
        return EnvironmentFinalization(
            cleanup_errors=tuple(cleanup_errors),
            state_publications=tuple(publications),
        )


class EnvironmentRunService:
    """Prepare Project roots from an admitted immutable Run composition."""

    def __init__(
        self,
        store: LocalStore,
        reconstructor: EnvironmentSnapshotReconstructor,
        *,
        user_skills_root: Path | None = None,
    ) -> None:
        self._store = store
        self._reconstructor = reconstructor
        self._user_skills_root = user_skills_root

    async def prepare(self, composition: ResolvedRunComposition) -> EnvironmentRunPlan:
        profile = composition.environment_profile
        reconstructed = self._reconstructor.reconstruct(profile)
        roots = await normalize_project_roots(composition.project_roots)
        canonical_host_paths = reconstructed.adapter.preserves_host_paths
        content_plugin_skills = tuple(
            (item.plugin_id, item.skills_path) for item in composition.content_plugins if item.skills_path is not None
        )
        path_layout = EnvironmentPathLayout.resolve(
            canonical_host_paths=canonical_host_paths,
            project_roots=roots,
            user_skills_root=self._user_skills_root,
            content_plugin_skills=content_plugin_skills,
        )
        mounts: list[_PreparedMount] = []
        try:
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
                state = await self._load_state(key=key, reference=expected, profile=profile)
                environment = await self._reconstructor.bind(
                    reconstructed,
                    root=root,
                    state=state,
                )
                mounts.append(
                    _PreparedMount(
                        alias="workspace" if index == 1 else f"workspace-{index}",
                        key=key,
                        expected_state_ref=expected,
                        supplied_state=state,
                        environment=environment,
                        permission_ceiling=EnvironmentPermissionSet(operations=frozenset(EnvironmentAction)),
                        mount_path=(path_layout.project_mounts[index - 1] if canonical_host_paths else None),
                    )
                )
            if _root_selects_skills(composition):
                for index, ((plugin_id, root), (_layout_id, mount_path)) in enumerate(
                    zip(content_plugin_skills, path_layout.content_plugin_skills, strict=True),
                    start=1,
                ):
                    if canonical_host_paths and Path(root) in roots:
                        continue
                    mounts.append(
                        await self._prepare_content_plugin_skills_mount(
                            plugin_id=plugin_id,
                            root=Path(root),
                            alias=f"content-plugin-{index}",
                            mount_path=mount_path if canonical_host_paths else None,
                        )
                    )
                if not (canonical_host_paths and Path(path_layout.user_skills) in roots):
                    mounts.append(
                        await self._prepare_user_skills_mount(
                            mount_path=(path_layout.user_skills if canonical_host_paths else None)
                        )
                    )
            extensions = await self._reconstructor.create_extensions(composition)
            runtime = create_environment_runtime(
                mounts={
                    item.alias: EnvironmentRuntimeMount(
                        binding=_EnvironmentBinding(item.environment),
                        permission_ceiling=item.permission_ceiling,
                        working_directory="/",
                        mount_path=item.mount_path,
                    )
                    for item in mounts
                },
                default_mount=mounts[0].alias,
                extensions=extensions,
            )
        except BaseException as exc:
            with CancelScope(shield=True):
                try:
                    await _discard_prepared(mounts)
                except BaseException as cleanup_exc:
                    exc.add_note(f"Prepared Environment cleanup also failed: {cleanup_exc!r}")
            raise
        return EnvironmentRunPlan(
            store=self._store,
            profile=profile,
            mounts=mounts,
            runtime=runtime,
        )

    async def _prepare_content_plugin_skills_mount(
        self,
        *,
        plugin_id: str,
        root: Path,
        alias: str,
        mount_path: str | None,
    ) -> _PreparedMount:
        try:
            normalized = await to_thread.run_sync(_validate_content_plugin_skills_root, root)
            provider = DirectLocalEnvironmentProvider()
            configuration = provider.validate_configuration(
                schema_version="1",
                value={
                    "environment_id": f"content-plugin-{hashlib.sha256(os.fsencode(normalized)).hexdigest()[:16]}",
                    "root": {"path": os.fspath(normalized), "read_only": True},
                    "shell_profiles": [],
                    "allowed_executables": [],
                    "allowed_ports": [],
                    "allowed_environment_keys": [],
                },
            )
            environment = provider.create_environment(
                configuration=configuration,
                state=None,
                runtime=DirectLocalProviderRuntime(),
            )
        except Exception as exc:
            raise EnvironmentLifecycleError(
                "A captured Content Plugin Skill directory could not be prepared.",
                code="content_plugin_skills_mount_failed",
                details={"plugin_id": plugin_id, "root": os.fspath(root)},
            ) from exc
        read_actions = frozenset(
            {
                EnvironmentAction.FILE_STAT,
                EnvironmentAction.FILE_READ_TEXT,
                EnvironmentAction.FILE_READ_BYTES,
                EnvironmentAction.FILE_LIST,
                EnvironmentAction.FILE_QUERY,
                EnvironmentAction.FILE_SEARCH_TEXT,
                EnvironmentAction.FILE_COPY_SOURCE,
            }
        )
        return _PreparedMount(
            alias=alias,
            key=None,
            expected_state_ref=None,
            supplied_state=None,
            environment=environment,
            permission_ceiling=EnvironmentPermissionSet(operations=read_actions),
            mount_path=mount_path,
        )

    async def _prepare_user_skills_mount(self, *, mount_path: str | None) -> _PreparedMount:
        root = self._user_skills_root or Path.home() / ".agents" / "skills"
        try:
            normalized = await to_thread.run_sync(_prepare_user_skills_root, root)
            provider = DirectLocalEnvironmentProvider()
            configuration = provider.validate_configuration(
                schema_version="1",
                value={
                    "environment_id": f"user-skills-{hashlib.sha256(os.fsencode(normalized)).hexdigest()[:16]}",
                    "root": {"path": os.fspath(normalized), "read_only": False},
                    "shell_profiles": [],
                    "allowed_executables": [],
                    "allowed_ports": [],
                    "allowed_environment_keys": [],
                },
            )
            environment = provider.create_environment(
                configuration=configuration,
                state=None,
                runtime=DirectLocalProviderRuntime(),
            )
        except Exception as exc:
            raise EnvironmentLifecycleError(
                "The user Skill directory could not be prepared.",
                code="user_skills_mount_failed",
                details={"root": os.fspath(root)},
            ) from exc
        file_actions = frozenset(action for action in EnvironmentAction if action.value.startswith("environment.file."))
        return _PreparedMount(
            alias="user-skills",
            key=None,
            expected_state_ref=None,
            supplied_state=None,
            environment=environment,
            permission_ceiling=EnvironmentPermissionSet(operations=file_actions),
            mount_path=mount_path,
        )

    async def _load_state(
        self,
        *,
        key: EnvironmentBindingKey,
        reference: ObjectRef | None,
        profile: ResolvedEnvironmentProfile,
    ) -> EnvironmentState | None:
        if reference is None:
            return None
        try:
            value = await self._store.objects.read_model(reference, StoredEnvironmentState)
        except StoreError:
            raise
        if (
            value.binding != key
            or value.provider_schema_version != profile.provider_schema_version
            or value.state.provider_key != profile.provider_key
        ):
            raise EnvironmentLifecycleError(
                "Stored Environment state is incompatible with its binding.",
                code="environment_state_incompatible",
                details={"adapter_key": profile.adapter_key, "provider_key": profile.provider_key},
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


def _validate_content_plugin_skills_root(value: Path) -> Path:
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


async def _discard_prepared(mounts: Sequence[_PreparedMount]) -> None:
    errors: list[Exception] = []
    for mount in reversed(mounts):
        try:
            await mount.environment.close()
        except Exception as exc:
            errors.append(exc)
    if errors:
        raise BaseExceptionGroup("Prepared Environment cleanup failed", errors)


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
