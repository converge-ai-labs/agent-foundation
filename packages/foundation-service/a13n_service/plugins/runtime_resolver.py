"""Package-index resolution and artifact retention for runner Plugin Runtime candidates."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Protocol
from urllib.parse import SplitResult, unquote, urlsplit

import anyio
import httpx2
from a13n_harness import SafeFailure
from anyio import CapacityLimiter, fail_after, to_thread
from packaging.markers import default_environment
from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import SpecifierSet
from packaging.tags import Tag, parse_tag, sys_tags
from packaging.utils import InvalidWheelFilename, canonicalize_name, parse_wheel_filename
from packaging.version import InvalidVersion, Version
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.storage import short_session, transaction
from a13n_service.storage.filesystem import prepare_root
from a13n_service.temporal import utc_now

from .artifact import inspect_distribution_wheel
from .commands import (
    PluginRuntimeCatalogSnapshot,
    PluginRuntimeCommand,
    PluginRuntimeCommandFailure,
    PluginRuntimeVersionSpec,
)
from .errors import PluginError
from .models import PluginRuntimeResolutionRecord
from .objects import PluginObjectStore
from .runtime import (
    LockedDistribution,
    PluginRuntimeLock,
    PluginRuntimeLockError,
    PluginRuntimeLockStore,
)
from .staging import PluginStaging

_MAX_REQUIREMENTS = 512
_MAX_REQUIREMENTS_BYTES = 256 * 1024
_MAX_LOCK_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ResolvedDependencyWheel:
    distribution_name: str
    version: str
    source_url: str
    filename: str
    size_bytes: int
    content_digest: str


class RuntimeDependencyResolver(Protocol):
    async def resolve(
        self,
        *,
        requirements: Sequence[str],
        preferences: Mapping[str, str],
        constraints: Mapping[str, str],
    ) -> tuple[ResolvedDependencyWheel, ...]: ...


class RuntimeDependencyArtifactRetainer(Protocol):
    async def retain(self, wheel: ResolvedDependencyWheel) -> LockedDistribution: ...


class _UvHashes(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class _UvWheel(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    url: str = Field(min_length=1, max_length=4096)
    size: int = Field(gt=0)
    hashes: _UvHashes


class _UvPackage(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    name: str = Field(min_length=1, max_length=256)
    version: str = Field(min_length=1, max_length=256)
    wheels: tuple[_UvWheel, ...] = Field(min_length=1)


class _UvLock(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    lock_version: str = Field(alias="lock-version")
    packages: tuple[_UvPackage, ...]


class UvRuntimeDependencyResolver:
    """Resolve exact binary Wheels with uv without importing package-manager internals."""

    def __init__(
        self,
        work_root: Path,
        *,
        executable: str = "uv",
        default_index_url: str = "https://pypi.org/simple",
        index_urls: Sequence[str] = (),
        timeout_seconds: float = 120,
        max_packages: int = 512,
        compatible_tags: Sequence[Tag] | None = None,
        limiter: CapacityLimiter | None = None,
    ) -> None:
        if not executable or "\x00" in executable:
            raise ValueError("uv resolver executable must be non-empty")
        if timeout_seconds <= 0 or timeout_seconds > 900:
            raise ValueError("resolver timeout must be between zero and 900 seconds")
        if max_packages <= 0 or max_packages > 4096:
            raise ValueError("resolver package limit must be between one and 4096")
        self._work_root = work_root
        self._executable = executable
        self._default_index_url = _validated_index_url(default_index_url)
        self._index_urls = tuple(_validated_index_url(value) for value in index_urls)
        self._timeout_seconds = timeout_seconds
        self._max_packages = max_packages
        tags = tuple(compatible_tags) if compatible_tags is not None else tuple(sys_tags())
        self._tag_rank = {tag: index for index, tag in enumerate(tags)}
        self._limiter = limiter

    @classmethod
    async def create(
        cls,
        files_root: Path,
        *,
        executable: str = "uv",
        default_index_url: str = "https://pypi.org/simple",
        index_urls: Sequence[str] = (),
        timeout_seconds: float = 120,
        max_packages: int = 512,
        compatible_tags: Sequence[Tag] | None = None,
        limiter: CapacityLimiter | None = None,
    ) -> UvRuntimeDependencyResolver:
        root = await prepare_root(files_root / "plugin-runtime-resolver-v1", create=True, limiter=limiter)
        return cls(
            root,
            executable=executable,
            default_index_url=default_index_url,
            index_urls=index_urls,
            timeout_seconds=timeout_seconds,
            max_packages=max_packages,
            compatible_tags=compatible_tags,
            limiter=limiter,
        )

    async def resolve(
        self,
        *,
        requirements: Sequence[str],
        preferences: Mapping[str, str],
        constraints: Mapping[str, str],
    ) -> tuple[ResolvedDependencyWheel, ...]:
        normalized_requirements = _normalized_requirements(requirements)
        if not normalized_requirements:
            return ()
        work_dir = Path(
            await to_thread.run_sync(
                partial(tempfile.mkdtemp, prefix="resolve-", dir=self._work_root),
                limiter=self._limiter,
            )
        )
        try:
            requirements_path = work_dir / "requirements.in"
            resolved_path = work_dir / "resolved.txt"
            constraints_path = work_dir / "constraints.txt"
            lock_path = work_dir / "pylock.runtime.toml"
            await _write_private_text(requirements_path, "\n".join(normalized_requirements) + "\n", self._limiter)
            await _write_private_text(resolved_path, _pins(preferences), self._limiter)
            await _write_private_text(constraints_path, _pins(constraints), self._limiter)
            await self._run_compile(
                source=requirements_path,
                output=resolved_path,
                constraints=constraints_path,
                output_format="requirements.txt",
                no_dependencies=False,
            )
            await self._run_compile(
                source=resolved_path,
                output=lock_path,
                constraints=constraints_path,
                output_format="pylock.toml",
                no_dependencies=True,
            )
            raw_lock = await _read_bounded(lock_path, _MAX_LOCK_BYTES, self._limiter)
            return self._parse_lock(raw_lock)
        finally:
            await to_thread.run_sync(shutil.rmtree, work_dir, True, limiter=self._limiter)

    async def _run_compile(
        self,
        *,
        source: Path,
        output: Path,
        constraints: Path,
        output_format: str,
        no_dependencies: bool,
    ) -> None:
        command = [
            self._executable,
            "--no-config",
            "pip",
            "compile",
            str(source),
            "--output-file",
            str(output),
            "--format",
            output_format,
            "--constraints",
            str(constraints),
            "--only-binary",
            ":all:",
            "--python",
            sys.executable,
            "--no-header",
            "--no-annotate",
            "--no-sources",
            "--index-strategy",
            "first-index",
            "--keyring-provider",
            "disabled",
            "--no-python-downloads",
            "--no-progress",
            "--color",
            "never",
        ]
        if no_dependencies:
            command.append("--no-deps")
        try:
            with fail_after(self._timeout_seconds):
                completed = await anyio.run_process(
                    command,
                    check=False,
                    cwd=self._work_root,
                    env=self._process_environment(),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
        except (FileNotFoundError, PermissionError, TimeoutError) as error:
            raise _command_failure(
                "plugin_runtime_incompatible", "Plugin dependency resolution is unavailable."
            ) from error
        if completed.returncode != 0:
            raise _command_failure("plugin_dependency_conflict", "Plugin dependencies cannot be resolved.")

    def _process_environment(self) -> dict[str, str]:
        allowed = {
            "PATH",
            "TMPDIR",
            "SSL_CERT_FILE",
            "SSL_CERT_DIR",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "NO_PROXY",
            "http_proxy",
            "https_proxy",
            "no_proxy",
        }
        environment = {key: value for key, value in os.environ.items() if key in allowed}
        environment["HOME"] = str(self._work_root)
        environment["UV_NO_CACHE"] = "true"
        environment["UV_DEFAULT_INDEX"] = self._default_index_url
        if self._index_urls:
            environment["UV_INDEX"] = " ".join(self._index_urls)
        return environment

    def _parse_lock(self, raw: bytes) -> tuple[ResolvedDependencyWheel, ...]:
        try:
            parsed = _UvLock.model_validate(tomllib.loads(raw.decode("utf-8")))
        except (UnicodeDecodeError, tomllib.TOMLDecodeError, ValidationError) as error:
            raise _command_failure(
                "plugin_runtime_incompatible", "Plugin dependency resolution was invalid."
            ) from error
        if parsed.lock_version != "1.0" or len(parsed.packages) > self._max_packages:
            raise _command_failure("plugin_runtime_incompatible", "Plugin dependency resolution exceeded its contract.")
        resolved: list[ResolvedDependencyWheel] = []
        names: set[str] = set()
        for package in parsed.packages:
            name = str(canonicalize_name(package.name))
            if name in names:
                raise _command_failure("plugin_runtime_incompatible", "Plugin dependency resolution was ambiguous.")
            names.add(name)
            try:
                version = str(Version(package.version))
            except InvalidVersion as error:
                raise _command_failure(
                    "plugin_runtime_incompatible", "Plugin dependency resolution was invalid."
                ) from error
            resolved.append(self._select_wheel(name=name, version=version, wheels=package.wheels))
        return tuple(sorted(resolved, key=lambda item: item.distribution_name))

    def _select_wheel(
        self,
        *,
        name: str,
        version: str,
        wheels: Sequence[_UvWheel],
    ) -> ResolvedDependencyWheel:
        candidates: list[tuple[int, str, _UvWheel]] = []
        for wheel in wheels:
            parsed_url = urlsplit(wheel.url)
            filename = unquote(Path(parsed_url.path).name)
            if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc or not filename:
                continue
            try:
                wheel_name, wheel_version, _build, wheel_tags = parse_wheel_filename(filename)
            except InvalidWheelFilename:
                continue
            if str(canonicalize_name(wheel_name)) != name or str(wheel_version) != version:
                continue
            ranks = [self._tag_rank[tag] for tag in wheel_tags if tag in self._tag_rank]
            if ranks:
                candidates.append((min(ranks), filename, wheel))
        if not candidates:
            raise _command_failure("plugin_runtime_incompatible", "No compatible Plugin dependency Wheel exists.")
        _rank, filename, wheel = min(candidates, key=lambda item: (item[0], item[1]))
        return ResolvedDependencyWheel(
            distribution_name=name,
            version=version,
            source_url=wheel.url,
            filename=filename,
            size_bytes=wheel.size,
            content_digest=wheel.hashes.sha256,
        )


class HttpRuntimeDependencyArtifactRetainer:
    """Download, inspect, and retain one uv-selected Wheel by content identity."""

    def __init__(
        self,
        http_client: httpx2.AsyncClient,
        staging: PluginStaging,
        objects: PluginObjectStore,
        *,
        max_wheel_bytes: int,
        max_expanded_bytes: int,
        max_archive_members: int,
        index_urls: Sequence[str] = (),
        compatible_tags: Sequence[Tag] | None = None,
        python_version: Version | None = None,
        limiter: CapacityLimiter | None = None,
    ) -> None:
        self._http_client = http_client
        self._staging = staging
        self._objects = objects
        self._max_wheel_bytes = max_wheel_bytes
        self._max_expanded_bytes = max_expanded_bytes
        self._max_archive_members = max_archive_members
        self._compatible_tags = frozenset(compatible_tags or sys_tags())
        self._python_version = python_version or Version(platform.python_version())
        self._index_auth = _index_authentication(index_urls)
        self._limiter = limiter

    async def retain(self, wheel: ResolvedDependencyWheel) -> LockedDistribution:
        if wheel.size_bytes > self._max_wheel_bytes:
            raise _command_failure("plugin_runtime_incompatible", "A Plugin dependency Wheel exceeds its limit.")
        staged = None
        try:
            async with self._http_client.stream(
                "GET",
                wheel.source_url,
                auth=self._request_auth(wheel.source_url),
                follow_redirects=True,
            ) as response:
                response.raise_for_status()
                content_length = _content_length(response.headers.get("content-length"))
                staged = await self._staging.stage(
                    response.aiter_bytes(),
                    max_size_bytes=self._max_wheel_bytes,
                    content_length=content_length,
                )
            if staged.size_bytes != wheel.size_bytes or staged.content_digest != wheel.content_digest:
                raise _command_failure(
                    "plugin_runtime_incompatible", "A Plugin dependency Wheel failed integrity checks."
                )
            inspected = await inspect_distribution_wheel(
                staged.path,
                max_expanded_bytes=self._max_expanded_bytes,
                max_members=self._max_archive_members,
                limiter=self._limiter,
            )
            if inspected.distribution_name != wheel.distribution_name or inspected.version != wheel.version:
                raise _command_failure("plugin_runtime_incompatible", "A Plugin dependency Wheel changed identity.")
            if inspected.requires_python is not None and self._python_version not in SpecifierSet(
                inspected.requires_python
            ):
                raise _command_failure("plugin_platform_incompatible", "A Plugin dependency Wheel is incompatible.")
            inspected_tags = frozenset(tag for value in inspected.wheel_tags for tag in parse_tag(value))
            if not inspected_tags.intersection(self._compatible_tags):
                raise _command_failure("plugin_runtime_incompatible", "A Plugin dependency Wheel is incompatible.")
            artifact_ref = await self._objects.publish(staged)
            return LockedDistribution(
                distribution_name=wheel.distribution_name,
                version=wheel.version,
                source="artifact",
                artifact_digest=wheel.content_digest,
                artifact_ref=artifact_ref,
            )
        except PluginRuntimeCommandFailure:
            raise
        except (PluginError, httpx2.HTTPError, ValueError) as error:
            raise _command_failure(
                "plugin_runtime_incompatible", "A Plugin dependency Wheel is unavailable."
            ) from error
        finally:
            if staged is not None:
                await staged.remove()

    def _request_auth(self, source_url: str) -> tuple[str, str] | None:
        parsed = urlsplit(source_url)
        if parsed.username is not None:
            return None
        return self._index_auth.get(_url_origin(parsed))


@dataclass(frozen=True, slots=True)
class _PluginContribution:
    plugin_id: str
    plugin_version_id: str
    plugin_key: str
    distribution_name: str
    version: str
    top_level_package: str
    wheel_digest: str
    artifact_ref: str
    requires_dist: tuple[str, ...]

    @classmethod
    def from_spec(cls, spec: PluginRuntimeVersionSpec) -> _PluginContribution:
        return cls(
            plugin_id=spec.plugin.id,
            plugin_version_id=spec.version.id,
            plugin_key=spec.plugin.plugin_key,
            distribution_name=spec.plugin.distribution_name,
            version=spec.version.version,
            top_level_package=spec.plugin.top_level_package,
            wheel_digest=spec.version.content_digest,
            artifact_ref=spec.version.artifact_ref,
            requires_dist=spec.version.requires_dist,
        )


class FoundationPluginRuntimeCandidateResolver:
    """Resolve one exact candidate and durably bind it to the command operation."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        runtime_locks: PluginRuntimeLockStore,
        dependency_resolver: RuntimeDependencyResolver,
        artifact_retainer: RuntimeDependencyArtifactRetainer,
        *,
        compatible_tags: Sequence[Tag] | None = None,
        python_version: Version | None = None,
        clock=None,
    ) -> None:
        self._sessions = sessions
        self._runtime_locks = runtime_locks
        self._dependency_resolver = dependency_resolver
        self._artifact_retainer = artifact_retainer
        self._compatible_tags = frozenset(compatible_tags or sys_tags())
        self._python_version = python_version or Version(platform.python_version())
        self._marker_environment = {key: str(value) for key, value in default_environment().items()}
        self._clock = clock or utc_now

    async def resolve_candidate(
        self,
        *,
        operation_id: str,
        command: PluginRuntimeCommand,
        catalog: PluginRuntimeCatalogSnapshot,
    ) -> PluginRuntimeLock:
        selected = _selected_versions(command, catalog)
        request_digest = _resolution_request_digest(command, catalog, selected)
        replay = await self._load_resolution(operation_id, request_digest)
        if replay is not None:
            return replay
        current = await self._current_lock(catalog.active_lock_digest)
        if current is not None and _plugin_identity(current) == _spec_identity(selected):
            return await self._persist_resolution(operation_id, request_digest, current)
        requirements = self._requirements(selected)
        plugin_names = {str(canonicalize_name(item.plugin.distribution_name)): item for item in selected}
        preferences = _artifact_preferences(current, excluded_names=frozenset(plugin_names))
        constraints = self._runtime_locks.manifest.distributions
        resolved = await self._dependency_resolver.resolve(
            requirements=requirements,
            preferences=preferences,
            constraints=constraints,
        )
        distributions: list[LockedDistribution] = []
        for item in resolved:
            plugin = plugin_names.get(item.distribution_name)
            if plugin is not None:
                if item.version != plugin.version.version:
                    raise _command_failure("plugin_dependency_conflict", "Plugin dependencies conflict.")
                continue
            worker_version = self._runtime_locks.manifest.distribution_version(item.distribution_name)
            if worker_version == item.version:
                distributions.append(
                    LockedDistribution(
                        distribution_name=item.distribution_name,
                        version=item.version,
                        source="worker_release",
                    )
                )
            else:
                distributions.append(await self._artifact_retainer.retain(item))
        contributions = tuple(_PluginContribution.from_spec(item) for item in selected)
        try:
            async with transaction(self._sessions) as session:
                concurrent = await self._load_resolution_in_session(session, operation_id, request_digest)
                if concurrent is not None:
                    return concurrent
                runtime_lock = await self._runtime_locks.build_and_persist(
                    session,
                    mode="runner",
                    plugins=contributions,
                    locked_distributions=distributions,
                )
                session.add(
                    PluginRuntimeResolutionRecord(
                        operation_id=operation_id,
                        request_digest=request_digest,
                        runtime_lock_digest=runtime_lock.digest,
                        created_at=self._clock(),
                    )
                )
                await session.flush()
                return runtime_lock
        except IntegrityError:
            replay = await self._load_resolution(operation_id, request_digest)
            if replay is not None:
                return replay
            raise
        except PluginRuntimeLockError as error:
            raise _command_failure(error.reason, "Plugin dependencies cannot be locked.") from error

    async def require_candidate(self, *, runtime_lock_digest: str) -> PluginRuntimeLock:
        async with short_session(self._sessions) as session:
            return await self._runtime_locks.require(session, runtime_lock_digest, mode="runner")

    async def _current_lock(self, digest: str | None) -> PluginRuntimeLock | None:
        if digest is None:
            return None
        return await self.require_candidate(runtime_lock_digest=digest)

    async def _persist_resolution(
        self,
        operation_id: str,
        request_digest: str,
        runtime_lock: PluginRuntimeLock,
    ) -> PluginRuntimeLock:
        try:
            async with transaction(self._sessions) as session:
                concurrent = await self._load_resolution_in_session(session, operation_id, request_digest)
                if concurrent is not None:
                    return concurrent
                session.add(
                    PluginRuntimeResolutionRecord(
                        operation_id=operation_id,
                        request_digest=request_digest,
                        runtime_lock_digest=runtime_lock.digest,
                        created_at=self._clock(),
                    )
                )
                await session.flush()
                return runtime_lock
        except IntegrityError:
            replay = await self._load_resolution(operation_id, request_digest)
            if replay is not None:
                return replay
            raise

    async def _load_resolution(self, operation_id: str, request_digest: str) -> PluginRuntimeLock | None:
        async with short_session(self._sessions) as session:
            return await self._load_resolution_in_session(session, operation_id, request_digest)

    async def _load_resolution_in_session(
        self,
        session: AsyncSession,
        operation_id: str,
        request_digest: str,
    ) -> PluginRuntimeLock | None:
        record = await session.get(PluginRuntimeResolutionRecord, operation_id)
        if record is None:
            return None
        if record.request_digest != request_digest:
            raise _command_failure("plugin_runtime_changed", "The Plugin Runtime command changed during resolution.")
        return await self._runtime_locks.require(session, record.runtime_lock_digest, mode="runner")

    def _requirements(self, selected: Sequence[PluginRuntimeVersionSpec]) -> tuple[str, ...]:
        plugin_versions = {
            str(canonicalize_name(item.plugin.distribution_name)): Version(item.version.version) for item in selected
        }
        requirements: list[str] = []
        for item in selected:
            self._validate_plugin_target(item)
            for raw in item.version.requires_dist:
                requirement = Requirement(raw)
                if requirement.marker is not None and not requirement.marker.evaluate(self._marker_environment):
                    continue
                if requirement.url is not None:
                    raise _command_failure(
                        "plugin_platform_incompatible", "Direct Plugin requirements are unsupported."
                    )
                plugin_version = plugin_versions.get(str(canonicalize_name(requirement.name)))
                if plugin_version is not None:
                    if requirement.specifier and plugin_version not in requirement.specifier:
                        raise _command_failure("plugin_dependency_conflict", "Plugin dependencies conflict.")
                    continue
                worker_version = self._runtime_locks.manifest.distribution_version(requirement.name)
                if worker_version is not None:
                    if requirement.specifier and Version(worker_version) not in requirement.specifier:
                        raise _command_failure("plugin_dependency_conflict", "Plugin dependencies conflict.")
                    continue
                requirements.append(str(requirement))
        return tuple(requirements)

    def _validate_plugin_target(self, item: PluginRuntimeVersionSpec) -> None:
        if item.requires_python is not None and self._python_version not in SpecifierSet(item.requires_python):
            raise _command_failure("plugin_platform_incompatible", "A PluginVersion is incompatible with Python.")
        wheel_tags = frozenset(tag for value in item.wheel_tags for tag in parse_tag(value))
        if not wheel_tags.intersection(self._compatible_tags):
            raise _command_failure("plugin_runtime_incompatible", "A PluginVersion has no compatible Wheel.")


