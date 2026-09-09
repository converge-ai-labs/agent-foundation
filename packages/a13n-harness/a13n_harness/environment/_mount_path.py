"""Canonical aggregate mount-path parsing and matching."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

_DRIVE_PATH = re.compile(r"^(?P<drive>[A-Za-z]):/(?P<tail>.*)$")


@dataclass(frozen=True, slots=True)
class ParsedMountPath:
    """One canonical absolute path in the Harness aggregate path space."""

    value: str
    flavor: Literal["posix", "windows"]
    anchor: tuple[str, ...]
    segments: tuple[str, ...]

    @property
    def comparison_key(self) -> tuple[str, ...]:
        values = (*self.anchor, *self.segments)
        if self.flavor == "windows":
            return tuple(value.casefold() for value in values)
        return values

    @property
    def depth(self) -> int:
        return len(self.segments)

    def suffix_below(self, root: ParsedMountPath) -> tuple[str, ...] | None:
        if self.flavor != root.flavor:
            return None
        own_key = self.comparison_key
        root_key = root.comparison_key
        if own_key[: len(root_key)] != root_key:
            return None
        return self.segments[len(root.segments) :]


def parse_mount_path(value: str) -> ParsedMountPath:
    """Parse one canonical absolute POSIX, drive, or UNC aggregate path."""

    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError("mount_path must be a canonical absolute path")

    drive_match = _DRIVE_PATH.fullmatch(value)
    if drive_match is not None:
        if "\\" in value:
            raise ValueError("Windows mount_path must use forward slashes")
        drive = drive_match.group("drive")
        tail = drive_match.group("tail")
        segments = _parse_segments(tail, allow_empty=True)
        return ParsedMountPath(
            value=value,
            flavor="windows",
            anchor=(f"{drive}:",),
            segments=segments,
        )

    if value.startswith("//"):
        if "\\" in value:
            raise ValueError("Windows mount_path must use forward slashes")
        body = value[2:]
        if body.endswith("/"):
            body = body[:-1]
        components = _parse_segments(body, allow_empty=False)
        if len(components) < 2:
            raise ValueError("UNC mount_path must include a server and share")
        canonical_root = len(components) == 2 and value.endswith("/")
        if value.endswith("/") and not canonical_root:
            raise ValueError("mount_path must not have a trailing separator")
        return ParsedMountPath(
            value=value,
            flavor="windows",
            anchor=("//", components[0], components[1]),
            segments=components[2:],
        )

    if value.startswith("/"):
        if value == "/":
            return ParsedMountPath(value=value, flavor="posix", anchor=("/",), segments=())
        if value.endswith("/"):
            raise ValueError("mount_path must not have a trailing separator")
        return ParsedMountPath(
            value=value,
            flavor="posix",
            anchor=("/",),
            segments=_parse_segments(value[1:], allow_empty=False),
        )

    raise ValueError("mount_path must be an absolute POSIX, drive, or UNC path")


def validate_working_directory(value: str | None) -> None:
    """Require a canonical provider-local working directory when one is supplied."""
    if value is not None and (
        not isinstance(value, str)
        or not value.startswith("/")
        or "\x00" in value
        or "//" in value
        or (value != "/" and value.endswith("/"))
        or any(segment in {".", ".."} for segment in value.split("/"))
    ):
        raise ValueError("working_directory must be a canonical absolute path")


def normalize_operation_path(value: str) -> str:
    """Normalize harmless input spelling without resolving parent traversal or links."""
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError("Use a non-empty path without NUL characters.")
    if ".." in value.split("/"):
        raise ValueError("Parent traversal (..) is not supported; use an absolute path under an available mount.")
    drive = _DRIVE_PATH.fullmatch(value)
    if (drive is not None or value.startswith("//")) and "\\" in value:
        raise ValueError("Use forward slashes for Windows drive and UNC paths.")
    prefix = "//" if value.startswith("//") else "/" if value.startswith("/") else ""
    segments = [segment for segment in value.split("/") if segment not in {"", "."}]
    normalized = prefix + "/".join(segments)
    if drive is not None and len(segments) == 1:
        normalized += "/"
    elif drive is None and not prefix and _DRIVE_PATH.fullmatch(normalized):
        # A relative POSIX name such as ./C:/file must not become a drive path.
        normalized = f"./{normalized}"
    return normalized or "."


def provider_path_from_suffix(suffix: tuple[str, ...]) -> str:
    """Render a matched aggregate suffix as a provider-local absolute path."""

    return "/" if not suffix else f"/{'/'.join(suffix)}"


def mount_path_from_provider_path(root: str, provider_path: str) -> str:
    """Render one canonical provider-local path below its aggregate mount root."""

    if not provider_path.startswith("/") or "\x00" in provider_path:
        raise ValueError("provider path must be absolute")
    if provider_path == "/":
        return root
    if provider_path.endswith("/"):
        raise ValueError("provider path must not have a trailing separator")
    suffix = _parse_segments(provider_path[1:], allow_empty=False)
    separator = "" if root.endswith("/") else "/"
    return f"{root}{separator}{'/'.join(suffix)}"


def _parse_segments(value: str, *, allow_empty: bool) -> tuple[str, ...]:
    if not value:
        if allow_empty:
            return ()
        raise ValueError("mount_path must contain path segments")
    segments = tuple(value.split("/"))
    if any(not segment or segment in {".", ".."} for segment in segments):
        raise ValueError("mount_path must be canonical")
    return segments


__all__ = [
    "ParsedMountPath",
    "mount_path_from_provider_path",
    "parse_mount_path",
    "provider_path_from_suffix",
]
