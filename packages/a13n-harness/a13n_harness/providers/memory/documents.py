"""Versioned document values shared by file stores and their host-owned tools."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from a13n_harness.providers.environment.models import EnvironmentError
from a13n_harness.providers.environment.text import apply_unified_diff

DocumentKind = Literal["semantic", "procedural", "episodic"]
MAX_DOCUMENT_BYTES = 256 * 1024
MAX_READ_BYTES = 32 * 1024


class MemoryDocumentError(Exception):
    """Bounded public failure; never includes paths, content, or transport diagnostics."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _Value(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Replace(_Value):
    type: Literal["replace"]
    text: str = Field(max_length=MAX_DOCUMENT_BYTES)


class Append(_Value):
    type: Literal["append"]
    text: str = Field(max_length=MAX_DOCUMENT_BYTES)


class Replacement(_Value):
    old_string: str = Field(min_length=1, max_length=MAX_DOCUMENT_BYTES)
    new_string: str = Field(max_length=MAX_DOCUMENT_BYTES)
    replace_all: bool = False


class Edit(_Value):
    type: Literal["edit"]
    edits: tuple[Replacement, ...] = Field(min_length=1, max_length=256)


class Patch(_Value):
    type: Literal["patch"]
    patch: str = Field(min_length=1, max_length=MAX_DOCUMENT_BYTES)


DocumentChange = Annotated[Replace | Append | Edit | Patch, Field(discriminator="type")]


def revise_text(text: str, change: DocumentChange) -> str:
    """Apply a complete batch before publication, preserving exact accepted bytes."""
    match change:
        case Replace():
            candidate = change.text
        case Append():
            candidate = text + change.text
        case Edit():
            candidate = text
            for edit in change.edits:
                count = candidate.count(edit.old_string)
                if count == 0 or (count > 1 and not edit.replace_all):
                    raise MemoryDocumentError("memory_edit_conflict")
                candidate = candidate.replace(edit.old_string, edit.new_string, -1 if edit.replace_all else 1)
                _body(candidate)
        case Patch():
            try:
                _validate_patch(change.patch)
                candidate, _ = apply_unified_diff(text, change.patch, max_result_bytes=MAX_DOCUMENT_BYTES)
            except (EnvironmentError, ValueError, TypeError) as error:
                raise MemoryDocumentError("memory_patch_invalid") from error
    return _body(candidate)


def _validate_patch(patch: str) -> None:
    """Check one-document hunk structure before the shared exact text transform."""
    lines = patch.splitlines(keepends=True)
    if lines and lines[0].startswith("--- "):
        if len(lines) < 2 or not lines[1].startswith("+++ ") or any("/dev/null" in line for line in lines[:2]):
            raise ValueError("Invalid document patch headers")
        lines = lines[2:]
    index = 0
    delta = 0
    hunks = 0
    while index < len(lines):
        match = re.fullmatch(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?: [^\r\n]*)?\r?\n?", lines[index])
        if match is None:
            raise ValueError("Invalid document patch hunk")
        old_start, old_count = int(match[1]), int(match[2] or "1")
        new_start, new_count = int(match[3]), int(match[4] or "1")
        old_position = old_start - (1 if old_count else 0)
        new_position = new_start - (1 if new_count else 0)
        if old_position < 0 or new_position < 0 or new_position != old_position + delta:
            raise ValueError("Invalid successor hunk position")
        index += 1
        removed = added = 0
        while index < len(lines) and not lines[index].startswith("@@"):
            line = lines[index]
            if line.startswith(" "):
                removed += 1
                added += 1
            elif line.startswith("-"):
                removed += 1
            elif line.startswith("+"):
                added += 1
            elif line.rstrip("\r\n") != "\\ No newline at end of file":
                raise ValueError("Invalid patch line")
            index += 1
        if (removed, added) != (old_count, new_count):
            raise ValueError("Patch hunk counts do not match")
        delta += new_count - old_count
        hunks += 1
    if not hunks:
        raise ValueError("A document patch requires a hunk")


def _body(value: str) -> str:
    if not value.strip() or "\x00" in value or len(value.encode("utf-8")) > MAX_DOCUMENT_BYTES:
        raise MemoryDocumentError("memory_document_invalid")
    return value


class DocumentInput(_Value):
    kind: DocumentKind
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(max_length=320)
    text: str = Field(min_length=1, max_length=MAX_DOCUMENT_BYTES)
    path: str = Field(min_length=1, max_length=512)
    sources: tuple[str, ...] = Field(default=(), max_length=64)
    applicability: str | None = Field(default=None, max_length=2000)
    event_time: datetime | None = None
    effective_time: datetime | None = None
    correction_of: str | None = Field(default=None, max_length=128)

    @field_validator("title")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Title must not be blank")
        return value

    @field_validator("text")
    @classmethod
    def bounded_body(cls, value: str) -> str:
        return _body(value)

    @field_validator("path")
    @classmethod
    def logical_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if (
            path.is_absolute()
            or str(path) != value
            or "\\" in value
            or any(part.startswith(".") for part in path.parts)
            or path.name == "_index.md"
            or path.suffix != ".md"
            or path.parts[0] not in {"semantic", "procedural", "episodic"}
        ):
            raise ValueError("Use a normalized document path within a memory kind")
        return value

    @field_validator("sources")
    @classmethod
    def bounded_sources(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not source.strip() or len(source) > 2048 for source in value):
            raise ValueError("Invalid source reference")
        return value


class FileMemoryDocument(DocumentInput):
    schema_version: Literal[1] = 1
    id: str
    version: int = Field(ge=1)
    scope: str
    store_id: str
    created_at: datetime
    saved_at: datetime
    principal: str

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


Document = FileMemoryDocument


class DocumentRead(_Value):
    id: str
    version: int
    digest: str
    title: str
    path: str
    text: str
    start: int
    next_start: int | None


class DocumentHeading(_Value):
    title: str
    level: int
    locator: str
    start: int
    end: int


class DocumentMutation(_Value):
    document: Document
    change_id: str | None
    unchanged: bool = False
    indexed: bool = False
