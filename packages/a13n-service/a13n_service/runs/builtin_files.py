"""Immutable text files for instance-local instructions, exposed through Harness file tools.

Package files are opened relative to directory descriptors with no symlink following. Model requests only
address the resulting immutable map: they cannot race a host path check, open special files, or escape it.
"""

import os
import stat
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, NoReturn

from a13n_harness.environment.files import (
    FileEntriesResult,
    FileMetadata,
    FileOperator,
    FileQueryRequest,
    FileTextMatch,
    FileTextResult,
    FileTextSearchRequest,
    FileTextSearchResult,
)
from a13n_harness.environment.models import EnvironmentError
from a13n_harness.providers.environment._file_patterns import PathPattern, PatternError, content_pattern

ROOT = "/platform-skills/platform-operations"
MAX_BYTES = 4 * 1024 * 1024


def packaged_text(root: Path) -> dict[str, str]:
    """Load bounded regular UTF-8 documents; reject symlinks at every opened directory and leaf."""
    result: dict[str, str] = {}
    total = 0

    def walk(directory: int, prefix: str) -> None:
        nonlocal total
        for name in sorted(os.listdir(directory)):
            descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            try:
                info = os.fstat(descriptor)
                relative = f"{prefix}/{name}" if prefix else name
                if stat.S_ISDIR(info.st_mode):
                    walk(descriptor, relative)
                elif stat.S_ISREG(info.st_mode):
                    if info.st_size > MAX_BYTES or len(result) >= 256:
                        raise ValueError("Builtin skill exceeds its size limit")
                    with os.fdopen(os.dup(descriptor), "rb") as source:
                        data = source.read(MAX_BYTES + 1)
                    total += len(data)
                    if total > MAX_BYTES:
                        raise ValueError("Builtin skill exceeds its size limit")
                    result[f"{ROOT}/{relative}"] = data.decode("utf-8")
                else:
                    raise ValueError("Builtin skill contains a non-regular file")
            finally:
                os.close(descriptor)

    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        walk(descriptor, "")
    finally:
        os.close(descriptor)
    return result


class BuiltinFiles(FileOperator):
    def __init__(self, files: dict[str, str]):
        self.files = MappingProxyType(dict(files))
        self.directories = frozenset(
            str(parent)
            for name in files
            for parent in PurePosixPath(name).parents
            if str(parent) == ROOT or str(parent).startswith(ROOT + "/")
        ) | {ROOT}
        if any(self._path(name) != name for name in files):
            raise ValueError("Builtin files require canonical paths")

    def _path(self, path: str) -> str:
        if "\x00" in path or ".." in path.split("/"):
            raise EnvironmentError("Path is outside the builtin skill", code="environment_denied")
        candidate = PurePosixPath(path if path.startswith("/") else f"{ROOT}/{path}")
        value = str(candidate)
        if value != ROOT and not value.startswith(ROOT + "/"):
            raise EnvironmentError("Path is outside the builtin skill", code="environment_denied")
        return value

    async def stat(self, path: str) -> FileMetadata:
        path = self._path(path)
        if path in self.files:
            return FileMetadata(path=path, kind="file", size=len(self.files[path].encode()), writable=False)
        if path in self.directories:
            return FileMetadata(path=path, kind="directory", writable=False)
        raise EnvironmentError("Builtin skill path does not exist", code="environment_not_found")

    async def read_text(
        self, path: str, *, line_offset: int = 0, line_limit: int = 200, max_line_length: int = 2000
    ) -> FileTextResult:
        item = await self.stat(path)
        if item.kind != "file":
            raise EnvironmentError("Path is not a text file", code="environment_unsupported")
        lines = self.files[item.path].splitlines(keepends=True)
        selected = lines[line_offset : line_offset + min(line_limit, 1000)]
        truncated = tuple(line_offset + index + 1 for index, line in enumerate(selected) if len(line) > max_line_length)
        text = "".join(line if len(line) <= max_line_length else line[:max_line_length] + "\n" for line in selected)
        return FileTextResult(
            path=item.path,
            text=text,
            line_offset=line_offset,
            lines_read=len(selected),
            has_more=line_offset + len(selected) < len(lines),
            truncated_lines=truncated,
        )

    async def list(
        self, path: str, *, offset: int = 0, max_results: int, include_hidden: bool = False
    ) -> FileEntriesResult:
        return await self.query(
            FileQueryRequest(
                root=path,
                pattern="*",
                recursive=False,
                offset=offset,
                max_results=max_results,
                include_hidden=include_hidden,
            )
        )

    async def query(self, request: FileQueryRequest) -> FileEntriesResult:
        root = (await self.stat(request.root)).path
        try:
            pattern = PathPattern(request.pattern)
        except PatternError as error:
            raise EnvironmentError("Invalid file pattern", code="environment_request_invalid") from error
        matches = []
        for name in sorted(self.files.keys() | self.directories):
            if not name.startswith(root + "/"):
                continue
            relative = name[len(root) + 1 :]
            if (not request.recursive and "/" in relative) or (
                not request.include_hidden and any(p.startswith(".") for p in relative.split("/"))
            ):
                continue
            if pattern.matches(relative):
                item = await self.stat(name)
                if request.kinds is None or item.kind in request.kinds:
                    matches.append(item)
        end = request.offset + min(request.max_results, 1000)
        return FileEntriesResult(
            entries=tuple(matches[request.offset : end]), offset=request.offset, has_more=end < len(matches)
        )

    async def search_text(self, request: FileTextSearchRequest) -> FileTextSearchResult:
        root = await self.stat(request.root)
        try:
            pattern = content_pattern(request.pattern, regex=request.regex, case_sensitive=request.case_sensitive)
        except PatternError as error:
            raise EnvironmentError("Invalid search pattern", code="environment_request_invalid") from error
        paths = (
            [root.path]
            if root.kind == "file"
            else [
                item.path
                for item in (
                    await self.query(
                        FileQueryRequest(
                            root=root.path,
                            pattern=request.include,
                            include_hidden=request.include_hidden,
                            kinds=frozenset({"file"}),
                            max_results=1000,
                        )
                    )
                ).entries
            ]
        )
        matches = []
        for path in paths[: request.max_files]:
            text = self.files[path]
            if len(text.encode()) > request.max_file_bytes:
                continue
            lines = text.splitlines()
            count = 0
            for index, line in enumerate(lines):
                if not pattern.search(line):
                    continue
                start = max(0, index - request.context_lines)
                matches.append(
                    FileTextMatch(
                        path=path,
                        line=index + 1,
                        text=line[: request.max_line_length],
                        text_truncated=len(line) > request.max_line_length,
                        context_start_line=start + 1,
                        context="\n".join(
                            context_line[: request.max_line_length]
                            for context_line in lines[start : index + request.context_lines + 1]
                        ),
                    )
                )
                count += 1
                if request.max_matches_per_file is not None and count >= request.max_matches_per_file:
                    break
        end = request.offset + min(request.max_matches, 1000)
        return FileTextSearchResult(
            matches=tuple(matches[request.offset : end]), offset=request.offset, has_more=end < len(matches)
        )

    async def _deny(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise EnvironmentError("Builtin skills permit text reads only", code="environment_denied")

    read_bytes = write_bytes_stream = write_text = patch_text = mkdir = move = remove = copy = _deny

    async def read_bytes_stream(self, path: str, *, chunk_size: int = 65536):
        await self._deny()
        yield b""  # Keep the FileOperator streaming protocol while refusing byte access.
