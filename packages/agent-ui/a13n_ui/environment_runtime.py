"""Trusted workspace binding and Host-authoritative Environment run state."""

from __future__ import annotations

import inspect
import os
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from types import MappingProxyType
from typing import Literal

from a13n_environment_provider import (
    DirectLocalProviderRuntime,
    Environment,
    EnvironmentProvider,
    EnvironmentState,
    LocalEnvdProviderRuntime,
    TemporaryLocalEnvdRuntimeAllocator,
    resolve_agent_envd_executable,
)
from anyio import CancelScope, move_on_after, to_thread
from pydantic import BaseModel, ConfigDict, Field

from a13n_ui.composition.catalogs import (
    LOCAL_EIP_PROVIDER_KEY,
    NATIVE_PROVIDER_KEY,
    BinderCatalogEntry,
    EnvironmentWorkspaceBinder,
    ProviderCatalogEntry,
    ProviderRuntime,
    WorkspaceBinderCatalog,
    builtin_workspace_binder_catalog,
    selected_provider_catalog,
)
from a13n_ui.composition.models import DependencyLock, ResolvedEnvironmentProfile
from a13n_ui.errors import CompositionError, EnvironmentLifecycleError, StoreError
from a13n_ui.managed_runtime import ManagedEnvdRuntime
from a13n_ui.settings import EnvdRuntimeSettings
from a13n_ui.storage import (
    EnvironmentBindingKey,
    LocalStore,
    ObjectRef,
    Session,
    StoredEnvironmentState,
)
from a13n_ui.storage.objects import ObjectKind


