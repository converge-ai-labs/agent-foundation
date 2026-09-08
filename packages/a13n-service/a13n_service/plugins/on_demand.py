"""Process-local loading for the on-demand Plugin Runtime profile."""

from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass
from pathlib import Path

import anyio
from a13n_harness.plugin_factories import (
    HarnessPluginFactory,
    HarnessPluginFactoryCatalog,
    HarnessPluginFactoryRegistration,
    build_harness_plugin_factory_catalog,
)
from packaging.utils import canonicalize_name

from .materialization import PluginRuntimeMaterializationError, PluginRuntimeMaterializer
from .runtime import LockedPlugin, PluginRuntimeLock


class OnDemandPluginRuntimeDeclined(Exception):
    """A pre-claim compatibility failure that leaves the Worker usable."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class OnDemandPluginRuntimeFatalError(Exception):
    """A post-publication failure that requires Worker process replacement."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class LoadedPluginProvenance:
    """Verified identity retained for the lifetime of one Worker process."""

    plugin_key: str
    plugin_version_id: str
    distribution_name: str
    distribution_version: str
    top_level_package: str
    wheel_digest: str
    runtime_root: Path


@dataclass(frozen=True, slots=True)
class PreparedOnDemandPluginRuntime:
    """Exact selected factory catalog ready for Agent reconstruction."""

    runtime_lock_digest: str
    factory_catalog: HarnessPluginFactoryCatalog


@dataclass(frozen=True, slots=True)
class _LoadedPlugin:
    locked: LockedPlugin
    provenance: LoadedPluginProvenance
    registration: HarnessPluginFactoryRegistration
    factory: HarnessPluginFactory


class OnDemandPluginRuntime:
    """Monotonically accumulate compatible Plugin factories in one interpreter."""

    def __init__(self, materializer: PluginRuntimeMaterializer) -> None:
        self._materializer = materializer
        self._lock = anyio.Lock()
        self._by_key: dict[str, _LoadedPlugin] = {}
        self._by_distribution: dict[str, _LoadedPlugin] = {}
        self._by_top_level_package: dict[str, _LoadedPlugin] = {}
        self._published_paths: list[Path] = []
        self._fatal_reason: str | None = None

    @property
    def ready(self) -> bool:
        """Whether this process may continue preflighting work."""

        return self._fatal_reason is None

    @property
    def loaded_plugins(self) -> tuple[LoadedPluginProvenance, ...]:
        """Return a deterministic process-local diagnostic snapshot."""

        return tuple(self._by_key[key].provenance for key in sorted(self._by_key))

    @property
    def published_paths(self) -> tuple[Path, ...]:
        """Return immutable Runtime roots currently visible to Python imports."""

        return tuple(self._published_paths)

    async def prepare_for_claim(self, runtime_lock: PluginRuntimeLock) -> PreparedOnDemandPluginRuntime:
        """Load one exact compatible lock before the caller claims its Run."""

        async with self._lock:
            self._require_ready()
            plugins = _validate_on_demand_lock(runtime_lock)
            new_plugins = self._preflight_registry(plugins)
            try:
                materialized = await self._materializer.materialize(runtime_lock)
            except PluginRuntimeMaterializationError as error:
                raise OnDemandPluginRuntimeDeclined(error.reason) from error

            if new_plugins:
                self._publish_and_load(
                    new_plugins,
                    runtime_root=materialized.root,
                    site_packages=materialized.site_packages,
                )

            entries = tuple(
                (self._by_key[plugin.plugin_key].registration, self._by_key[plugin.plugin_key].factory)
                for plugin in plugins
            )
            return PreparedOnDemandPluginRuntime(
                runtime_lock_digest=runtime_lock.digest,
                factory_catalog=HarnessPluginFactoryCatalog(entries),
            )

    def _require_ready(self) -> None:
        if self._fatal_reason is not None:
            raise OnDemandPluginRuntimeFatalError(self._fatal_reason)

    def _preflight_registry(self, plugins: tuple[LockedPlugin, ...]) -> tuple[LockedPlugin, ...]:
        new_plugins: list[LockedPlugin] = []
        for plugin in plugins:
            loaded = self._by_key.get(plugin.plugin_key)
            if loaded is not None:
                if loaded.locked != plugin:
                    raise OnDemandPluginRuntimeDeclined("plugin_runtime_process_conflict")
                continue

            distribution_name = str(canonicalize_name(plugin.distribution_name))
            if distribution_name in self._by_distribution:
                raise OnDemandPluginRuntimeDeclined("plugin_runtime_process_conflict")
            if plugin.top_level_package in self._by_top_level_package:
                raise OnDemandPluginRuntimeDeclined("plugin_runtime_process_conflict")
            if _module_is_loaded(plugin.top_level_package):
                raise OnDemandPluginRuntimeDeclined("plugin_runtime_process_conflict")
            new_plugins.append(plugin)
        return tuple(new_plugins)

    def _publish_and_load(
        self,
        plugins: tuple[LockedPlugin, ...],
        *,
        runtime_root: Path,
        site_packages: Path,
    ) -> None:
        path = str(site_packages)
        if path not in sys.path:
            sys.path.insert(0, path)
        self._published_paths.append(site_packages)
        importlib.invalidate_caches()
        try:
            catalog = build_harness_plugin_factory_catalog(plugin_keys=tuple(plugin.plugin_key for plugin in plugins))
            loaded = _verify_loaded_plugins(
                plugins,
                catalog=catalog,
                runtime_root=runtime_root,
                site_packages=site_packages,
            )
        except Exception as error:
            self._fatal_reason = "plugin_factory_load_failed"
            raise OnDemandPluginRuntimeFatalError(self._fatal_reason) from error

        for item in loaded:
            self._by_key[item.locked.plugin_key] = item
            self._by_distribution[str(canonicalize_name(item.locked.distribution_name))] = item
            self._by_top_level_package[item.locked.top_level_package] = item


