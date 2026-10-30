"""A Run's directory view of a Space. Search falls back to the same scoped file interface."""

from collections.abc import Mapping
from dataclasses import dataclass

from a13n_harness.providers.memory import (
    Changes,
    FileEntry,
    FileFormat,
    FileStore,
    FileText,
    FullResync,
    MemoryStoreError,
    Origin,
    validate_path,
)


@dataclass(frozen=True, slots=True)
class SpaceFileView:
    store: FileStore
    roots: tuple[str, ...]
    format: FileFormat

    def visible(self, path: str) -> bool:
        return path.startswith(self.roots)

    def _path(self, path: str) -> str:
        path = validate_path(path, self.format)
        if not self.visible(path):
            raise MemoryStoreError("forbidden", "The path is outside this Run's Memory Space view")
        return path

    async def list(self) -> list[FileEntry]:
        return [entry for entry in await self.store.list() if self.visible(entry.path)]

    async def read(self, path: str) -> FileText:
        return await self.store.read(self._path(path))

    def _dependencies(self, dependencies: Mapping[str, str | None] | None) -> dict[str, str | None]:
        return {self._path(path): version for path, version in (dependencies or {}).items()}

    async def write(
        self,
        path: str,
        text: str,
        *,
        expected: str | None,
        origin: Origin,
        dependencies: Mapping[str, str | None] | None = None,
    ) -> str:
        return await self.store.write(
            self._path(path), text, expected=expected, origin=origin, dependencies=self._dependencies(dependencies)
        )

    async def move(
        self,
        source: str,
        destination: str,
        *,
        expected: str,
        origin: Origin,
        dependencies: Mapping[str, str | None] | None = None,
    ) -> str:
        return await self.store.move(
            self._path(source),
            self._path(destination),
            expected=expected,
            origin=origin,
            dependencies=self._dependencies(dependencies),
        )

    async def delete(
        self,
        path: str,
        *,
        expected: str,
        origin: Origin,
        dependencies: Mapping[str, str | None] | None = None,
    ) -> None:
        await self.store.delete(
            self._path(path), expected=expected, origin=origin, dependencies=self._dependencies(dependencies)
        )

    async def changes(self, since: str | None) -> Changes | FullResync:
        changes = await self.store.changes(since)
        if isinstance(changes, FullResync):
            return changes
        return Changes(changes.cursor, tuple(path for path in changes.paths if self.visible(path)))

    async def purge(self) -> None:
        raise MemoryStoreError("forbidden", "A Run cannot purge a Memory Space")