class WorkspaceBinding(BaseModel):
    """One normalized ordered folder list captured for a single admitted Run."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    folders: tuple[Path, ...] = Field(min_length=1, max_length=64)


type ProviderRuntimeFactory = Callable[
    [EnvironmentProvider],
    object | Awaitable[object | None] | None,
]


@dataclass(frozen=True, slots=True)
class ReconstructedEnvironmentProfile:
    """Verified pinned profile with trusted process-local implementations."""

    profile: ResolvedEnvironmentProfile
    binder: EnvironmentWorkspaceBinder
    provider: EnvironmentProvider


@dataclass(frozen=True, slots=True)
class EnvironmentStatePublication:
    """One changed-state publication outcome independent from continuation selection."""

    key: EnvironmentBindingKey
    previous: ObjectRef | None
    replacement: ObjectRef | None
    status: Literal["unchanged", "published", "failed"]
    error: Exception | None = None


@dataclass(frozen=True, slots=True)
class EnvironmentFinalization:
    """Complete non-destructive cleanup and state-publication outcome."""

    cleanup_errors: tuple[Exception, ...]
    state_publications: tuple[EnvironmentStatePublication, ...]


@dataclass(slots=True)
class _PreparedMount:
    alias: str
    key: EnvironmentBindingKey
    expected_state_ref: ObjectRef | None
    supplied_state: EnvironmentState | None
    environment: Environment


class EnvironmentSnapshotReconstructor:
    """Verify profile provenance and create fresh Provider runtime collaborators."""

    def __init__(
        self,
        *,
        envd_settings: EnvdRuntimeSettings | None = None,
        binder_catalog: WorkspaceBinderCatalog | None = None,
        provider_entries: tuple[ProviderCatalogEntry, ...] = (),
        runtime_factories: Mapping[str, ProviderRuntimeFactory] | None = None,
        local_runtime_parent: Path | None = None,
    ) -> None:
        self._envd_settings = envd_settings or EnvdRuntimeSettings()
        self._binders = binder_catalog or builtin_workspace_binder_catalog()
        self._provider_entries = provider_entries
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
        """Verify exact installed Provider and binder provenance without external I/O."""

        providers = selected_provider_catalog(
            provider_keys=(profile.provider_key,),
            explicit_entries=self._provider_entries,
        )
        provider = providers.require(profile.provider_key)
        binder = self._binders.require(profile.binder_key)
        _verify_profile_provenance(profile, provider=provider, binder=binder)
        if binder.binder.provider_key != provider.provider.key:
            raise CompositionError(
                "The pinned workspace binder is incompatible with its Environment Provider.",
                code="workspace_binder_incompatible",
                details={"binder_key": profile.binder_key, "provider_key": profile.provider_key},
            )
        if profile.provider_schema_version not in provider.provider.configuration_versions:
            raise CompositionError(
                "The pinned Provider configuration version is unavailable.",
                code="environment_snapshot_incompatible",
                details={"provider_key": profile.provider_key},
            )
        return ReconstructedEnvironmentProfile(
            profile=profile,
            binder=binder.binder,
            provider=provider.provider,
        )

    async def bind(
        self,
        reconstructed: ReconstructedEnvironmentProfile,
        *,
        folder: Path,
        state: EnvironmentState | None,
    ) -> Environment:
        """Create one fresh inert adapter for one normalized folder."""

        collaborator = await self._runtime_collaborator(reconstructed.provider)
        runtime = ProviderRuntime(provider=reconstructed.provider, collaborator=collaborator)
        try:
            environment = await reconstructed.binder.bind(
                profile=reconstructed.profile,
                folder=folder,
                state=state,
                runtime=runtime,
            )
        except CompositionError:
            raise
        except Exception as exc:
            raise EnvironmentLifecycleError(
                "The Environment workspace binder could not construct an adapter.",
                code="environment_binding_failed",
                details={"binder_key": reconstructed.profile.binder_key},
            ) from exc
        if not isinstance(environment, Environment):
            raise EnvironmentLifecycleError(
                "The Environment workspace binder returned an invalid adapter.",
                code="environment_binding_invalid",
                details={"binder_key": reconstructed.profile.binder_key},
            )
        if environment.provider_key != reconstructed.profile.provider_key:
            raise EnvironmentLifecycleError(
                "The Environment workspace binder retargeted its pinned Provider.",
                code="environment_binding_invalid",
                details={"binder_key": reconstructed.profile.binder_key},
            )
        return environment

    async def _runtime_collaborator(self, provider: EnvironmentProvider) -> object | None:
        if provider.key == NATIVE_PROVIDER_KEY:
            return DirectLocalProviderRuntime()
        if provider.key == LOCAL_EIP_PROVIDER_KEY:
            executable = self._envd_settings.executable
            if executable is None:
                if self._managed_envd is None:
                    raise EnvironmentLifecycleError(
                        "Local EIP requires an Agent UI runtime cache or an explicit executable override.",
                        code="local_eip_runtime_unavailable",
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
                "No trusted runtime factory is available for the pinned Environment Provider.",
                code="provider_runtime_unsupported",
                details={"provider_key": provider.key},
            )
        value = factory(provider)
        return await value if inspect.isawaitable(value) else value


class EnvironmentRunPlan:
    """Prepared single-use Environment inputs plus later Host-state publication."""

    def __init__(
        self, *, store: LocalStore, profile: ResolvedEnvironmentProfile, mounts: Sequence[_PreparedMount]
    ) -> None:
        self._store = store
        self.profile = profile
        self._mounts = tuple(mounts)
        self.environments: Mapping[str, Environment] = MappingProxyType(
            {mount.alias: mount.environment for mount in self._mounts}
        )
        self.default_environment = self._mounts[0].alias
        self._finalized = False

    async def finalize(self, *, timeout_seconds: float = 30.0) -> EnvironmentFinalization:
        """Close adapters within one bound, then publish every known changed cached state."""

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
            final_state = mount.environment.dump_state()
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
                    envelope = await self._store.objects.publish_model(
                        object_kind=ObjectKind.environment_state,
                        value=stored,
                    )
                    replacement = envelope.ref
                await self._store.environment_states.select_state(
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
    """Prepare deterministic Harness mounts from one Session pin and message binding."""

    def __init__(
        self,
        store: LocalStore,
        reconstructor: EnvironmentSnapshotReconstructor,
    ) -> None:
        self._store = store
        self._reconstructor = reconstructor

    async def prepare(self, session: Session, binding: WorkspaceBinding) -> EnvironmentRunPlan:
        """Load Host-authoritative state and construct fresh adapters before Run entry."""

        profile = await self._store.objects.read_model(
            session.environment_snapshot.object,
            ResolvedEnvironmentProfile,
        )
        reconstructed = self._reconstructor.reconstruct(profile)
        mounts: list[_PreparedMount] = []
        try:
            for index, folder in enumerate(binding.folders, start=1):
                key = EnvironmentBindingKey(
                    session_id=session.session_id,
                    profile_digest=session.environment_snapshot.object.logical_digest,
                    binder_key=profile.binder_key,
                    normalized_folder=os.fspath(folder),
                )
                head = await self._store.environment_states.ensure(key)
                state = await self._load_state(
                    key=key,
                    reference=head.state,
                    profile=profile,
                )
                environment = await self._reconstructor.bind(
                    reconstructed,
                    folder=folder,
                    state=state,
                )
                mounts.append(
                    _PreparedMount(
                        alias="workspace" if index == 1 else f"workspace-{index}",
                        key=key,
                        expected_state_ref=head.state,
                        supplied_state=state,
                        environment=environment,
                    )
                )
        except BaseException as exc:
            cleanup_error: BaseException | None = None
            with CancelScope(shield=True):
                try:
                    await _discard_prepared(mounts)
                except BaseException as cleanup_exc:
                    cleanup_error = cleanup_exc
            if cleanup_error is not None:
                exc.add_note(f"Prepared Environment cleanup also failed: {cleanup_error!r}")
            raise
        return EnvironmentRunPlan(store=self._store, profile=profile, mounts=mounts)

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
                "Stored Environment state is incompatible with its pinned binding.",
                code="environment_state_incompatible",
                details={"binder_key": profile.binder_key, "provider_key": profile.provider_key},
            )
        return value.state


async def normalize_workspace_binding(folders: Sequence[Path | str]) -> WorkspaceBinding:
    """Resolve, validate, and de-duplicate one ordered message-time folder list."""

    if not folders:
        raise EnvironmentLifecycleError(
            "A Workspace binding requires at least one folder.",
            code="workspace_binding_empty",
        )
    if len(folders) > 64:
        raise EnvironmentLifecycleError(
            "A Workspace binding exceeds the supported folder count.",
            code="workspace_binding_too_large",
        )
    normalized: list[Path] = []
    seen: set[Path] = set()
    for value in folders:
        try:
            path = await to_thread.run_sync(_normalize_folder, Path(value))
        except (OSError, ValueError) as exc:
            raise EnvironmentLifecycleError(
                "A Workspace binding folder is missing, inaccessible, or not a directory.",
                code="workspace_folder_invalid",
                details={"folder": os.fspath(value)},
            ) from exc
        if path not in seen:
            normalized.append(path)
            seen.add(path)
    return WorkspaceBinding(folders=tuple(normalized))


def _verify_profile_provenance(
    profile: ResolvedEnvironmentProfile,
    *,
    provider: ProviderCatalogEntry,
    binder: BinderCatalogEntry,
) -> None:
    if provider.lock != profile.provider_lock:
        raise _provenance_mismatch(profile.provider_lock)
    if binder.lock != profile.binder_lock:
        raise _provenance_mismatch(profile.binder_lock)


def _provenance_mismatch(lock: DependencyLock) -> CompositionError:
    return CompositionError(
        "Installed trusted runtime provenance does not match the pinned Environment snapshot.",
        code="environment_snapshot_provenance_mismatch",
        details={"kind": lock.dependency_kind, "key": lock.key},
    )


def _normalize_folder(value: Path) -> Path:
    raw = value.expanduser()
    if "\x00" in os.fspath(raw):
        raise ValueError("folder contains NUL")
    resolved = raw.resolve(strict=True)
    if not resolved.is_dir() or not os.access(resolved, os.R_OK | os.X_OK):
        raise OSError("folder is not an accessible directory")
    return resolved


async def _discard_prepared(mounts: Sequence[_PreparedMount]) -> None:
    errors: list[Exception] = []
    for mount in reversed(mounts):
        try:
            await mount.environment.close()
        except Exception as exc:
            errors.append(exc)
    if errors:
        raise BaseExceptionGroup("Prepared Environment adapter cleanup failed", errors)


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
    "WorkspaceBinding",
    "normalize_workspace_binding",
]
