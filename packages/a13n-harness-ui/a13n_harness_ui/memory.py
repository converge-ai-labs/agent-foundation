"""Application-owned global and Project file memory; no Environment mount required."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from a13n_harness.capabilities.memory import DEFAULT_FILE_GUIDE, FileMemoryCapability, FileMount, MemoryCursors
from a13n_harness.providers.memory import DirectoryFileStore


@dataclass(frozen=True, slots=True)
class MemoryScope:
    name: str
    key: str
    root: Path

    def mount(self) -> FileMount:
        guide = (
            "Shared by every conversation using this configuration. Keep only cross-project preferences and facts."
            if self.name == "global"
            else "Shared by conversations in this Project. Keep project decisions, conventions and stable facts here."
        )
        return FileMount(
            name=self.name,
            cursor_key=self.key,
            store=DirectoryFileStore(self.root),
            access="write",
            always_load=("MEMORY.md",),
            guide=f"{guide} {DEFAULT_FILE_GUIDE} Keep MEMORY.md concise; detailed topics belong in separate files.",
        )


def memory_scopes(configuration_root: Path, project_id: str | None) -> tuple[MemoryScope, ...]:
    root = configuration_root / "memory"
    scopes = (MemoryScope("global", "global", root / "global"),)
    if project_id is not None:
        # Project resources own the identity; never interpret an arbitrary path as a scope.
        if Path(project_id).name != project_id or project_id in {".", ".."} or "\\" in project_id:
            raise ValueError("Invalid Project memory identity")
        scopes += (MemoryScope("project", f"project:{project_id}", root / "projects" / project_id),)
    return scopes


def bind_memory(
    configuration_root: Path | None,
    *,
    enabled: bool,
    project_id: str | None,
    positions: Mapping[str, str | None] | None = None,
) -> tuple[FileMemoryCapability | None, MemoryCursors]:
    scopes = memory_scopes(configuration_root, project_id) if enabled and configuration_root is not None else ()
    previous = positions or {}
    cursors = MemoryCursors({scope.key: previous[scope.key] for scope in scopes if scope.key in previous})
    capability = FileMemoryCapability(tuple(scope.mount() for scope in scopes), cursors=cursors) if scopes else None
    return capability, cursors
