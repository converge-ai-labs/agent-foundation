"""Harness materialization for verified immutable Foundation Skill packages."""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Protocol

from a13n_harness.capabilities import FileSkillSource, SkillCatalogItem
from a13n_harness.environment.files import FileKind, FileOperator
from a13n_harness.environment.models import EnvironmentError
from a13n_harness.errors import DefinitionError
from anyio import Lock

from .domain import FoundationSkillRevisionLock
from .package import MAX_FILES, NormalizedSkillFile, NormalizedSkillPackage

_COMPLETION_FILE = ".a13n-foundation-complete.json"
_STALE_ENVIRONMENT_CODES = frozenset({"environment_stale_mount", "environment_provider_binding_reused"})


class SkillAttemptFence(Protocol):
    """Worker-owned authority check for the current TurnAttempt fence."""

    async def require_current(self) -> None: ...


class SkillMaterializationStale(RuntimeError):
    """The current TurnAttempt no longer owns materialization authority."""


@dataclass(frozen=True, slots=True)
class LockedSkillPackage:
    lock: FoundationSkillRevisionLock
    package: NormalizedSkillPackage


class FoundationSkillSource:
    """Scan only the exact verified package roots selected for this Turn."""

    def __init__(self, source_id: str, target_root: str, packages: Sequence[LockedSkillPackage]) -> None:
        self._source_id = source_id
        self._target_root = target_root
        self._packages = tuple(packages)

    @property
    def source_id(self) -> str:
        return self._source_id

    @property
    def roots(self) -> tuple[str, ...]:
        return (self._target_root,)

    async def catalog(self, *, files: FileOperator) -> tuple[SkillCatalogItem, ...]:
        items: list[SkillCatalogItem] = []
        for selected in self._packages:
            root = _package_root(self._target_root, selected.lock.content_digest)
            source = FileSkillSource(
                f"{self.source_id}-{selected.lock.content_digest[:16]}",
                (root,),
                required=True,
                max_entries_per_root=MAX_FILES,
            )
            discovered = tuple(await source.catalog(files=files))
            if len(discovered) != 1:
                raise _invalid("A materialized Skill package has an invalid catalog shape.")
            item = discovered[0]
            manifest = selected.package.manifest
            if item.name != selected.lock.skill_name or item.description != manifest.description or item.path != root:
                raise _invalid("A materialized Skill package does not match its immutable lock.")
            items.append(item)
        return tuple(items)


class FoundationSkillMaterializer:
    """Reconcile one selected immutable catalog and publish its completion marker last."""

    def __init__(
        self,
        materializer_id: str,
        target_root: str,
        catalog_digest: str,
        packages: Sequence[LockedSkillPackage],
        *,
        fence: SkillAttemptFence | None,
    ) -> None:
        self._materializer_id = materializer_id
        self._target_root = target_root
        self._catalog_digest = catalog_digest
        self._packages = tuple(packages)
        self._fence = fence
        self._lock = Lock()
        self._completion = _completion_payload(catalog_digest, self._packages)

    @property
    def materializer_id(self) -> str:
        return self._materializer_id

    @property
    def target_root(self) -> str:
        return self._target_root

    async def materialize(self, *, files: FileOperator) -> None:
        try:
            async with self._lock:
                await self._require_current()
                if await self._matches(files, require_completion=True):
                    await self._require_current()
                    return
                await self._replace(files)
                await self._require_current()
        except SkillMaterializationStale as error:
            raise DefinitionError(
                "Skill materialization authority became stale.",
                code="skill_materialization_stale",
                retry_hint="new_run",
            ) from error
        except EnvironmentError as error:
            if error.code in _STALE_ENVIRONMENT_CODES:
                raise DefinitionError(
                    "The Environment changed during Skill materialization.",
                    code="skill_materialization_stale",
                    retry_hint="new_run",
                ) from error
            raise DefinitionError(
                "The Environment is unavailable for Skill materialization.",
                code="skill_materialization_unavailable",
                retry_hint="new_run",
                details={"environment_code": error.code},
            ) from error

    async def _replace(self, files: FileOperator) -> None:
        await self._remove_target(files)
        await files.mkdir(self.target_root, parents=True, exist_ok=False)
        for selected in self._packages:
            await self._require_current()
            root = _package_root(self.target_root, selected.lock.content_digest)
            await files.mkdir(root, parents=True, exist_ok=False)
            for item in selected.package.files:
                destination = f"{root}/{item.path}"
                await files.mkdir(destination.rsplit("/", 1)[0], parents=True, exist_ok=True)
                await files.write_bytes_stream(destination, _one_chunk(item.content), mode="create")
        if not await self._matches(files, require_completion=False):
            raise _invalid("The materialized Skill root failed complete verification.")
        await self._require_current()
        await files.write_bytes_stream(
            f"{self.target_root}/{_COMPLETION_FILE}",
            _one_chunk(self._completion),
            mode="create",
        )

    async def _remove_target(self, files: FileOperator) -> None:
        try:
            await files.stat(self.target_root)
        except EnvironmentError as error:
            if error.code == "environment_not_found":
                return
            raise
        await files.remove(self.target_root, recursive=True)

    async def _matches(self, files: FileOperator, *, require_completion: bool) -> bool:
        try:
            metadata = await files.stat(self.target_root)
        except EnvironmentError as error:
            if error.code == "environment_not_found":
                return False
            raise
        if metadata.kind != "directory":
            return False
        expected = _expected_entries(self._packages, include_completion=require_completion)
        actual = await _walk_entries(files, self.target_root, maximum=len(expected) + 1)
        if actual != {path: kind for path, (kind, _) in expected.items()}:
            return False
        if require_completion:
            completion = await _read_bounded(files, f"{self.target_root}/{_COMPLETION_FILE}", len(self._completion))
            if completion != self._completion:
                return False
        for path, (kind, package_file) in expected.items():
            if kind == "file" and package_file is not None:
                if not await _matches_file(files, f"{self.target_root}/{path}", package_file):
                    return False
        return True

    async def _require_current(self) -> None:
        if self._fence is not None:
            await self._fence.require_current()


