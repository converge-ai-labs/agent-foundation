"""Worker-local materialization of immutable Plugin Runtime locks."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from functools import partial
from pathlib import Path

import anyio
from anyio import CapacityLimiter, fail_after, to_thread
from packaging.utils import canonicalize_name

from a13n_service.storage.filesystem import prepare_root

from .artifact import inspect_distribution_wheel, inspect_plugin_wheel
from .errors import PluginError
from .objects import PluginObjectStore
from .runtime import LockedDistribution, LockedPlugin, PluginRuntimeLock, WorkerReleaseManifest

_MANIFEST_NAME = "runtime-lock.json"
_SITE_PACKAGES_NAME = "site-packages"
_WHEELS_NAME = "wheels"


class PluginRuntimeMaterializationError(Exception):
    """Bounded worker-preflight failure for one exact Runtime lock."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class MaterializedPluginRuntime:
    runtime_lock_digest: str
    root: Path
    site_packages: Path
    manifest_path: Path


class PluginRuntimeMaterializer:
    """Build one verified, read-only local directory from shared artifact authority."""

    def __init__(
        self,
        root: Path,
        objects: PluginObjectStore,
        worker_manifest: WorkerReleaseManifest,
        *,
        executable: str = "uv",
        max_wheel_bytes: int,
        max_expanded_bytes: int,
        max_archive_members: int,
        max_runtime_bytes: int,
        timeout_seconds: float = 120,
        limiter: CapacityLimiter | None = None,
    ) -> None:
        if not executable or "\x00" in executable:
            raise ValueError("Runtime materializer executable must be non-empty")
        if min(max_wheel_bytes, max_expanded_bytes, max_archive_members, max_runtime_bytes) <= 0:
            raise ValueError("Runtime materializer bounds must be positive")
        if timeout_seconds <= 0 or timeout_seconds > 900:
            raise ValueError("Runtime materializer timeout must be between zero and 900 seconds")
        self._root = root
        self._locks_root = root / "locks"
        self._temporary_root = root / "tmp"
        self._objects = objects
        self._worker_manifest = worker_manifest
        self._executable = executable
        self._max_wheel_bytes = max_wheel_bytes
        self._max_expanded_bytes = max_expanded_bytes
        self._max_archive_members = max_archive_members
        self._max_runtime_bytes = max_runtime_bytes
        self._timeout_seconds = timeout_seconds
        self._limiter = limiter
        self._materialization_locks: dict[str, anyio.Lock] = {}

    @classmethod
    async def create(
        cls,
        files_root: Path,
        objects: PluginObjectStore,
        worker_manifest: WorkerReleaseManifest,
        *,
        executable: str = "uv",
        max_wheel_bytes: int,
        max_expanded_bytes: int,
        max_archive_members: int,
        max_runtime_bytes: int,
        timeout_seconds: float = 120,
        limiter: CapacityLimiter | None = None,
    ) -> PluginRuntimeMaterializer:
        root = await prepare_root(files_root / "plugin-runtime-cache-v1", create=True, limiter=limiter)
        instance = cls(
            root,
            objects,
            worker_manifest,
            executable=executable,
            max_wheel_bytes=max_wheel_bytes,
            max_expanded_bytes=max_expanded_bytes,
            max_archive_members=max_archive_members,
            max_runtime_bytes=max_runtime_bytes,
            timeout_seconds=timeout_seconds,
            limiter=limiter,
        )
        await to_thread.run_sync(instance._prepare_layout, limiter=limiter)
        return instance

    async def materialize(self, runtime_lock: PluginRuntimeLock) -> MaterializedPluginRuntime:
        self._validate_lock(runtime_lock)
        lock = self._materialization_locks.setdefault(runtime_lock.digest, anyio.Lock())
        async with lock:
            destination = self._locks_root / runtime_lock.digest
            if await to_thread.run_sync(destination.exists, limiter=self._limiter):
                return await self._require_existing(runtime_lock, destination)

            temporary = Path(
                await to_thread.run_sync(
                    partial(tempfile.mkdtemp, prefix=f"{runtime_lock.digest}.", dir=self._temporary_root),
                    limiter=self._limiter,
                )
            )
            try:
                site_packages = temporary / _SITE_PACKAGES_NAME
                wheels = temporary / _WHEELS_NAME
                await to_thread.run_sync(site_packages.mkdir, limiter=self._limiter)
                await to_thread.run_sync(wheels.mkdir, limiter=self._limiter)
                wheel_paths = await self._prepare_wheels(runtime_lock, wheels)
                if wheel_paths:
                    await self._install_wheels(wheel_paths, site_packages)
                await to_thread.run_sync(shutil.rmtree, wheels, limiter=self._limiter)
                manifest_path = temporary / _MANIFEST_NAME
                await _write_private_file(manifest_path, _manifest_bytes(runtime_lock), self._limiter)
                await self._verify_runtime_size(temporary)
                try:
                    await to_thread.run_sync(os.replace, temporary, destination, limiter=self._limiter)
                except OSError as error:
                    if await to_thread.run_sync(destination.exists, limiter=self._limiter):
                        return await self._require_existing(runtime_lock, destination)
                    raise PluginRuntimeMaterializationError("plugin_runtime_materialization_failed") from error
                await to_thread.run_sync(_make_read_only, destination, limiter=self._limiter)
                return _materialized(runtime_lock.digest, destination)
            except PluginRuntimeMaterializationError:
                raise
            except (OSError, PluginError, ValueError) as error:
                raise PluginRuntimeMaterializationError("plugin_runtime_materialization_failed") from error
            finally:
                if await to_thread.run_sync(temporary.exists, limiter=self._limiter):
                    await to_thread.run_sync(shutil.rmtree, temporary, True, limiter=self._limiter)

    def _prepare_layout(self) -> None:
        for directory in (self._locks_root, self._temporary_root):
            directory.mkdir(mode=0o700, exist_ok=True)
            mode = directory.lstat().st_mode
            if not stat.S_ISDIR(mode) or stat.S_ISLNK(mode):
                raise ValueError("Plugin Runtime cache layout is invalid")
            os.chmod(directory, 0o700)

    def _validate_lock(self, runtime_lock: PluginRuntimeLock) -> None:
        if runtime_lock.computed_digest() != runtime_lock.digest:
            raise PluginRuntimeMaterializationError("plugin_runtime_lock_invalid")
        if runtime_lock.runtime_target != self._worker_manifest.runtime_target:
            raise PluginRuntimeMaterializationError("plugin_runtime_target_mismatch")
        if runtime_lock.harness_version != self._worker_manifest.harness_version:
            raise PluginRuntimeMaterializationError("plugin_runtime_harness_mismatch")
        for distribution in runtime_lock.distributions:
            if distribution.source != "worker_release":
                continue
            if self._worker_manifest.distribution_version(distribution.distribution_name) != distribution.version:
                raise PluginRuntimeMaterializationError("plugin_worker_dependency_missing")
        artifact_distributions = {
            str(canonicalize_name(item.distribution_name)): item
            for item in runtime_lock.distributions
            if item.source == "artifact"
        }
        for plugin in runtime_lock.plugins:
            distribution = artifact_distributions.get(str(canonicalize_name(plugin.distribution_name)))
            if (
                distribution is None
                or distribution.version != plugin.version
                or distribution.artifact_digest != plugin.wheel_digest
            ):
                raise PluginRuntimeMaterializationError("plugin_runtime_lock_invalid")

    async def _prepare_wheels(self, runtime_lock: PluginRuntimeLock, root: Path) -> tuple[Path, ...]:
        plugins = {
            (str(canonicalize_name(item.distribution_name)), item.wheel_digest): item for item in runtime_lock.plugins
        }
        result: list[Path] = []
        total = 0
        for distribution in runtime_lock.distributions:
            if distribution.source != "artifact":
                continue
            assert distribution.artifact_digest is not None and distribution.artifact_ref is not None
            path = root / f"{distribution.artifact_digest}.download"
            size = await self._download(distribution, path)
            total += size
            if total > self._max_runtime_bytes:
                raise PluginRuntimeMaterializationError("plugin_runtime_capacity_exceeded")
            plugin = plugins.get((str(canonicalize_name(distribution.distribution_name)), distribution.artifact_digest))
            if plugin is None:
                wheel_tags = await self._inspect_dependency(distribution, path)
            else:
                wheel_tags = await self._inspect_plugin(plugin, distribution, path)
            install_path = root / _wheel_install_filename(distribution, wheel_tags)
            await to_thread.run_sync(os.replace, path, install_path, limiter=self._limiter)
            result.append(install_path)
        return tuple(result)

    async def _download(self, distribution: LockedDistribution, destination: Path) -> int:
        assert distribution.artifact_digest is not None and distribution.artifact_ref is not None
        digest = hashlib.sha256()
        size = 0
        try:
            async with self._objects.open_verified(
                artifact_ref=distribution.artifact_ref,
                content_digest=distribution.artifact_digest,
            ) as reader:
                if reader.info.size > self._max_wheel_bytes:
                    raise PluginRuntimeMaterializationError("plugin_runtime_capacity_exceeded")
                target = await anyio.open_file(destination, "xb", limiter=self._limiter)
                try:
                    await to_thread.run_sync(os.chmod, destination, 0o600, limiter=self._limiter)
                    async for chunk in reader:
                        size += len(chunk)
                        if size > self._max_wheel_bytes:
                            raise PluginRuntimeMaterializationError("plugin_runtime_capacity_exceeded")
                        digest.update(chunk)
                        await target.write(chunk)
                    await target.flush()
                finally:
                    await target.aclose()
                if size != reader.info.size or digest.hexdigest() != distribution.artifact_digest:
                    raise PluginRuntimeMaterializationError("plugin_artifact_invalid")
                return size
        except PluginRuntimeMaterializationError:
            raise
        except PluginError as error:
            raise PluginRuntimeMaterializationError("plugin_artifact_unavailable") from error

    async def _inspect_plugin(
        self,
        plugin: LockedPlugin,
        distribution: LockedDistribution,
        path: Path,
    ) -> tuple[str, ...]:
        inspected = await inspect_plugin_wheel(
            path,
            max_expanded_bytes=self._max_expanded_bytes,
            max_members=self._max_archive_members,
            limiter=self._limiter,
        )
        if (
            inspected.plugin_key != plugin.plugin_key
            or str(canonicalize_name(inspected.distribution_name))
            != str(canonicalize_name(distribution.distribution_name))
            or inspected.version != distribution.version
            or inspected.top_level_package != plugin.top_level_package
        ):
            raise PluginRuntimeMaterializationError("plugin_artifact_invalid")
        return inspected.wheel_tags

    async def _inspect_dependency(self, distribution: LockedDistribution, path: Path) -> tuple[str, ...]:
        inspected = await inspect_distribution_wheel(
            path,
            max_expanded_bytes=self._max_expanded_bytes,
            max_members=self._max_archive_members,
            limiter=self._limiter,
        )
        if (
            str(canonicalize_name(inspected.distribution_name))
            != str(canonicalize_name(distribution.distribution_name))
            or inspected.version != distribution.version
        ):
            raise PluginRuntimeMaterializationError("plugin_artifact_invalid")
        return inspected.wheel_tags

    async def _install_wheels(self, wheel_paths: tuple[Path, ...], site_packages: Path) -> None:
        command = [
            self._executable,
            "--no-config",
            "--no-cache",
            "pip",
            "install",
            "--target",
            str(site_packages),
            "--no-index",
            "--no-deps",
            "--python",
            sys.executable,
            "--compile-bytecode",
            "--no-python-downloads",
            "--no-progress",
            "--color",
            "never",
            *(str(path) for path in wheel_paths),
        ]
        environment = _uv_environment(self._root)
        try:
            with fail_after(self._timeout_seconds):
                completed = await anyio.run_process(
                    command,
                    check=False,
                    cwd=self._root,
                    env=environment,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
        except (FileNotFoundError, PermissionError, TimeoutError) as error:
            raise PluginRuntimeMaterializationError("plugin_runtime_materialization_unavailable") from error
        if completed.returncode != 0:
            raise PluginRuntimeMaterializationError("plugin_runtime_materialization_failed")

    async def _verify_runtime_size(self, root: Path) -> None:
        size = await to_thread.run_sync(_directory_size, root, self._max_runtime_bytes, limiter=self._limiter)
        if size > self._max_runtime_bytes:
            raise PluginRuntimeMaterializationError("plugin_runtime_capacity_exceeded")

    async def _require_existing(
        self,
        runtime_lock: PluginRuntimeLock,
        destination: Path,
    ) -> MaterializedPluginRuntime:
        expected = _manifest_bytes(runtime_lock)
        try:
            actual = await to_thread.run_sync(
                _read_existing_manifest,
                destination,
                len(expected),
                limiter=self._limiter,
            )
        except (OSError, ValueError) as error:
            raise PluginRuntimeMaterializationError("plugin_runtime_cache_invalid") from error
        if actual != expected:
            raise PluginRuntimeMaterializationError("plugin_runtime_cache_invalid")
        return _materialized(runtime_lock.digest, destination)


def _materialized(digest: str, root: Path) -> MaterializedPluginRuntime:
    return MaterializedPluginRuntime(
        runtime_lock_digest=digest,
        root=root,
        site_packages=root / _SITE_PACKAGES_NAME,
        manifest_path=root / _MANIFEST_NAME,
    )


def _manifest_bytes(runtime_lock: PluginRuntimeLock) -> bytes:
    return (
        json.dumps(
            runtime_lock.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
        + b"\n"
    )


def _wheel_install_filename(distribution: LockedDistribution, wheel_tags: tuple[str, ...]) -> str:
    if not wheel_tags:
        raise PluginRuntimeMaterializationError("plugin_artifact_invalid")
    distribution_name = re.sub(r"[-_.]+", "_", distribution.distribution_name)
    version = re.sub(r"[^\w\d.]+", "_", distribution.version)
    return f"{distribution_name}-{version}-{wheel_tags[0]}.whl"


async def _write_private_file(path: Path, value: bytes, limiter: CapacityLimiter | None) -> None:
    target = await anyio.open_file(path, "xb", limiter=limiter)
    try:
        await to_thread.run_sync(os.chmod, path, 0o600, limiter=limiter)
        await target.write(value)
        await target.flush()
    finally:
        await target.aclose()


def _uv_environment(root: Path) -> dict[str, str]:
    allowed = {
        "PATH",
        "TMPDIR",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
    }
    environment = {key: value for key, value in os.environ.items() if key in allowed}
    environment["HOME"] = str(root)
    environment["UV_NO_CACHE"] = "true"
    return environment


def _directory_size(root: Path, limit: int) -> int:
    total = 0
    for current, directories, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        if stat.S_ISLNK(current_path.lstat().st_mode):
            raise ValueError("Plugin Runtime directory crosses a symlink")
        for name in (*directories, *files):
            path = current_path / name
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                raise ValueError("Plugin Runtime directory contains a symlink")
            if stat.S_ISREG(metadata.st_mode):
                total += metadata.st_size
                if total > limit:
                    return total
    return total


def _make_read_only(root: Path) -> None:
    for current, directories, files in os.walk(root, topdown=False, followlinks=False):
        current_path = Path(current)
        for name in files:
            path = current_path / name
            metadata = path.lstat()
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError("Plugin Runtime contains a non-regular file")
            os.chmod(path, 0o444)
        for name in directories:
            path = current_path / name
            metadata = path.lstat()
            if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                raise ValueError("Plugin Runtime contains an invalid directory")
            os.chmod(path, 0o555)
        os.chmod(current_path, 0o555)


def _read_existing_manifest(root: Path, expected_size: int) -> bytes:
    metadata = root.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)
    ):
        raise ValueError("Plugin Runtime cache entry is invalid")
    site_packages = root / _SITE_PACKAGES_NAME
    site_metadata = site_packages.lstat()
    if (
        not stat.S_ISDIR(site_metadata.st_mode)
        or stat.S_ISLNK(site_metadata.st_mode)
        or site_metadata.st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)
    ):
        raise ValueError("Plugin Runtime site-packages is invalid")
    manifest = root / _MANIFEST_NAME
    manifest_metadata = manifest.lstat()
    if (
        not stat.S_ISREG(manifest_metadata.st_mode)
        or manifest_metadata.st_size != expected_size
        or manifest_metadata.st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)
    ):
        raise ValueError("Plugin Runtime manifest is invalid")
    with manifest.open("rb") as source:
        return source.read(expected_size + 1)
