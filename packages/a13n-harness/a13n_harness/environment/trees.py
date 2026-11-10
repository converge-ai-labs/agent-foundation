"""Bounded metadata BFS for providers without a native tree operation."""

from __future__ import annotations

import posixpath
from collections import deque

from a13n_harness._json import dump_json_bytes
from a13n_harness.environment.files import FileMetadata, FileOperator, FileTreeRequest, FileTreeResult
from a13n_harness.environment.models import EnvironmentError


def encoded_path_prefix_bytes(root: str) -> int:
    return len(dump_json_bytes(root.rstrip("/"))) - 2


def tree_entry_value(entry: FileMetadata) -> dict[str, str | int]:
    item: dict[str, str | int] = {"kind": entry.kind, "path": entry.path}
    if entry.size is not None:
        item["size"] = entry.size
    return item


def tree_entry_bytes(entry: FileMetadata, root: str, prefix_bytes: int) -> int:
    prefix = root.rstrip("/")
    if not entry.path.startswith(prefix + "/"):
        raise EnvironmentError("Tree entry is outside its root.", code="environment_provider_failure")
    value = tree_entry_value(entry.model_copy(update={"path": entry.path[len(prefix) :]}))
    return len(dump_json_bytes(value)) + prefix_bytes + 1


async def list_tree(files: FileOperator, request: FileTreeRequest) -> FileTreeResult:
    entries: list[FileMetadata] = []
    pending = deque([(request.root, 0, 0)])
    calls = 0
    used = 0
    truncated = False
    root: str | None = None
    while pending:
        if calls >= request.max_directory_calls or len(entries) >= request.max_entries or used >= request.max_bytes:
            truncated = True
            break
        path, parent_depth, offset = pending.popleft()
        remaining = request.max_entries - len(entries)
        page = await files.list(path, offset=offset, max_results=remaining, include_hidden=request.include_hidden)
        calls += 1
        ordered = sorted(page.entries, key=lambda item: item.path)
        if len(ordered) > remaining:
            ordered = ordered[:remaining]
            truncated = True
        if root is None and ordered:
            root = posixpath.dirname(ordered[0].path)
        for entry in ordered:
            assert root is not None
            prefix_bytes = request.path_prefix_bytes
            if prefix_bytes is None:
                prefix_bytes = encoded_path_prefix_bytes(root)
            else:
                # list() can canonicalize an explicitly selected directory link.
                # Routing replaces the requested prefix, not the observed root.
                prefix_bytes += encoded_path_prefix_bytes(root) - encoded_path_prefix_bytes(request.root)
            cost = tree_entry_bytes(entry, root, prefix_bytes)
            if used + cost > request.max_bytes:
                return FileTreeResult(entries=tuple(entries), truncated=True, directory_calls=calls)
            entries.append(entry)
            used += cost
            if entry.kind == "directory":
                if parent_depth + 1 < request.max_depth:
                    pending.append((entry.path, parent_depth + 1, 0))
                else:
                    truncated = True
        if page.has_more:
            if not ordered:
                return FileTreeResult(entries=tuple(entries), truncated=True, directory_calls=calls)
            pending.appendleft((path, parent_depth, offset + len(ordered)))
    return FileTreeResult(entries=tuple(entries), truncated=truncated, directory_calls=calls)
