"""Model-facing file memory tools: view, grep, and writes that check their condition at the call."""

from __future__ import annotations

import re
from collections.abc import Callable, Collection, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import replace
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import Field, JsonValue
from pydantic_ai import RunContext
from pydantic_ai.exceptions import ToolFailed

from a13n_harness.context import AgentContext
from a13n_harness.providers.memory import (
    FileEntry,
    FileFormat,
    FileStore,
    FileText,
    GrepMatch,
    MemoryAccess,
    MemoryStoreError,
    Origin,
    SearchableFileStore,
    describe,
    validate_directory,
    validate_path,
)

from ._instructions import InstructionFunctionToolset, tool_instruction
from ._memory import MemoryName, MemoryTool, failure, memory_toolset, mounted

if TYPE_CHECKING:
    from a13n_harness.capabilities.memory import FileMount

type FileToolKey = Literal["view", "grep", "create", "edit", "append", "move", "delete"]

_TOOLS: dict[FileToolKey, MemoryTool] = {
    "view": MemoryTool(
        "read",
        frozenset({"read"}),
        128 * 1024,
        'List a memory directory ("" for the root, or a path ending in "/"), or read one file with its version.',
    ),
    "grep": MemoryTool(
        "read",
        frozenset({"read"}),
        64 * 1024,
        "Find the lines of a memory's files that contain a text. Literal and case-insensitive unless asked otherwise.",
    ),
    "create": MemoryTool(
        "write", frozenset({"write"}), 4096, "Create a memory file at a path that does not exist yet."
    ),
    "edit": MemoryTool(
        "write",
        frozenset({"read", "write"}),
        4096,
        "Replace old_string with new_string in a memory file; old_string must occur exactly once.",
    ),
    "append": MemoryTool(
        "write", frozenset({"read", "write"}), 4096, "Append text to the end of an existing memory file."
    ),
    "move": MemoryTool(
        "write",
        frozenset({"read", "write", "delete"}),
        4096,
        "Move or rename a memory file to a destination that does not exist yet.",
    ),
    "delete": MemoryTool("write", frozenset({"delete"}), 4096, "Delete a memory file at the version you viewed."),
}
FILE_TOOL_KEYS: tuple[FileToolKey, ...] = tuple(_TOOLS)
_INSTRUCTION = tool_instruction("memory-files")

_FilePath = Annotated[
    str, Field(min_length=1, description="File path relative to the memory root, such as notes/tea.md")
]


