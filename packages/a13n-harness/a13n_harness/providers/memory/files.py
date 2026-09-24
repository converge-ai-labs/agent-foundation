"""Path and content rules every file memory applies, whatever stores it."""

from __future__ import annotations

import unicodedata

import yaml
from pydantic import BaseModel, ConfigDict, Field

from .contracts import MemoryStoreError

_FENCE = "---\n"
_CLOSING = "\n---\n"


class FileFormat(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_file_bytes: int = Field(default=65_536, ge=1)
    description_chars: int = Field(default=200, ge=1)
    frontmatter_bytes: int = Field(default=2048, ge=1)
    path_bytes: int = Field(default=256, ge=1)


def validate_path(path: str, fmt: FileFormat) -> str:
    """The canonical form of a file path, or `invalid_path`."""
    normalized = unicodedata.normalize("NFC", path)
    if not normalized or normalized.startswith("/") or normalized.endswith("/"):
        raise MemoryStoreError("invalid_path", "A path names a file relative to the memory root.")
    _check_segments(normalized, fmt)
    return normalized


def validate_directory(path: str, fmt: FileFormat) -> str:
    """The canonical form of a directory: "" for the root, else a path ending in "/"."""
    normalized = unicodedata.normalize("NFC", path)
    if normalized == "":
        return normalized
    if normalized.startswith("/") or not normalized.endswith("/"):
        raise MemoryStoreError("invalid_path", 'A directory is "" or a relative path ending in "/".')
    _check_segments(normalized[:-1], fmt)
    return normalized


def _check_segments(path: str, fmt: FileFormat) -> None:
    if len(path.encode()) > fmt.path_bytes:
        raise MemoryStoreError("invalid_path", f"A path has at most {fmt.path_bytes} bytes.")
    if any(unicodedata.category(char) == "Cc" or char == "\\" for char in path):
        raise MemoryStoreError("invalid_path", "A path has no control characters or backslashes.")
    if any(segment in ("", ".", "..") for segment in path.split("/")):
        raise MemoryStoreError("invalid_path", 'A path has no empty, "." or ".." segments.')


def describe(text: str, fmt: FileFormat) -> str | None:
    """Check a file's content and return its description.

    The description is the frontmatter `description`, else the first non-empty
    line of the body, cut to `description_chars`.
    """
    if len(text.encode()) > fmt.max_file_bytes:
        raise MemoryStoreError("too_large", f"A memory file has at most {fmt.max_file_bytes} bytes.")
    body = text
    if text.startswith(_FENCE):
        end = text.find(_CLOSING, len(_FENCE) - 1)
        if end < 0 or len(text[: end + len(_CLOSING)].encode()) > fmt.frontmatter_bytes:
            raise MemoryStoreError(
                "invalid_file", f"Frontmatter is closed by a '---' line within {fmt.frontmatter_bytes} bytes."
            )
        description = _frontmatter_description(text[len(_FENCE) : end], fmt)
        if description is not None:
            return description
        body = text[end + len(_CLOSING) :]
    line = next((line.strip() for line in body.splitlines() if line.strip()), None)
    return line[: fmt.description_chars] if line else None


def _frontmatter_description(source: str, fmt: FileFormat) -> str | None:
    try:
        value = yaml.safe_load(source)
    except yaml.YAMLError as error:
        raise MemoryStoreError("invalid_file", "Frontmatter is not valid YAML.") from error
    if value is None:
        return None
    if not isinstance(value, dict):
        raise MemoryStoreError("invalid_file", "Frontmatter is a YAML mapping.")
    description = value.get("description")
    if description is None:
        return None
    if not isinstance(description, str) or "\n" in description.strip() or not description.strip():
        raise MemoryStoreError("invalid_file", "The frontmatter description is one non-empty line.")
    if len(description.strip()) > fmt.description_chars:
        raise MemoryStoreError("invalid_file", f"A description has at most {fmt.description_chars} characters.")
    return description.strip()