def _expected_entries(
    packages: tuple[LockedSkillPackage, ...],
    *,
    include_completion: bool,
) -> dict[str, tuple[FileKind, NormalizedSkillFile | None]]:
    expected: dict[str, tuple[FileKind, NormalizedSkillFile | None]] = {}
    if include_completion:
        expected[_COMPLETION_FILE] = ("file", None)
    for selected in packages:
        package_directory = selected.lock.content_digest
        expected[package_directory] = ("directory", None)
        for item in selected.package.files:
            relative = f"{package_directory}/{item.path}"
            parts = relative.split("/")
            for index in range(1, len(parts)):
                expected["/".join(parts[:index])] = ("directory", None)
            expected[relative] = ("file", item)
    return expected


async def _walk_entries(files: FileOperator, root: str, *, maximum: int) -> dict[str, FileKind]:
    pending = [root]
    actual: dict[str, FileKind] = {}
    prefix = f"{root}/"
    while pending:
        directory = pending.pop()
        offset = 0
        while True:
            page = await files.list(directory, offset=offset, max_results=256, include_hidden=True)
            for entry in page.entries:
                if not entry.path.startswith(prefix):
                    raise _invalid("The Environment returned a path outside the materialization root.")
                relative = entry.path[len(prefix) :]
                if not relative or relative in actual:
                    raise _invalid("The Environment returned an invalid materialization listing.")
                actual[relative] = entry.kind
                if len(actual) >= maximum:
                    return actual
                if entry.kind == "directory":
                    pending.append(entry.path)
            if not page.has_more:
                break
            offset += len(page.entries)
            if not page.entries:
                raise _invalid("The Environment returned a non-progressing materialization listing.")
    return actual


async def _matches_file(files: FileOperator, path: str, expected: NormalizedSkillFile) -> bool:
    digest = hashlib.sha256()
    size = 0
    try:
        async for chunk in files.read_bytes_stream(path):
            size += len(chunk)
            if size > len(expected.content):
                return False
            digest.update(chunk)
    except EnvironmentError as error:
        if error.code == "environment_not_found":
            return False
        raise
    return size == len(expected.content) and digest.hexdigest() == hashlib.sha256(expected.content).hexdigest()


async def _read_bounded(files: FileOperator, path: str, maximum: int) -> bytes:
    body = bytearray()
    try:
        async for chunk in files.read_bytes_stream(path):
            body.extend(chunk)
            if len(body) > maximum:
                return b""
    except EnvironmentError as error:
        if error.code == "environment_not_found":
            return b""
        raise
    return bytes(body)


def _completion_payload(catalog_digest: str, packages: tuple[LockedSkillPackage, ...]) -> bytes:
    value = {
        "schema_version": "1",
        "catalog_digest": catalog_digest,
        "packages": [item.lock.model_dump(mode="json") for item in packages],
    }
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True).encode()


def _package_root(target_root: str, content_digest: str) -> str:
    return f"{target_root}/{content_digest}"


async def _one_chunk(content: bytes) -> AsyncIterator[bytes]:
    yield content


def _invalid(message: str) -> DefinitionError:
    return DefinitionError(message, code="skill_materialization_invalid", retry_hint="dependency_change")
