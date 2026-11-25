"""Sandbox mount paths shared by managed volume providers."""

from collections.abc import Collection
from pathlib import PurePosixPath


def validate_mount_paths(mount_paths: Collection[str], *, reserved: tuple[str, ...]) -> None:
    private = tuple(map(PurePosixPath, ("/var/lib/a13n-envd", "/run/a13n-envd-state", *reserved)))
    paths = {PurePosixPath(path) for path in mount_paths}
    if len(paths) != len(mount_paths):
        raise ValueError("Volume mount paths must be unique")
    for value in mount_paths:
        path = PurePosixPath(value)
        if (
            not value.startswith("/")
            or value.startswith("//")
            or str(path) != value
            or ".." in path.parts
            or "\x00" in value
        ):
            raise ValueError("Volume mount paths must be normalized absolute paths")
        if any(directory.is_relative_to(path) or path.is_relative_to(directory) for directory in private):
            raise ValueError("Volume mounts cannot overlap platform-owned paths")
        if any(parent in paths for parent in path.parents):
            raise ValueError("Volume mount paths cannot overlap")
