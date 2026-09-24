"""An in-memory FileStore fake with numbered versions, a change feed and interference hooks."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from a13n_harness.providers.memory import (
    Changes,
    FileEntry,
    FileFormat,
    FileText,
    FullResync,
    GrepMatch,
    MemoryStoreError,
    Origin,
    describe,
    validate_path,
)


class FakeFileStore:
    """Versions are `v<revision>`; the cursor is the revision count.

    `before(operation)` runs at the start of every call, so a test can change
    files underneath a caller or make the store fail.
    """

    def __init__(self, files: Mapping[str, str] | None = None) -> None:
        self.files: dict[str, FileText] = {}
        self.feed: list[str] = []
        self.origins: list[Origin] = []
        self.calls: list[str] = []
        self.before: Callable[[str], None] = lambda operation: None
        for path, text in (files or {}).items():
            self.put(path, text)

    def put(self, path: str, text: str) -> str:
        """Change a file directly, as another writer would."""
        self.feed.append(path)
        self.files[path] = FileText(path=path, text=text, version=f"v{len(self.feed)}")
        return self.files[path].version

    def remove(self, path: str) -> None:
        self.feed.append(path)
        del self.files[path]

    def _enter(self, operation: str) -> None:
        self.calls.append(operation)
        self.before(operation)

    def _check(self, path: str, expected: str | None) -> None:
        current = self.files.get(path)
        if (current.version if current else None) != expected:
            raise MemoryStoreError("version_mismatch", f"{path} changed.", current=current)

    async def list(self) -> list[FileEntry]:
        self._enter("list")
        return [
            FileEntry(
                path=path,
                version=file.version,
                size=len(file.text.encode()),
                description=describe(file.text, FileFormat()),
            )
            for path, file in sorted(self.files.items())
        ]

    async def read(self, path: str) -> FileText:
        self._enter("read")
        if path not in self.files:
            raise MemoryStoreError("not_found", f"{path} does not exist.")
        return self.files[path]

    async def write(self, path: str, text: str, *, expected: str | None, origin: Origin) -> str:
        self._enter("write")
        path = validate_path(path, FileFormat())
        describe(text, FileFormat())
        self._check(path, expected)
        self.origins.append(origin)
        return self.put(path, text)

    async def move(self, source: str, destination: str, *, expected: str, origin: Origin) -> str:
        self._enter("move")
        self._check(source, expected)
        if destination in self.files:
            raise MemoryStoreError("already_exists", f"{destination} exists.", current=self.files[destination])
        self.origins.append(origin)
        text = self.files[source].text
        self.remove(source)
        return self.put(destination, text)

    async def delete(self, path: str, *, expected: str, origin: Origin) -> None:
        self._enter("delete")
        self._check(path, expected)
        self.origins.append(origin)
        self.remove(path)

    async def changes(self, since: str | None) -> Changes | FullResync:
        self._enter("changes")
        cursor = str(len(self.feed))
        if since is None:
            return FullResync(cursor=cursor)
        return Changes(cursor=cursor, paths=tuple(sorted(set(self.feed[int(since) :]))))

    async def purge(self) -> None:
        self.files.clear()


class SearchingFileStore(FakeFileStore):
    """A fake that answers grep itself."""

    def __init__(self, files: Mapping[str, str] | None = None) -> None:
        super().__init__(files)
        self.searches: list[tuple[str, bool, bool, str, int]] = []

    async def search(
        self, pattern: str, *, regex: bool, case_sensitive: bool, path: str, limit: int
    ) -> tuple[list[GrepMatch], bool]:
        self.searches.append((pattern, regex, case_sensitive, path, limit))
        return [GrepMatch(path="native.md", line=3, text="from the store")], True