class MemoryFileToolset:
    """The file memory tools over one capability's mounts.

    A tool is offered when it is enabled and some mount's access allows it; its
    `memory` enum lists exactly those mounts. Every write reads the file, checks
    its condition, and writes expecting the version it read, re-reading after a
    lost compare-and-swap up to `write_retries` times.
    """

    def __init__(
        self,
        mounts: Sequence[FileMount],
        *,
        format: FileFormat,
        write_retries: int,
        origin: Origin,
        tools: Collection[FileToolKey],
    ) -> None:
        self._mounts = {mount.name: mount for mount in mounts}
        self._format = format
        self._write_retries = write_retries
        self._origin = origin
        self._enabled = frozenset(tools)

    def get_toolset(self) -> InstructionFunctionToolset | None:
        return memory_toolset(self, "file", _TOOLS, self._enabled, self._mounts, _INSTRUCTION)

    async def view(
        self,
        ctx: RunContext[AgentContext],
        memory: MemoryName,
        path: Annotated[str, Field(description='A file, or a directory: "" for the root or a path ending in "/"')] = "",
    ) -> dict[str, JsonValue]:
        del ctx
        mount = self._mount(memory, "read")
        with _store_failures(self._format):
            if path == "" or path.endswith("/"):
                directory = validate_directory(path, self._format)
                return self._listing(mount, directory, await mount.store.list())
            path = validate_path(path, self._format)
            file = await _read(mount.store, path)
            if file is not None:
                return {"memory": mount.name, **_file(file, self._format)}
            entries = await mount.store.list()
            if any(entry.path.startswith(f"{path}/") for entry in entries):
                return self._listing(mount, f"{path}/", entries)
        raise failure(
            "not_found", f'{path} does not exist. List the files with memory_file_view(memory="{mount.name}").'
        )

    async def grep(
        self,
        ctx: RunContext[AgentContext],
        memory: MemoryName,
        pattern: Annotated[str, Field(min_length=1, max_length=512, description="Text to find in each line")],
        path: Annotated[str, Field(description='Directory to search: "" for all files or a path ending in "/"')] = "",
        regex: Annotated[bool, Field(description="Treat pattern as a regular expression")] = False,
        case_sensitive: Annotated[bool, Field(description="Match letter case exactly")] = False,
        limit: Annotated[int, Field(ge=1, le=200, description="Maximum matching lines to return")] = 50,
    ) -> dict[str, JsonValue]:
        del ctx
        mount = self._mount(memory, "read")
        with _store_failures(self._format):
            directory = validate_directory(path, self._format)
            if isinstance(mount.store, SearchableFileStore):
                matches, truncated = await mount.store.search(
                    pattern, regex=regex, case_sensitive=case_sensitive, path=directory, limit=limit
                )
            else:
                matches, truncated = await _scan(
                    mount.store, _compile(pattern, regex, case_sensitive), directory, limit
                )
        result: dict[str, JsonValue] = {
            "memory": mount.name,
            "matches": [{"path": match.path, "line": match.line, "text": match.text} for match in matches],
            "truncated": truncated,
        }
        if not matches:
            result["hint"] = (
                "No line matched. Try a shorter or different keyword, or read the index with "
                f'memory_file_view(memory="{mount.name}").'
            )
        return result

    async def create(
        self,
        ctx: RunContext[AgentContext],
        memory: MemoryName,
        path: _FilePath,
        content: Annotated[str, Field(description="The whole content of the new file")],
    ) -> dict[str, JsonValue]:
        def created(current: FileText | None) -> str:
            if current is not None:
                raise _unmet(
                    "already_exists", f"{current.path} already exists; nothing changed.", current, self._format
                )
            return content

        return await self._write(ctx, memory, path, created)

    async def edit(
        self,
        ctx: RunContext[AgentContext],
        memory: MemoryName,
        path: _FilePath,
        old_string: Annotated[str, Field(min_length=1, description="Text that occurs exactly once in the file")],
        new_string: Annotated[str, Field(description="Replacement text")],
    ) -> dict[str, JsonValue]:
        def edited(current: FileText | None) -> str:
            current = self._existing(current, path)
            count = current.text.count(old_string)
            if count != 1:
                where = "does not occur" if count == 0 else f"occurs {count} times"
                raise _unmet(
                    "no_match" if count == 0 else "ambiguous_match",
                    f"old_string {where} in {path}; nothing changed. Read the current content and retry with text "
                    "that occurs exactly once.",
                    current,
                    self._format,
                )
            return current.text.replace(old_string, new_string, 1)

        return await self._write(ctx, memory, path, edited)

    async def append(
        self,
        ctx: RunContext[AgentContext],
        memory: MemoryName,
        path: _FilePath,
        content: Annotated[str, Field(min_length=1, description="Text to add at the end of the file")],
    ) -> dict[str, JsonValue]:
        def appended(current: FileText | None) -> str:
            text = self._existing(current, path).text
            return f"{text}\n{content}" if text and not text.endswith("\n") else text + content

        return await self._write(ctx, memory, path, appended)

    async def move(
        self,
        ctx: RunContext[AgentContext],
        memory: MemoryName,
        source: _FilePath,
        destination: _FilePath,
    ) -> dict[str, JsonValue]:
        mount = self._mount(memory, "write")
        origin = replace(self._origin, tool_call_id=ctx.tool_call_id)
        with _store_failures(self._format):
            source = validate_path(source, self._format)
            destination = validate_path(destination, self._format)
            for _ in range(self._write_retries + 1):
                current = self._existing(await _read(mount.store, source), source)
                try:
                    version = await mount.store.move(source, destination, expected=current.version, origin=origin)
                except MemoryStoreError as error:
                    if error.code != "version_mismatch":
                        raise
                    continue
                return {"memory": mount.name, "path": destination, "version": version}
            raise await self._exhausted(mount.store, source)

    async def delete(
        self,
        ctx: RunContext[AgentContext],
        memory: MemoryName,
        path: _FilePath,
        version: Annotated[str, Field(min_length=1, description="The version you viewed")],
    ) -> dict[str, JsonValue]:
        mount = self._mount(memory, "write")
        origin = replace(self._origin, tool_call_id=ctx.tool_call_id)
        with _store_failures(self._format):
            path = validate_path(path, self._format)
            try:
                await mount.store.delete(path, expected=version, origin=origin)
            except MemoryStoreError as error:
                if error.code != "version_mismatch":
                    raise
                current = self._existing(error.current, path)
                raise _unmet(
                    "version_mismatch",
                    f"{path} is no longer at version {version}; nothing was deleted. Read the current content "
                    "and decide again.",
                    current,
                    self._format,
                ) from None
        return {"memory": mount.name, "path": path, "deleted": True}

    async def _write(
        self,
        ctx: RunContext[AgentContext],
        memory: str,
        path: str,
        change: Callable[[FileText | None], str],
    ) -> dict[str, JsonValue]:
        """Read, check the condition in `change`, and write expecting the version read."""
        mount = self._mount(memory, "write")
        origin = replace(self._origin, tool_call_id=ctx.tool_call_id)
        with _store_failures(self._format):
            path = validate_path(path, self._format)
            for _ in range(self._write_retries + 1):
                current = await _read(mount.store, path)
                text = change(current)
                describe(text, self._format)
                expected = current.version if current is not None else None
                try:
                    version = await mount.store.write(path, text, expected=expected, origin=origin)
                except MemoryStoreError as error:
                    if error.code != "version_mismatch":
                        raise
                    continue
                return {"memory": mount.name, "path": path, "version": version, "size": len(text.encode())}
            raise await self._exhausted(mount.store, path)

    def _mount(self, memory: str, access: MemoryAccess) -> FileMount:
        return mounted(self._mounts, memory, access)

    def _existing(self, current: FileText | None, path: str) -> FileText:
        if current is None:
            raise _unmet("not_found", f"{path} does not exist; nothing changed.", None, self._format)
        return current

    async def _exhausted(self, store: FileStore, path: str) -> ToolFailed:
        return _unmet(
            "conflict_retries_exhausted",
            f"Other writes kept changing {path}; nothing changed after {self._write_retries} retries. "
            "Read the current content and decide again.",
            await _read(store, path),
            self._format,
        )

    def _listing(self, mount: FileMount, directory: str, entries: Sequence[FileEntry]) -> dict[str, JsonValue]:
        """The files directly in `directory`, and each subdirectory with its file count."""
        files: dict[str, JsonValue] = {}
        counts: dict[str, int] = {}
        for entry in entries:
            if not entry.path.startswith(directory):
                continue
            rest = entry.path[len(directory) :]
            if "/" in rest:
                child = f"{directory}{rest.split('/', 1)[0]}/"
                counts[child] = counts.get(child, 0) + 1
            else:
                files[entry.path] = {
                    "path": entry.path,
                    "description": entry.description,
                    "size": entry.size,
                    "version": entry.version,
                }
        if directory and not files and not counts:
            raise failure("not_found", f"{directory} does not exist or holds no files.")
        listed = files | {child: {"path": child, "files": count} for child, count in counts.items()}
        return {"memory": mount.name, "path": directory, "entries": [listed[path] for path in sorted(listed)]}


