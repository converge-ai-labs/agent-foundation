"""Exact PluginVersion resolution for Agent Revision creation and Run acceptance."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Final

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.tags import Tag, parse_tag, sys_tags
from packaging.utils import canonicalize_name
from packaging.version import Version
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.iam import AuthenticatedActor, WorkspaceAction, authorize_workspace
from a13n_service.plugins.domain import PluginSource
from a13n_service.plugins.models import PluginRecord, PluginRuntimeStateRecord, PluginVersionRecord
from a13n_service.plugins.runtime import (
    PluginRuntimeLock,
    PluginRuntimeLockError,
    PluginRuntimeLockStore,
    RuntimeTarget,
    WorkerReleaseManifest,
    default_runtime_target,
    installed_distribution_versions,
    installed_harness_version,
)
from a13n_service.plugins.runtime import installed_top_level_packages as installed_service_packages
from a13n_service.temporal import Clock

from .domain import (
    OnDemandPluginSelection,
    PluginRuntimeMode,
    PluginSelection,
    ResolvedPluginVersion,
    RunnerPluginSelection,
)

_READY: Final = "ready"


class PluginSelectionError(Exception):
    """Bounded internal failure translated by the owning Agent operation."""

    def __init__(self, reason: str, *, path: str = "plugins") -> None:
        super().__init__(reason)
        self.reason = reason
        self.path = path


@dataclass(frozen=True, slots=True)
class PreparedPluginVersion:
    selection: PluginSelection
    plugin_version_id: str
    plugin_id: str
    plugin_key: str
    distribution_name: str
    version: str
    top_level_package: str
    wheel_digest: str
    artifact_ref: str
    requires_dist: tuple[str, ...]
    requires_python: str | None
    wheel_tags: tuple[str, ...]
    root_is_purelib: bool
    source: PluginSource

    def resolved(self) -> ResolvedPluginVersion:
        return ResolvedPluginVersion(
            instance_name=self.selection.instance_name,
            plugin_id=self.plugin_id,
            plugin_version_id=self.plugin_version_id,
            plugin_key=self.plugin_key,
            distribution_name=self.distribution_name,
            version=self.version,
            top_level_package=self.top_level_package,
            wheel_digest=self.wheel_digest,
            config=self.selection.config,
        )


@dataclass(frozen=True, slots=True)
class PreparedPluginSelections:
    items: tuple[PreparedPluginVersion, ...]
    require_available_plugin: bool = True
    runtime_lock_digest: str | None = None
    require_active_catalog: bool = False

    @property
    def resolved(self) -> tuple[ResolvedPluginVersion, ...]:
        return tuple(item.resolved() for item in self.items)


class AgentPluginSelectionResolver:
    """Resolve on-demand selections without importing or installing Plugin code."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        runtime_mode: PluginRuntimeMode,
        installed_distributions: dict[str, str] | None = None,
        installed_top_level_packages: frozenset[str] | None = None,
        compatible_tags: frozenset[Tag] | None = None,
        python_version: Version | None = None,
        worker_release: str = "unknown",
        harness_version: str | None = None,
        runtime_target: RuntimeTarget | None = None,
        runtime_lock_clock: Clock | None = None,
    ) -> None:
        self._sessions = sessions
        self.runtime_mode = runtime_mode
        distribution_versions = (
            installed_distributions if installed_distributions is not None else installed_distribution_versions()
        )
        self._installed_distributions = {
            str(canonicalize_name(name)): distribution_version
            for name, distribution_version in distribution_versions.items()
        }
        installed_packages = (
            installed_top_level_packages if installed_top_level_packages is not None else installed_service_packages()
        )
        self._installed_top_level_packages = frozenset(installed_packages).union(sys.stdlib_module_names)
        self._compatible_tags = compatible_tags if compatible_tags is not None else frozenset(sys_tags())
        self._python_version = python_version or Version(".".join(str(item) for item in sys.version_info[:3]))
        self.runtime_locks = PluginRuntimeLockStore(
            WorkerReleaseManifest(
                worker_release=worker_release,
                harness_version=harness_version or installed_harness_version(),
                runtime_target=runtime_target or default_runtime_target(),
                distributions=self._installed_distributions,
            ),
            clock=runtime_lock_clock,
        )

    async def prepare(
        self,
        session: AsyncSession,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        selections: tuple[PluginSelection, ...],
    ) -> PreparedPluginSelections:
        self._require_supported_mode(selections)
        if not selections:
            if self.runtime_mode is PluginRuntimeMode.runner:
                state = await self._require_active_runtime_state(session, for_update=False)
                return PreparedPluginSelections(
                    (),
                    runtime_lock_digest=state.active_lock_digest,
                    require_active_catalog=True,
                )
            return PreparedPluginSelections(())
        await authorize_workspace(
            session,
            actor=actor,
            workspace_id=workspace_id,
            action=WorkspaceAction.plugin_read,
        )
        if self.runtime_mode is PluginRuntimeMode.runner:
            return await self._prepare_runner(session, selections)
        return await self._prepare_on_demand(session, selections)

    async def _prepare_on_demand(
        self,
        session: AsyncSession,
        selections: tuple[PluginSelection, ...],
    ) -> PreparedPluginSelections:
        version_ids = tuple(item.plugin_version_id for item in selections if isinstance(item, OnDemandPluginSelection))
        rows = tuple(
            (
                await session.execute(
                    select(PluginVersionRecord, PluginRecord)
                    .join(PluginRecord, PluginRecord.id == PluginVersionRecord.plugin_id)
                    .where(PluginVersionRecord.id.in_(version_ids))
                )
            ).all()
        )
        by_id = {plugin_version.id: (plugin_version, plugin) for plugin_version, plugin in rows}
        if set(by_id) != set(version_ids):
            raise PluginSelectionError("plugin_version_not_found")

        prepared: list[PreparedPluginVersion] = []
        version_by_plugin: dict[str, str] = {}
        for index, selection in enumerate(selections):
            if not isinstance(selection, OnDemandPluginSelection):
                raise PluginSelectionError("plugin_runtime_mode_mismatch", path=f"plugins.{index}")
            plugin_version, plugin = by_id[selection.plugin_version_id]
            if plugin.archived_at is not None or plugin_version.status != _READY:
                raise PluginSelectionError("plugin_version_unavailable", path=f"plugins.{index}")
            selected_version = version_by_plugin.setdefault(plugin.id, plugin_version.id)
            if selected_version != plugin_version.id:
                raise PluginSelectionError("plugin_version_conflict", path=f"plugins.{index}")
            item = _prepare_item(selection, plugin_version, plugin)
            self._validate_runtime_compatibility(item, path=f"plugins.{index}")
            prepared.append(item)
        return PreparedPluginSelections(tuple(prepared))

    async def _prepare_runner(
        self,
        session: AsyncSession,
        selections: tuple[PluginSelection, ...],
    ) -> PreparedPluginSelections:
        state = await self._require_active_runtime_state(session, for_update=False)
        plugin_keys = tuple(item.plugin_key for item in selections if isinstance(item, RunnerPluginSelection))
        plugins = tuple(
            (await session.scalars(select(PluginRecord).where(PluginRecord.plugin_key.in_(plugin_keys)))).all()
        )
        by_key = {plugin.plugin_key: plugin for plugin in plugins}
        if set(by_key) != set(plugin_keys):
            raise PluginSelectionError("plugin_version_unavailable")
        active_version_ids = tuple(
            plugin.active_version_id for plugin in plugins if plugin.active_version_id is not None
        )
        versions = tuple(
            (
                await session.scalars(select(PluginVersionRecord).where(PluginVersionRecord.id.in_(active_version_ids)))
            ).all()
        )
        by_version_id = {plugin_version.id: plugin_version for plugin_version in versions}

        prepared: list[PreparedPluginVersion] = []
        for index, selection in enumerate(selections):
            path = f"plugins.{index}"
            if not isinstance(selection, RunnerPluginSelection):
                raise PluginSelectionError("plugin_runtime_mode_mismatch", path=path)
            plugin = by_key[selection.plugin_key]
            plugin_version = (
                by_version_id.get(plugin.active_version_id) if plugin.active_version_id is not None else None
            )
            if (
                plugin.archived_at is not None
                or plugin_version is None
                or plugin_version.plugin_id != plugin.id
                or plugin_version.status != _READY
            ):
                raise PluginSelectionError("plugin_version_unavailable", path=path)
            prepared.append(_prepare_item(selection, plugin_version, plugin))
        return PreparedPluginSelections(
            tuple(prepared),
            runtime_lock_digest=state.active_lock_digest,
            require_active_catalog=True,
        )

    async def prepare_retained(
        self,
        session: AsyncSession,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        selections: tuple[PluginSelection, ...],
        resolved: tuple[ResolvedPluginVersion, ...],
        runtime_lock_digest: str,
    ) -> PreparedPluginSelections:
        """Revalidate exact frozen versions without following mutable Plugin heads."""

        if len(selections) != len(resolved):
            raise PluginSelectionError("plugin_revision_invalid")
        if not selections:
            return PreparedPluginSelections(
                (),
                require_available_plugin=False,
                runtime_lock_digest=runtime_lock_digest,
            )
        await authorize_workspace(
            session,
            actor=actor,
            workspace_id=workspace_id,
            action=WorkspaceAction.plugin_read,
        )
        version_ids = tuple(item.plugin_version_id for item in resolved)
        rows = tuple(
            (
                await session.execute(
                    select(PluginVersionRecord, PluginRecord)
                    .join(PluginRecord, PluginRecord.id == PluginVersionRecord.plugin_id)
                    .where(PluginVersionRecord.id.in_(version_ids))
                )
            ).all()
        )
        by_id = {plugin_version.id: (plugin_version, plugin) for plugin_version, plugin in rows}
        if set(by_id) != set(version_ids):
            raise PluginSelectionError("plugin_version_unavailable")

        prepared: list[PreparedPluginVersion] = []
        version_by_plugin: dict[str, str] = {}
        for index, (selection, expected) in enumerate(zip(selections, resolved, strict=True)):
            path = f"plugins.{index}"
            if selection.mode is not self.runtime_mode:
                raise PluginSelectionError("plugin_runtime_mode_mismatch", path=path)
            if (
                selection.instance_name != expected.instance_name
                or selection.config != expected.config
                or (
                    isinstance(selection, OnDemandPluginSelection)
                    and selection.plugin_version_id != expected.plugin_version_id
                )
                or (isinstance(selection, RunnerPluginSelection) and selection.plugin_key != expected.plugin_key)
            ):
                raise PluginSelectionError("plugin_revision_invalid", path=path)
            current = by_id.get(expected.plugin_version_id)
            if current is None:
                raise PluginSelectionError("plugin_version_unavailable", path=path)
            plugin_version, plugin = current
            if plugin_version.status != _READY:
                raise PluginSelectionError("plugin_version_unavailable", path=path)
            selected_version = version_by_plugin.setdefault(plugin.id, plugin_version.id)
            if selected_version != plugin_version.id:
                raise PluginSelectionError("plugin_version_conflict", path=path)
            item = _prepare_item(selection, plugin_version, plugin)
            if item.resolved() != expected:
                raise PluginSelectionError("plugin_version_changed", path=path)
            if self.runtime_mode is PluginRuntimeMode.on_demand:
                self._validate_runtime_compatibility(item, path=path)
            prepared.append(item)
        return PreparedPluginSelections(
            tuple(prepared),
            require_available_plugin=False,
            runtime_lock_digest=runtime_lock_digest,
        )

    async def freeze_in_transaction(
        self,
        session: AsyncSession,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        prepared: PreparedPluginSelections,
    ) -> tuple[ResolvedPluginVersion, ...]:
        if prepared.require_active_catalog:
            active_state = await self._require_active_runtime_state(session, for_update=True)
            if active_state.active_lock_digest != prepared.runtime_lock_digest:
                raise PluginSelectionError("plugin_version_changed")
        if not prepared.items:
            return ()
        await authorize_workspace(
            session,
            actor=actor,
            workspace_id=workspace_id,
            action=WorkspaceAction.plugin_read,
        )
        version_ids = tuple(item.plugin_version_id for item in prepared.items)
        rows = tuple(
            (
                await session.execute(
                    select(PluginVersionRecord, PluginRecord)
                    .join(PluginRecord, PluginRecord.id == PluginVersionRecord.plugin_id)
                    .where(PluginVersionRecord.id.in_(version_ids))
                    .with_for_update()
                )
            ).all()
        )
        by_id = {plugin_version.id: (plugin_version, plugin) for plugin_version, plugin in rows}
        result: list[ResolvedPluginVersion] = []
        for index, expected in enumerate(prepared.items):
            current = by_id.get(expected.plugin_version_id)
            if current is None:
                raise PluginSelectionError("plugin_version_changed", path=f"plugins.{index}")
            plugin_version, plugin = current
            if (
                prepared.require_available_plugin and plugin.archived_at is not None
            ) or plugin_version.status != _READY:
                raise PluginSelectionError("plugin_version_unavailable", path=f"plugins.{index}")
            if prepared.require_active_catalog and plugin.active_version_id != expected.plugin_version_id:
                raise PluginSelectionError("plugin_version_changed", path=f"plugins.{index}")
            if _prepare_item(expected.selection, plugin_version, plugin) != expected:
                raise PluginSelectionError("plugin_version_changed", path=f"plugins.{index}")
            result.append(expected.resolved())
        return tuple(result)

    async def freeze_runtime_lock(
        self,
        session: AsyncSession,
        *,
        prepared: PreparedPluginSelections,
        child_lock_digests: tuple[str, ...],
        use_active_catalog: bool,
    ) -> PluginRuntimeLock:
        if self.runtime_mode is PluginRuntimeMode.on_demand:
            return await self.runtime_locks.build_and_persist(
                session,
                mode="on_demand",
                plugins=prepared.items,
                child_lock_digests=child_lock_digests,
            )
        lock_digest = prepared.runtime_lock_digest
        if use_active_catalog:
            try:
                state = await self._require_active_runtime_state(session, for_update=True)
            except PluginSelectionError as error:
                raise PluginRuntimeLockError(error.reason, path=error.path) from error
            lock_digest = state.active_lock_digest
        if lock_digest is None:
            raise PluginRuntimeLockError("plugin_runtime_lock_unavailable")
        return await self.runtime_locks.require_runner_catalog(
            session,
            lock_digest,
            plugins=prepared.items,
            child_lock_digests=child_lock_digests,
        )

    def _require_supported_mode(self, selections: tuple[PluginSelection, ...]) -> None:
        for index, selection in enumerate(selections):
            if selection.mode is not self.runtime_mode:
                raise PluginSelectionError("plugin_runtime_mode_mismatch", path=f"plugins.{index}")

    async def _require_active_runtime_state(
        self,
        session: AsyncSession,
        *,
        for_update: bool,
    ) -> PluginRuntimeStateRecord:
        statement = select(PluginRuntimeStateRecord).where(PluginRuntimeStateRecord.id == "runtime")
        if for_update:
            statement = statement.with_for_update()
        state = await session.scalar(statement)
        if state is None or state.mode != PluginRuntimeMode.runner.value:
            raise PluginSelectionError("plugin_runtime_mode_mismatch")
        if state.active_lock_digest is None:
            raise PluginSelectionError("plugin_runtime_unavailable")
        return state

    def _validate_runtime_compatibility(self, item: PreparedPluginVersion, *, path: str) -> None:
        if not item.root_is_purelib:
            raise PluginSelectionError("plugin_runtime_incompatible", path=path)
        wheel_tags = frozenset(tag for value in item.wheel_tags for tag in parse_tag(value))
        if not wheel_tags.intersection(self._compatible_tags):
            raise PluginSelectionError("plugin_runtime_incompatible", path=path)
        if item.requires_python is not None and self._python_version not in SpecifierSet(item.requires_python):
            raise PluginSelectionError("plugin_runtime_incompatible", path=path)
        if item.source is PluginSource.uploaded and item.top_level_package in self._installed_top_level_packages:
            raise PluginSelectionError("plugin_platform_incompatible", path=path)
        if (
            item.source is PluginSource.uploaded
            and canonicalize_name(item.distribution_name) in self._installed_distributions
        ):
            raise PluginSelectionError("plugin_platform_incompatible", path=path)
        for raw_requirement in item.requires_dist:
            requirement = Requirement(raw_requirement)
            if requirement.marker is not None and not requirement.marker.evaluate():
                continue
            if requirement.url is not None:
                raise PluginSelectionError("plugin_platform_incompatible", path=path)
            installed = self._installed_distributions.get(canonicalize_name(requirement.name))
            if installed is None:
                raise PluginSelectionError("plugin_worker_dependency_missing", path=path)
            if requirement.specifier and Version(installed) not in requirement.specifier:
                raise PluginSelectionError("plugin_platform_incompatible", path=path)


def _prepare_item(
    selection: PluginSelection,
    plugin_version: PluginVersionRecord,
    plugin: PluginRecord,
) -> PreparedPluginVersion:
    return PreparedPluginVersion(
        selection=selection,
        plugin_version_id=plugin_version.id,
        plugin_id=plugin.id,
        plugin_key=plugin.plugin_key,
        distribution_name=plugin.distribution_name,
        version=plugin_version.version,
        top_level_package=plugin.top_level_package,
        wheel_digest=plugin_version.content_digest,
        artifact_ref=plugin_version.artifact_ref,
        requires_dist=tuple(plugin_version.requires_dist),
        requires_python=plugin_version.requires_python,
        wheel_tags=tuple(plugin_version.wheel_tags),
        root_is_purelib=plugin_version.root_is_purelib,
        source=PluginSource(plugin.source),
    )