def _validate_on_demand_lock(runtime_lock: PluginRuntimeLock) -> tuple[LockedPlugin, ...]:
    if runtime_lock.mode != "on_demand":
        raise OnDemandPluginRuntimeDeclined("plugin_runtime_mode_mismatch")
    if runtime_lock.computed_digest() != runtime_lock.digest:
        raise OnDemandPluginRuntimeDeclined("plugin_runtime_lock_invalid")

    plugins = runtime_lock.plugins
    keys = {plugin.plugin_key for plugin in plugins}
    distributions = {str(canonicalize_name(plugin.distribution_name)) for plugin in plugins}
    packages = {plugin.top_level_package for plugin in plugins}
    plugin_ids = {plugin.plugin_id for plugin in plugins}
    version_ids = {plugin.plugin_version_id for plugin in plugins}
    if not (len(keys) == len(distributions) == len(packages) == len(plugin_ids) == len(version_ids) == len(plugins)):
        raise OnDemandPluginRuntimeDeclined("plugin_runtime_lock_invalid")

    locked_distributions: dict[str, tuple[str, str]] = {}
    seen_distribution_names: set[str] = set()
    for distribution in runtime_lock.distributions:
        name = str(canonicalize_name(distribution.distribution_name))
        if name in seen_distribution_names:
            raise OnDemandPluginRuntimeDeclined("plugin_runtime_lock_invalid")
        seen_distribution_names.add(name)
        if distribution.source == "artifact":
            assert distribution.artifact_digest is not None
            locked_distributions[name] = (distribution.version, distribution.artifact_digest)

    expected = {
        str(canonicalize_name(plugin.distribution_name)): (plugin.version, plugin.wheel_digest) for plugin in plugins
    }
    if locked_distributions != expected:
        raise OnDemandPluginRuntimeDeclined("plugin_runtime_lock_invalid")
    return plugins


def _verify_loaded_plugins(
    plugins: tuple[LockedPlugin, ...],
    *,
    catalog: HarnessPluginFactoryCatalog,
    runtime_root: Path,
    site_packages: Path,
) -> tuple[_LoadedPlugin, ...]:
    registrations = {registration.plugin_key: registration for registration in catalog.registrations}
    if len(registrations) != len(plugins):
        raise ValueError("Plugin factory catalog has incomplete provenance")

    resolved_site_packages = site_packages.resolve(strict=True)
    result: list[_LoadedPlugin] = []
    for plugin in plugins:
        registration = registrations.get(plugin.plugin_key)
        if (
            registration is None
            or registration.distribution_name is None
            or str(canonicalize_name(registration.distribution_name))
            != str(canonicalize_name(plugin.distribution_name))
            or registration.distribution_version != plugin.version
            or not (
                registration.class_module == plugin.top_level_package
                or registration.class_module.startswith(f"{plugin.top_level_package}.")
            )
        ):
            raise ValueError("Plugin factory provenance does not match the Runtime lock")
        module = sys.modules.get(registration.class_module)
        module_file = getattr(module, "__file__", None)
        if not isinstance(module_file, str):
            raise ValueError("Plugin factory module has no filesystem provenance")
        try:
            resolved_module = Path(module_file).resolve(strict=True)
        except OSError as error:
            raise ValueError("Plugin factory module provenance is unavailable") from error
        if not resolved_module.is_relative_to(resolved_site_packages):
            raise ValueError("Plugin factory module came from outside the Runtime lock")

        result.append(
            _LoadedPlugin(
                locked=plugin,
                provenance=LoadedPluginProvenance(
                    plugin_key=plugin.plugin_key,
                    plugin_version_id=plugin.plugin_version_id,
                    distribution_name=str(canonicalize_name(plugin.distribution_name)),
                    distribution_version=plugin.version,
                    top_level_package=plugin.top_level_package,
                    wheel_digest=plugin.wheel_digest,
                    runtime_root=runtime_root,
                ),
                registration=registration,
                factory=catalog.require(plugin.plugin_key),
            )
        )
    return tuple(result)


def _module_is_loaded(top_level_package: str) -> bool:
    prefix = f"{top_level_package}."
    return any(name == top_level_package or name.startswith(prefix) for name in sys.modules)
