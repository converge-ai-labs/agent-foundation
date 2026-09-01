"""Exact PluginVersion resolution for Preset publication and Run acceptance."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, packages_distributions, version
from typing import Final

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.tags import Tag, parse_tag, sys_tags
from packaging.utils import canonicalize_name
from packaging.version import Version
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.iam import AuthenticatedActor, WorkspaceAction, authorize_workspace
from a13n_service.plugins.domain import PluginLifecycleState, PluginSource
from a13n_service.plugins.models import PluginRecord, PluginVersionRecord

from .domain import OnDemandPluginSelection, PluginRuntimeMode, PluginSelection, ResolvedPluginVersion

_READY: Final = "ready"


class PluginSelectionError(Exception):
    """Bounded internal failure translated by the owning Agent operation."""

    def __init__(self, reason: str, *, path: str = "plugins") -> None:
        super().__init__(reason)
        self.reason = reason
        self.path = path


@dataclass(frozen=True, slots=True)
class PreparedPluginVersion:
    selection: OnDemandPluginSelection
    plugin_id: str
    plugin_key: str
    distribution_name: str
    distribution_version: str
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
            plugin_version_id=self.selection.plugin_version_id,
            plugin_key=self.plugin_key,
            distribution_name=self.distribution_name,
            distribution_version=self.distribution_version,
            top_level_package=self.top_level_package,
            wheel_digest=self.wheel_digest,
            config=self.selection.config,
        )


@dataclass(frozen=True, slots=True)
class PreparedPluginSelections:
    items: tuple[PreparedPluginVersion, ...]

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
    ) -> None:
        self._sessions = sessions
        self.runtime_mode = runtime_mode
        self._installed_distributions = (
            installed_distributions if installed_distributions is not None else _installed_distribution_versions()
        )
        self._installed_top_level_packages = (
            installed_top_level_packages
            if installed_top_level_packages is not None
            else frozenset(packages_distributions())
        )
        self._compatible_tags = compatible_tags if compatible_tags is not None else frozenset(sys_tags())
        self._python_version = python_version or Version(".".join(str(item) for item in sys.version_info[:3]))

    async def prepare(
        self,
        session: AsyncSession,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        selections: tuple[PluginSelection, ...],
    ) -> PreparedPluginSelections:
        if not selections:
            return PreparedPluginSelections(())
        self._require_supported_mode(selections)
        await authorize_workspace(
            session,
            actor=actor,
            workspace_id=workspace_id,
            action=WorkspaceAction.plugin_read,
        )
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
            if plugin.lifecycle_state != PluginLifecycleState.available.value or plugin_version.status != _READY:
                raise PluginSelectionError("plugin_version_unavailable", path=f"plugins.{index}")
            selected_version = version_by_plugin.setdefault(plugin.id, plugin_version.id)
            if selected_version != plugin_version.id:
                raise PluginSelectionError("plugin_version_conflict", path=f"plugins.{index}")
            item = _prepare_item(selection, plugin_version, plugin)
            self._validate_runtime_compatibility(item, path=f"plugins.{index}")
            prepared.append(item)
        return PreparedPluginSelections(tuple(prepared))

    async def freeze_in_transaction(
        self,
        session: AsyncSession,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        prepared: PreparedPluginSelections,
    ) -> tuple[ResolvedPluginVersion, ...]:
        if not prepared.items:
            return ()
        await authorize_workspace(
            session,
            actor=actor,
            workspace_id=workspace_id,
            action=WorkspaceAction.plugin_read,
        )
        version_ids = tuple(item.selection.plugin_version_id for item in prepared.items)
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
            current = by_id.get(expected.selection.plugin_version_id)
            if current is None:
                raise PluginSelectionError("plugin_version_changed", path=f"plugins.{index}")
            plugin_version, plugin = current
            if plugin.lifecycle_state != PluginLifecycleState.available.value or plugin_version.status != _READY:
                raise PluginSelectionError("plugin_version_unavailable", path=f"plugins.{index}")
            if _prepare_item(expected.selection, plugin_version, plugin) != expected:
                raise PluginSelectionError("plugin_version_changed", path=f"plugins.{index}")
            result.append(expected.resolved())
        return tuple(result)

    def _require_supported_mode(self, selections: tuple[PluginSelection, ...]) -> None:
        for index, selection in enumerate(selections):
            if selection.mode is not self.runtime_mode:
                raise PluginSelectionError("plugin_runtime_mode_mismatch", path=f"plugins.{index}")
        if self.runtime_mode is PluginRuntimeMode.runner:
            raise PluginSelectionError("plugin_runtime_resolution_unavailable")

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
        for raw_requirement in item.requires_dist:
            requirement = Requirement(raw_requirement)
            if requirement.marker is not None and not requirement.marker.evaluate():
                continue
            installed = self._installed_distributions.get(canonicalize_name(requirement.name))
            if installed is None:
                raise PluginSelectionError("plugin_worker_dependency_missing", path=path)
            if requirement.specifier and Version(installed) not in requirement.specifier:
                raise PluginSelectionError("plugin_platform_incompatible", path=path)


def _prepare_item(
    selection: OnDemandPluginSelection,
    plugin_version: PluginVersionRecord,
    plugin: PluginRecord,
) -> PreparedPluginVersion:
    return PreparedPluginVersion(
        selection=selection,
        plugin_id=plugin.id,
        plugin_key=plugin.plugin_key,
        distribution_name=plugin.distribution_name,
        distribution_version=plugin_version.version,
        top_level_package=plugin.top_level_package,
        wheel_digest=plugin_version.content_digest,
        artifact_ref=plugin_version.artifact_ref,
        requires_dist=tuple(plugin_version.requires_dist),
        requires_python=plugin_version.requires_python,
        wheel_tags=tuple(plugin_version.wheel_tags),
        root_is_purelib=plugin_version.root_is_purelib,
        source=PluginSource(plugin.source),
    )


def _installed_distribution_versions() -> dict[str, str]:
    result: dict[str, str] = {}
    for distributions in packages_distributions().values():
        for distribution in distributions:
            normalized = canonicalize_name(distribution)
            if normalized in result:
                continue
            try:
                result[normalized] = version(distribution)
            except PackageNotFoundError:
                continue
    return result
