"""Per-check successes, scoped to tracked inputs and the local tool environment.

Only hashes are persisted; environment values and file contents stay private.
External services and ignored inputs are not reproducible inputs: --no-cache
forces a fresh run after changing them.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from fnmatch import fnmatchcase
from pathlib import Path

from filelock import FileLock, Timeout


@contextmanager
def worktree_lock(path: Path) -> Iterator[None]:
    """Serialize verification from snapshot/cache reads through the last result write."""
    lock = FileLock(path)
    try:
        lock.acquire(timeout=0)
    except Timeout:
        print(f"waiting for another verify run in this worktree ({path})", flush=True)
        lock.acquire()
    try:
        yield
    finally:
        lock.release()


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def environment(root: Path) -> str:
    paths = {Path(sys.executable)}
    paths.update(Path(path) for tool in ("uv", "node", "pnpm", "make") if (path := shutil.which(tool)))
    paths.update(Path(sys.prefix).glob("lib/python*/site-packages/*.dist-info/METADATA"))
    paths.update((root / ".venv/pyvenv.cfg", root / "frontend/node_modules/.modules.yaml"))
    versions = {}
    for path in paths:
        if path.is_file():
            stat = path.stat()
            versions[str(path)] = (stat.st_size, stat.st_mtime_ns)
    return digest((sys.version, sys.platform, dict(os.environ), versions))


class Successes:
    """Per-check records; the caller holds worktree_lock while loading and saving."""

    def __init__(self, root: Path, record: Path, tree: str) -> None:
        self.root, self.record = root, record
        self.context = environment(root)
        # One listing per invocation; no checkout/index mutation, and no per-step
        # subprocess when fingerprinting even a large Python dependency closure.
        listing = subprocess.run(
            ["git", "ls-tree", "-rz", tree], cwd=root, capture_output=True, text=True, check=True
        ).stdout
        self.entries = [entry.split("\t", 1) for entry in listing.split("\0") if entry]
        try:
            data = json.loads(record.read_text())
            self.passed: dict[str, str] = data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            self.passed = {}

    def signature(self, command: list[str], cwd: Path, inputs: tuple[str, ...], env: dict[str, str] | None) -> str:
        # pytest node IDs are passed through temporary files; their contents, not
        # the randomly generated filename, identify the command.
        command = [
            Path(arg.split("@", 1)[1]).read_text() if arg.startswith("PYTHON_TEST_DIRS=@") else arg for arg in command
        ]
        selected = [
            entry
            for entry in self.entries
            if any(not path or fnmatchcase(entry[1], path) or entry[1].startswith(path + "/") for path in inputs)
        ]
        return digest((command, str(cwd), self.context, env, selected))

    def save(self, name: str, signature: str | None) -> None:
        if signature is None:
            self.passed.pop(name, None)
        else:
            self.passed[name] = signature
        temporary = self.record.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.passed, sort_keys=True))
        temporary.replace(self.record)
