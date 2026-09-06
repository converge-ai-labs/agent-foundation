"""Canonical internal Plugin Runtime locks and Worker release baselines."""

from __future__ import annotations

import hashlib
import json
import platform
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from importlib.metadata import PackageNotFoundError, distributions, packages_distributions, version
from typing import Literal, Protocol

from packaging.requirements import Requirement
from packaging.tags import sys_tags
from packaging.utils import canonicalize_name
from packaging.version import Version
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.temporal import utc_now

from .models import PluginRecord, PluginRuntimeLockRecord, PluginRuntimeStateRecord

PluginRuntimeModeValue = Literal["on_demand", "runner"]
DistributionSource = Literal["worker_release", "artifact"]


class PluginRuntimeLockError(Exception):
    """Bounded internal failure translated by the owning operation."""

    def __init__(self, reason: str, *, path: str = "plugins") -> None:
        super().__init__(reason)
        self.reason = reason
        self.path = path


class RuntimeTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    python_implementation: str = Field(min_length=1, max_length=64)
    python_minor: str = Field(pattern=r"^[0-9]+\.[0-9]+$")
    operating_system: str = Field(min_length=1, max_length=128)
    cpu_architecture: str = Field(min_length=1, max_length=128)
    wheel_abi: str = Field(min_length=1, max_length=128)