def _selected_versions(
    command: PluginRuntimeCommand,
    catalog: PluginRuntimeCatalogSnapshot,
) -> tuple[PluginRuntimeVersionSpec, ...]:
    selected = {item.plugin.id: item for item in catalog.active_versions}
    if command == "activate":
        if catalog.target_version is None:
            raise _command_failure("plugin_version_not_found", "The PluginVersion was not found.")
        selected[catalog.target_plugin.id] = catalog.target_version
    else:
        selected.pop(catalog.target_plugin.id, None)
    return tuple(sorted(selected.values(), key=lambda item: (item.plugin.plugin_key, item.version.id)))


def _plugin_identity(runtime_lock: PluginRuntimeLock) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((item.plugin_id, item.plugin_version_id) for item in runtime_lock.plugins))


def _spec_identity(selected: Sequence[PluginRuntimeVersionSpec]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((item.plugin.id, item.version.id) for item in selected))


def _artifact_preferences(
    runtime_lock: PluginRuntimeLock | None,
    *,
    excluded_names: frozenset[str],
) -> dict[str, str]:
    if runtime_lock is None:
        return {}
    return {
        item.distribution_name: item.version
        for item in runtime_lock.distributions
        if item.source == "artifact" and item.distribution_name not in excluded_names
    }


