"""Fresh-process validation for one materialized Plugin Runtime."""

from __future__ import annotations

import importlib
import json
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

from a13n_harness.plugin_factories import HarnessPluginFactoryCatalog, build_harness_plugin_factory_catalog
from packaging.utils import canonicalize_name
from pydantic import ValidationError

from .runtime import PluginRuntimeLock

_MAX_MANIFEST_BYTES = 8 * 1024 * 1024


class PluginRunnerBootstrapError(Exception):
    """Bounded failure before a Runner becomes ready."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class BootstrappedPluginRuntime:
    runtime_lock: PluginRuntimeLock
    factory_catalog: HarnessPluginFactoryCatalog


def bootstrap_materialized_runtime(
    runtime_root: Path,
    *,
    expected_digest: str,
) -> BootstrappedPluginRuntime:
    """Load and verify selected factories exactly once in a fresh Runner process."""

    root = _require_directory(runtime_root)
    manifest_path = _require_file(root, "runtime-lock.json")
    site_packages = _require_directory(root / "site-packages", parent=root)
    try:
        raw = manifest_path.read_bytes()
    except OSError as error:
        raise PluginRunnerBootstrapError("plugin_runtime_manifest_invalid") from error
    if not raw or len(raw) > _MAX_MANIFEST_BYTES:
        raise PluginRunnerBootstrapError("plugin_runtime_manifest_invalid")
    try:
        runtime_lock = PluginRuntimeLock.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValidationError) as error:
        raise PluginRunnerBootstrapError("plugin_runtime_manifest_invalid") from error
    if (
        runtime_lock.digest != expected_digest
        or runtime_lock.computed_digest() != expected_digest
        or root.name != expected_digest
    ):
        raise PluginRunnerBootstrapError("plugin_runtime_lock_invalid")
    for plugin in runtime_lock.plugins:
        if plugin.top_level_package in sys.modules:
            raise PluginRunnerBootstrapError("plugin_runtime_already_imported")

    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(site_packages))
    importlib.invalidate_caches()
    try:
        catalog = build_harness_plugin_factory_catalog(
            plugin_keys=tuple(plugin.plugin_key for plugin in runtime_lock.plugins)
        )
    except Exception as error:
        raise PluginRunnerBootstrapError("plugin_factory_load_failed") from error
    registrations = {registration.plugin_key: registration for registration in catalog.registrations}
    if len(registrations) != len(runtime_lock.plugins):
        raise PluginRunnerBootstrapError("plugin_factory_provenance_mismatch")
    for plugin in runtime_lock.plugins:
        registration = registrations.get(plugin.plugin_key)
        if (
            registration is None
            or registration.distribution_name is None
            or str(canonicalize_name(registration.distribution_name))
            != str(canonicalize_name(plugin.distribution_name))
            or registration.distribution_version != plugin.version
        ):
            raise PluginRunnerBootstrapError("plugin_factory_provenance_mismatch")
    return BootstrappedPluginRuntime(runtime_lock=runtime_lock, factory_catalog=catalog)


def _require_directory(path: Path, *, parent: Path | None = None) -> Path:
    try:
        resolved = path.resolve(strict=True)
        metadata = path.lstat()
    except OSError as error:
        raise PluginRunnerBootstrapError("plugin_runtime_cache_invalid") from error
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)
        or (parent is not None and not resolved.is_relative_to(parent))
    ):
        raise PluginRunnerBootstrapError("plugin_runtime_cache_invalid")
    return resolved


def _require_file(root: Path, name: str) -> Path:
    path = root / name
    try:
        resolved = path.resolve(strict=True)
        metadata = path.lstat()
    except OSError as error:
        raise PluginRunnerBootstrapError("plugin_runtime_cache_invalid") from error
    if (
        not resolved.is_relative_to(root)
        or not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)
    ):
        raise PluginRunnerBootstrapError("plugin_runtime_cache_invalid")
    return resolved


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(2)
    try:
        bootstrap_materialized_runtime(Path(sys.argv[1]), expected_digest=sys.argv[2])
    except PluginRunnerBootstrapError:
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