async def _read(store: FileStore, path: str) -> FileText | None:
    try:
        return await store.read(path)
    except MemoryStoreError as error:
        if error.code == "not_found":
            return None
        raise


def _compile(pattern: str, regex: bool, case_sensitive: bool) -> re.Pattern[str]:
    try:
        return re.compile(pattern if regex else re.escape(pattern), 0 if case_sensitive else re.IGNORECASE)
    except re.error as error:
        raise MemoryStoreError("invalid_pattern", f"The pattern is not a valid regular expression: {error}.") from None


async def _scan(store: FileStore, pattern: re.Pattern[str], directory: str, limit: int) -> tuple[list[GrepMatch], bool]:
    """Match lines by reading each file, for stores that do not search themselves."""
    matches: list[GrepMatch] = []
    for entry in await store.list():
        if not entry.path.startswith(directory) or (file := await _read(store, entry.path)) is None:
            continue
        for number, line in enumerate(file.text.splitlines(), start=1):
            if pattern.search(line):
                if len(matches) == limit:
                    return matches, True
                matches.append(GrepMatch(path=entry.path, line=number, text=line))
    return matches, False


def _file(file: FileText, fmt: FileFormat) -> dict[str, JsonValue]:
    """A file for the model, cut to the file size limit when a lowered limit left it larger."""
    data = file.text.encode()
    value: dict[str, JsonValue] = {
        "path": file.path,
        "version": file.version,
        "size": len(data),
        "content": data[: fmt.max_file_bytes].decode(errors="ignore"),
    }
    if len(data) > fmt.max_file_bytes:
        value["truncated"] = True
    return value


def _unmet(code: str, message: str, current: FileText | None, fmt: FileFormat) -> ToolFailed:
    """A failed condition, with the file as it is now (null when it does not exist)."""
    return failure(code, message, current=_file(current, fmt) if current is not None else None)


@contextmanager
def _store_failures(fmt: FileFormat) -> Iterator[None]:
    """Report a store's refusal to the model, with the file it names when there is one."""
    try:
        yield
    except MemoryStoreError as error:
        if error.current is not None:
            raise _unmet(error.code, str(error), error.current, fmt) from None
        raise failure(error.code, str(error)) from None


__all__ = ["FILE_TOOL_KEYS", "FileToolKey", "MemoryFileToolset"]