def _resolution_request_digest(
    command: PluginRuntimeCommand,
    catalog: PluginRuntimeCatalogSnapshot,
    selected: Sequence[PluginRuntimeVersionSpec],
) -> str:
    payload = {
        "command": command,
        "runtime_generation": catalog.runtime_generation,
        "active_lock_digest": catalog.active_lock_digest,
        "plugins": [
            {
                "plugin_id": item.plugin.id,
                "plugin_version_id": item.version.id,
                "content_digest": item.version.content_digest,
                "requires_dist": list(item.version.requires_dist),
                "requires_python": item.requires_python,
                "wheel_tags": list(item.wheel_tags),
            }
            for item in selected
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def _normalized_requirements(values: Sequence[str]) -> tuple[str, ...]:
    if len(values) > _MAX_REQUIREMENTS:
        raise _command_failure("plugin_dependency_conflict", "Plugin requirements exceed their limit.")
    try:
        normalized = tuple(str(Requirement(value)) for value in values)
    except InvalidRequirement as error:
        raise _command_failure("plugin_dependency_conflict", "Plugin requirements are invalid.") from error
    if sum(len(value.encode("utf-8")) + 1 for value in normalized) > _MAX_REQUIREMENTS_BYTES:
        raise _command_failure("plugin_dependency_conflict", "Plugin requirements exceed their limit.")
    return normalized


def _pins(values: Mapping[str, str]) -> str:
    return "".join(f"{canonicalize_name(name)}=={Version(version)}\n" for name, version in sorted(values.items()))


async def _write_private_text(path: Path, value: str, limiter: CapacityLimiter | None) -> None:
    target = await anyio.open_file(path, "xb", limiter=limiter)
    try:
        await to_thread.run_sync(os.chmod, path, 0o600, limiter=limiter)
        await target.write(value.encode("utf-8"))
        await target.flush()
    finally:
        await target.aclose()


async def _read_bounded(path: Path, max_bytes: int, limiter: CapacityLimiter | None) -> bytes:
    async with await anyio.open_file(path, "rb", limiter=limiter) as source:
        value = await source.read(max_bytes + 1)
    if len(value) > max_bytes:
        raise _command_failure("plugin_runtime_incompatible", "Plugin dependency resolution exceeded its limit.")
    return value


def _content_length(raw: str | None) -> int | None:
    if raw is None:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value >= 0 else None


def _validated_index_url(value: str) -> str:
    if not value or len(value) > 4096 or any(character.isspace() or ord(character) < 32 for character in value):
        raise ValueError("package index URL must be non-empty")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
        raise ValueError("package index URL must use HTTP or HTTPS")
    try:
        _ = parsed.port
    except ValueError as error:
        raise ValueError("package index URL has an invalid port") from error
    return value


def _index_authentication(index_urls: Sequence[str]) -> dict[tuple[str, str, int | None], tuple[str, str]]:
    authentication: dict[tuple[str, str, int | None], tuple[str, str]] = {}
    for value in index_urls:
        parsed = urlsplit(_validated_index_url(value))
        if parsed.username is None:
            continue
        origin = _url_origin(parsed)
        credentials = (unquote(parsed.username), unquote(parsed.password or ""))
        existing = authentication.setdefault(origin, credentials)
        if existing != credentials:
            raise ValueError("package indexes for one origin must use the same credentials")
    return authentication


def _url_origin(value: SplitResult) -> tuple[str, str, int | None]:
    assert value.hostname is not None
    return value.scheme.lower(), value.hostname.lower(), value.port


def _command_failure(code: str, message: str) -> PluginRuntimeCommandFailure:
    return PluginRuntimeCommandFailure(SafeFailure(code=code, message=message))
