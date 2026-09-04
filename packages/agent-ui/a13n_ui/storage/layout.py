"""Derived native layout for one Agent UI data root."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class StorageLayout:
    """Storage-owned paths derived from one validated data root."""

    root: Path
    database: Path
    objects: Path
    runtimes: Path
    staging: Path
    content_plugins: Path

    @classmethod
    def from_root(cls, root: Path) -> StorageLayout:
        return cls(
            root=root,
            database=root / "metadata.sqlite3",
            objects=root / "objects",
            runtimes=root / "runtimes",
            staging=root / "staging",
            content_plugins=root / "content-plugins",
        )

    def prepare(self) -> None:
        """Create private storage directories without creating source configuration."""

        for path in (self.root, self.objects, self.runtimes, self.staging):
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
            if os.name != "nt":
                path.chmod(0o700)


__all__ = ["StorageLayout"]
