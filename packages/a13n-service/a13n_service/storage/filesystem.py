"""Confined path and atomic file helpers for local or mounted filesystems."""

import os
import stat
import uuid
from collections.abc import AsyncIterable
from pathlib import Path, PurePosixPath, PureWindowsPath

import anyio
from anyio import CapacityLimiter, to_thread


async def prepare_root(root: Path, *, create: bool, limiter: CapacityLimiter | None = None) -> Path:
    def prepare() -> Path:
        if create:
            root.mkdir(parents=True, exist_ok=True, mode=0o700)
        resolved = root.resolve(strict=True)
        if not resolved.is_dir():
            raise ValueError(f"storage root is not a directory: {root}")
        return resolved

    return await to_thread.run_sync(prepare, limiter=limiter)


async def resolve_under_root(root: Path, logical_path: str, *, limiter: CapacityLimiter | None = None) -> Path:
    parts = _logical_parts(logical_path)

    def resolve() -> Path:
        resolved_root = root.resolve(strict=True)
        current = resolved_root
        for part in parts:
            current = current / part
            try:
                mode = current.lstat().st_mode
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(mode):
                raise ValueError("logical path crosses a symlink")
        candidate = resolved_root.joinpath(*parts)
        if not candidate.is_relative_to(resolved_root):
            raise ValueError("logical path escapes the configured root")
        return candidate

    return await to_thread.run_sync(resolve, limiter=limiter)


async def atomic_write(
    destination: Path,
    source: AsyncIterable[bytes],
    *,
    limiter: CapacityLimiter | None = None,
) -> None:
    parent = destination.parent
    temporary = parent / f".{destination.name}.{uuid.uuid4().hex}.tmp"

    await to_thread.run_sync(lambda: parent.mkdir(parents=True, exist_ok=True), limiter=limiter)
    try:
        file = await anyio.open_file(temporary, "xb", limiter=limiter)
        try:
            await to_thread.run_sync(lambda: os.chmod(temporary, 0o600), limiter=limiter)
            async for chunk in source:
                if not isinstance(chunk, bytes) or not chunk:
                    raise ValueError("file source must yield non-empty bytes")
                await file.write(chunk)
            await file.flush()
        finally:
            with anyio.move_on_after(5, shield=True):
                await file.aclose()
        await to_thread.run_sync(os.replace, temporary, destination, limiter=limiter)
    finally:
        with anyio.move_on_after(5, shield=True):
            await to_thread.run_sync(_unlink_if_present, temporary, limiter=limiter)


def _logical_parts(logical_path: str) -> tuple[str, ...]:
    if not logical_path or "\x00" in logical_path:
        raise ValueError("logical path must be non-empty and contain no NUL")
    windows_path = PureWindowsPath(logical_path)
    if windows_path.is_absolute() or windows_path.drive:
        raise ValueError("logical path must be relative")
    normalized = logical_path.replace("\\", "/")
    raw_parts = normalized.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise ValueError("logical path contains an invalid component")
    posix_path = PurePosixPath(normalized)
    if posix_path.is_absolute():
        raise ValueError("logical path contains an invalid component")
    return posix_path.parts


def _unlink_if_present(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
