"""Optional bounded Git text diff over one verified snapshot, never a repository."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
from pathlib import Path

from a13n_harness.providers.memory import DirectoryFileStore, MemoryStoreError

_MAX_BYTES = 256 * 1024
_MAX_FILES = 128
_MAX_DIFF_BYTES = 32 * 1024


async def snapshot(store: DirectoryFileStore, expected: dict[str, str]) -> dict[str, str]:
    if len(expected) > _MAX_FILES:
        return {}
    texts: dict[str, str] = {}
    size = 0
    try:
        for path, version in sorted(expected.items()):
            file = await store.read(path)
            size += len(path.encode()) + len(file.text.encode())
            if size > _MAX_BYTES or file.version != version:
                return {}
            texts[path] = file.text
    except (MemoryStoreError, OSError):
        return {}
    return texts


def diff_context(previous: dict[str, str], expected: dict[str, str], current: dict[str, str]) -> str:
    """Missing Git, stale snapshots and oversized output simply omit the hint."""
    if not previous or not current or len(previous) > _MAX_FILES:
        return ""
    if {path: hashlib.sha256(text.encode()).hexdigest()[:16] for path, text in previous.items()} != expected:
        return ""
    before = _text(previous)
    after = _text(current)
    if max(len(before.encode()), len(after.encode())) > _MAX_BYTES:
        return ""
    git = shutil.which("git")
    if git is None:
        return ""
    try:
        with tempfile.TemporaryDirectory(prefix="a13n-memory-diff-") as directory:
            root = Path(directory)
            (root / "previous").write_text(before)
            (root / "current").write_text(after)
            result = subprocess.run(
                [
                    git,
                    "diff",
                    "--no-index",
                    "--no-ext-diff",
                    "--no-textconv",
                    "--color=never",
                    "--",
                    "previous",
                    "current",
                ],
                cwd=root,
                capture_output=True,
                timeout=5,
                check=False,
            )
        if result.returncode != 1 or len(result.stdout) > _MAX_DIFF_BYTES:
            return ""
        return result.stdout.decode("utf-8", errors="replace")
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _text(files: dict[str, str]) -> str:
    return "\n".join(f"File: {path!r}\n{text}" for path, text in sorted(files.items()))