class LockedDistribution(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    distribution_name: str = Field(min_length=1, max_length=256)
    version: str = Field(min_length=1, max_length=256)
    source: DistributionSource
    artifact_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    artifact_ref: str | None = Field(default=None, min_length=1, max_length=1024)

    @model_validator(mode="after")
    def validate_source_fields(self) -> LockedDistribution:
        if self.source == "artifact":
            if self.artifact_digest is None or self.artifact_ref is None:
                raise ValueError("artifact distributions require digest and reference")
        elif self.artifact_digest is not None or self.artifact_ref is not None:
            raise ValueError("Worker release distributions cannot carry artifact fields")
        return self


class LockedPlugin(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    plugin_id: str = Field(min_length=1, max_length=72)
    plugin_version_id: str = Field(min_length=1, max_length=72)
    plugin_key: str = Field(min_length=1, max_length=128)
    distribution_name: str = Field(min_length=1, max_length=256)
    version: str = Field(min_length=1, max_length=256)
    top_level_package: str = Field(min_length=1, max_length=256)
    wheel_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class PluginRuntimeLock(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1"] = "1"
    mode: PluginRuntimeModeValue
    runtime_target: RuntimeTarget
    worker_release: str = Field(min_length=1, max_length=256)
    harness_version: str = Field(min_length=1, max_length=256)
    plugins: tuple[LockedPlugin, ...] = ()
    distributions: tuple[LockedDistribution, ...] = ()
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    def computed_digest(self) -> str:
        return _canonical_digest(self.model_dump(mode="json", exclude={"digest"}))


@dataclass(frozen=True, slots=True)
class WorkerReleaseManifest:
    worker_release: str
    harness_version: str
    runtime_target: RuntimeTarget
    distributions: Mapping[str, str]

    def distribution_version(self, name: str) -> str | None:
        return self.distributions.get(canonicalize_name(name))


def installed_distribution_versions() -> dict[str, str]:
    """Snapshot normalized distribution versions present in this service release."""

    return dict(_installed_distribution_version_items())


@cache
def _installed_distribution_version_items() -> tuple[tuple[str, str], ...]:
    result: dict[str, str] = {}
    for distribution in distributions():
        distribution_name = distribution.metadata.get("Name")
        if distribution_name is None:
            continue
        normalized = str(canonicalize_name(distribution_name))
        result.setdefault(normalized, distribution.version)
    return tuple(result.items())


@cache
def installed_top_level_packages() -> frozenset[str]:
    """Snapshot import packages present in this service release."""

    return frozenset(packages_distributions())


class RuntimePluginContribution(Protocol):
    @property
    def plugin_id(self) -> str: ...

    @property
    def plugin_version_id(self) -> str: ...

    @property
    def plugin_key(self) -> str: ...

    @property
    def distribution_name(self) -> str: ...

    @property
    def version(self) -> str: ...

    @property
    def top_level_package(self) -> str: ...

    @property
    def wheel_digest(self) -> str: ...

    @property
    def artifact_ref(self) -> str: ...

    @property
    def requires_dist(self) -> tuple[str, ...]: ...


class PluginRuntimeLockStore:
    """Build and persist immutable content-addressed Runtime lock manifests."""

    def __init__(
        self,
        manifest: WorkerReleaseManifest,
        *,
        clock=None,
    ) -> None:
        self.manifest = manifest
        self._clock = clock or utc_now

    async def build_and_persist(
        self,
        session: AsyncSession,
        *,
        mode: PluginRuntimeModeValue,
        plugins: Sequence[RuntimePluginContribution],
        child_lock_digests: Sequence[str] = (),
        locked_distributions: Sequence[LockedDistribution] = (),
    ) -> PluginRuntimeLock:
        locked_plugins: dict[str, LockedPlugin] = {}
        top_level_packages: dict[str, LockedPlugin] = {}
        distributions: dict[str, LockedDistribution] = {}

        child_locks = await self._load_many(session, child_lock_digests)
        for child in child_locks:
            if child.mode != mode:
                raise PluginRuntimeLockError("plugin_runtime_mode_mismatch")
            if child.runtime_target != self.manifest.runtime_target:
                raise PluginRuntimeLockError("plugin_runtime_target_mismatch")
            for item in child.plugins:
                _merge_plugin(locked_plugins, top_level_packages, item)
            for item in child.distributions:
                normalized = _normalize_distribution(item)
                if normalized.source == "worker_release":
                    current = self.manifest.distribution_version(normalized.distribution_name)
                    if current != normalized.version:
                        raise PluginRuntimeLockError("plugin_worker_dependency_missing")
                _merge_distribution(distributions, normalized)

        for item in locked_distributions:
            normalized = _normalize_distribution(item)
            if normalized.source == "worker_release":
                current = self.manifest.distribution_version(normalized.distribution_name)
                if current != normalized.version:
                    raise PluginRuntimeLockError("plugin_worker_dependency_missing")
            _merge_distribution(distributions, normalized)

        for plugin in plugins:
            locked = LockedPlugin(
                plugin_id=plugin.plugin_id,
                plugin_version_id=plugin.plugin_version_id,
                plugin_key=plugin.plugin_key,
                distribution_name=canonicalize_name(plugin.distribution_name),
                version=plugin.version,
                top_level_package=plugin.top_level_package,
                wheel_digest=plugin.wheel_digest,
            )
            _merge_plugin(locked_plugins, top_level_packages, locked)
            _merge_distribution(
                distributions,
                LockedDistribution(
                    distribution_name=locked.distribution_name,
                    version=locked.version,
                    source="artifact",
                    artifact_digest=locked.wheel_digest,
                    artifact_ref=plugin.artifact_ref,
                ),
            )

        for plugin in plugins:
            for raw_requirement in plugin.requires_dist:
                requirement = Requirement(raw_requirement)
                if requirement.marker is not None and not requirement.marker.evaluate():
                    continue
                if requirement.url is not None:
                    raise PluginRuntimeLockError("plugin_platform_incompatible")
                dependency_name = canonicalize_name(requirement.name)
                selected = distributions.get(dependency_name)
                if selected is not None:
                    if requirement.specifier and Version(selected.version) not in requirement.specifier:
                        raise PluginRuntimeLockError("plugin_dependency_conflict")
                    continue
                dependency_version = self.manifest.distribution_version(dependency_name)
                if dependency_version is None:
                    raise PluginRuntimeLockError("plugin_worker_dependency_missing")
                if requirement.specifier and Version(dependency_version) not in requirement.specifier:
                    raise PluginRuntimeLockError("plugin_dependency_conflict")
                _merge_distribution(
                    distributions,
                    LockedDistribution(
                        distribution_name=dependency_name,
                        version=dependency_version,
                        source="worker_release",
                    ),
                )

        plugin_items = tuple(
            sorted(locked_plugins.values(), key=lambda item: (item.plugin_key, item.plugin_version_id))
        )
        distribution_items = tuple(
            sorted(distributions.values(), key=lambda item: (item.distribution_name, item.version, item.source))
        )
        payload = {
            "schema_version": "1",
            "mode": mode,
            "runtime_target": self.manifest.runtime_target.model_dump(mode="json"),
            "worker_release": self.manifest.worker_release,
            "harness_version": self.manifest.harness_version,
            "plugins": [item.model_dump(mode="json") for item in plugin_items],
            "distributions": [item.model_dump(mode="json") for item in distribution_items],
        }
        runtime_lock = PluginRuntimeLock(**payload, digest=_canonical_digest(payload))
        await self._persist(session, runtime_lock)
        return runtime_lock

    async def initialize_empty_runner_catalog(self, session: AsyncSession) -> None:
        """Control-only bootstrap under the same row lock used by catalog commands."""
        state = await session.get(PluginRuntimeStateRecord, "runtime", with_for_update=True)
        if state is None or state.mode != "runner":
            raise PluginRuntimeLockError("plugin_runtime_mode_mismatch")
        if state.active_lock_digest is not None or state.command_operation_id is not None:
            return
        active = await session.scalar(
            select(PluginRecord.id).where(PluginRecord.active_version_id.is_not(None)).limit(1)
        )
        if active is not None or state.runtime_generation != 1:
            raise PluginRuntimeLockError("plugin_runtime_lock_unavailable")
        lock = await self.build_and_persist(session, mode="runner", plugins=())
        state.active_lock_digest = lock.digest

    async def require(
        self,
        session: AsyncSession,
        digest: str,
        *,
        mode: PluginRuntimeModeValue | None = None,
    ) -> PluginRuntimeLock:
        record = await session.get(PluginRuntimeLockRecord, digest)
        if record is None:
            raise PluginRuntimeLockError("plugin_runtime_lock_unavailable")
        runtime_lock = _validated_record(record)
        if mode is not None and runtime_lock.mode != mode:
            raise PluginRuntimeLockError("plugin_runtime_mode_mismatch")
        return runtime_lock

    async def require_runner_catalog(
        self,
        session: AsyncSession,
        digest: str,
        *,
        plugins: Sequence[RuntimePluginContribution],
        child_lock_digests: Sequence[str] = (),
    ) -> PluginRuntimeLock:
        runtime_lock = await self.require(session, digest, mode="runner")
        catalog_plugins = {item.plugin_key: item for item in runtime_lock.plugins}
        for plugin in plugins:
            expected = _locked_plugin(plugin)
            if catalog_plugins.get(expected.plugin_key) != expected:
                raise PluginRuntimeLockError("plugin_version_changed")
        if any(child_digest != runtime_lock.digest for child_digest in child_lock_digests):
            raise PluginRuntimeLockError("plugin_dependency_conflict", path="subagents")
        return runtime_lock

    async def _load_many(self, session: AsyncSession, digests: Sequence[str]) -> tuple[PluginRuntimeLock, ...]:
        unique = tuple(dict.fromkeys(digests))
        if not unique:
            return ()
        records = tuple(
            (
                await session.scalars(select(PluginRuntimeLockRecord).where(PluginRuntimeLockRecord.digest.in_(unique)))
            ).all()
        )
        by_digest = {record.digest: record for record in records}
        if set(by_digest) != set(unique):
            raise PluginRuntimeLockError("plugin_runtime_lock_unavailable")
        return tuple(_validated_record(by_digest[digest]) for digest in unique)

    async def _persist(self, session: AsyncSession, runtime_lock: PluginRuntimeLock) -> None:
        existing = await session.get(PluginRuntimeLockRecord, runtime_lock.digest)
        if existing is not None:
            if _validated_record(existing) != runtime_lock:
                raise PluginRuntimeLockError("plugin_runtime_lock_digest_conflict")
            return
        try:
            async with session.begin_nested():
                session.add(
                    PluginRuntimeLockRecord(
                        digest=runtime_lock.digest,
                        schema_version=runtime_lock.schema_version,
                        mode=runtime_lock.mode,
                        manifest=runtime_lock.model_dump(mode="json"),
                        created_at=self._clock(),
                    )
                )
                await session.flush()
        except IntegrityError:
            concurrent = await session.scalar(
                select(PluginRuntimeLockRecord).where(PluginRuntimeLockRecord.digest == runtime_lock.digest)
            )
            if concurrent is None or _validated_record(concurrent) != runtime_lock:
                raise PluginRuntimeLockError("plugin_runtime_lock_digest_conflict") from None


def default_runtime_target() -> RuntimeTarget:
    preferred_tag = next(sys_tags(), None)
    wheel_abi = preferred_tag.abi if preferred_tag is not None else "none"
    return RuntimeTarget(
        python_implementation=sys.implementation.name,
        python_minor=f"{sys.version_info.major}.{sys.version_info.minor}",
        operating_system=platform.system().lower(),
        cpu_architecture=platform.machine().lower(),
        wheel_abi=wheel_abi,
    )


def installed_harness_version() -> str:
    try:
        return version("a13n-harness")
    except PackageNotFoundError:
        return "unknown"


def _normalize_distribution(item: LockedDistribution) -> LockedDistribution:
    return item.model_copy(update={"distribution_name": canonicalize_name(item.distribution_name)})


def _locked_plugin(plugin: RuntimePluginContribution) -> LockedPlugin:
    return LockedPlugin(
        plugin_id=plugin.plugin_id,
        plugin_version_id=plugin.plugin_version_id,
        plugin_key=plugin.plugin_key,
        distribution_name=canonicalize_name(plugin.distribution_name),
        version=plugin.version,
        top_level_package=plugin.top_level_package,
        wheel_digest=plugin.wheel_digest,
    )


def _merge_plugin(
    by_key: dict[str, LockedPlugin],
    by_top_level: dict[str, LockedPlugin],
    item: LockedPlugin,
) -> None:
    current = by_key.get(item.plugin_key)
    if current is not None and current != item:
        raise PluginRuntimeLockError("plugin_dependency_conflict")
    top_level = by_top_level.get(item.top_level_package)
    if top_level is not None and top_level != item:
        raise PluginRuntimeLockError("plugin_dependency_conflict")
    by_key[item.plugin_key] = item
    by_top_level[item.top_level_package] = item


def _merge_distribution(by_name: dict[str, LockedDistribution], item: LockedDistribution) -> None:
    normalized = _normalize_distribution(item)
    current = by_name.get(normalized.distribution_name)
    if current is not None and current != normalized:
        raise PluginRuntimeLockError("plugin_dependency_conflict")
    by_name[normalized.distribution_name] = normalized


def _validated_record(record: PluginRuntimeLockRecord) -> PluginRuntimeLock:
    runtime_lock = PluginRuntimeLock.model_validate(record.manifest)
    if (
        record.digest != runtime_lock.digest
        or record.schema_version != runtime_lock.schema_version
        or record.mode != runtime_lock.mode
        or runtime_lock.computed_digest() != runtime_lock.digest
    ):
        raise PluginRuntimeLockError("plugin_runtime_lock_invalid")
    return runtime_lock


def _canonical_digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()
