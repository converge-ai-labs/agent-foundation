"""Safe normalization for managed Skill ZIP and file-tree sources."""

from __future__ import annotations

import hashlib
import io
import json
import re
import stat
import unicodedata
import zipfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Literal

from a13n_harness.capabilities import parse_skill_frontmatter
from a13n_harness.errors import DefinitionError

from .domain import ManagedSkillPackageFile, ManagedSkillPackageManifest

MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 8192
MAX_FILES = 4096
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_SKILL_DOCUMENT_BYTES = 256 * 1024
MAX_PATH_DEPTH = 32
MAX_PATH_BYTES = 1024
MAX_SEGMENT_BYTES = 255

_CONTENT_DIGEST_PREFIX = b"a13n.managed-skill-package.v1\n"
_SUPPORTED_COMPRESSION = frozenset({zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED})
_SKILL_FILE_CASEFOLD = "skill.md"
_URI_OR_DRIVE_PREFIX = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_WINDOWS_DEVICE_NAMES = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{number}" for number in range(1, 10)}
    | {f"lpt{number}" for number in range(1, 10)}
)
type SkillPackageErrorCode = Literal["skill_package_invalid", "skill_package_limit"]


class SkillPackageError(ValueError):
    """A source cannot be normalized into a complete managed Skill package."""

    def __init__(self, code: SkillPackageErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class NormalizedSkillFile:
    path: str
    content: bytes


@dataclass(frozen=True, slots=True)
class NormalizedSkillPackage:
    manifest: ManagedSkillPackageManifest
    files: tuple[NormalizedSkillFile, ...]
    archive_bytes: bytes


@dataclass(frozen=True, slots=True)
class _ArchiveEntry:
    info: zipfile.ZipInfo
    path: str


def normalize_skill_zip(archive: bytes) -> NormalizedSkillPackage:
    """Normalize one bounded ZIP body into deterministic immutable package bytes."""

    if len(archive) > MAX_ARCHIVE_BYTES:
        raise _limit("uploaded ZIP exceeds the package size limit")
    try:
        with zipfile.ZipFile(io.BytesIO(archive), "r") as source:
            entries = _validated_archive_entries(source)
            wrapper = _wrapper_directory(entries)
            files = _read_archive_files(source, entries, wrapper=wrapper)
    except SkillPackageError:
        raise
    except (OSError, UnicodeError, zipfile.BadZipFile, zipfile.LargeZipFile) as error:
        raise _invalid("uploaded ZIP is malformed") from error
    return normalize_skill_files((item.path, item.content) for item in files)


def normalize_skill_files(files: Iterable[tuple[str, bytes]]) -> NormalizedSkillPackage:
    """Normalize one source-relative regular-file tree under the shared package contract."""

    items = tuple(files)
    if len(items) > MAX_FILES:
        raise _limit("package contains too many regular files")

    normalized: dict[str, bytes] = {}
    casefolded: dict[str, str] = {}
    total_size = 0
    for raw_path, content in items:
        if not isinstance(content, bytes):
            raise TypeError("managed Skill file content must be bytes")
        path = normalize_skill_path(raw_path)
        folded = path.casefold()
        previous = casefolded.setdefault(folded, path)
        if previous != path or path in normalized:
            raise _invalid("package contains duplicate or case-folded-colliding paths")
        size = len(content)
        if size > MAX_FILE_BYTES:
            raise _limit("package contains a file larger than the per-file limit")
        if path == "SKILL.md" and size > MAX_SKILL_DOCUMENT_BYTES:
            raise _limit("SKILL.md exceeds its size limit")
        total_size += size
        if total_size > MAX_TOTAL_BYTES:
            raise _limit("package expanded content exceeds the total size limit")
        normalized[path] = content

    _require_one_root_skill_document(normalized)
    skill_name, description = _validate_skill_document(normalized["SKILL.md"])
    ordered_files = tuple(NormalizedSkillFile(path=path, content=normalized[path]) for path in sorted(normalized))
    file_manifest = tuple(
        ManagedSkillPackageFile(
            path=item.path,
            size_bytes=len(item.content),
            sha256=hashlib.sha256(item.content).hexdigest(),
        )
        for item in ordered_files
    )
    manifest_payload = {
        "schema_version": "1",
        "skill_name": skill_name,
        "description": description,
        "harness_skill_contract": "1",
        "files": [item.model_dump(mode="json") for item in file_manifest],
        "total_size_bytes": total_size,
    }
    content_digest = hashlib.sha256(_CONTENT_DIGEST_PREFIX + _canonical_json(manifest_payload)).hexdigest()
    manifest = ManagedSkillPackageManifest(**manifest_payload, content_digest=content_digest)
    return NormalizedSkillPackage(
        manifest=manifest,
        files=ordered_files,
        archive_bytes=_deterministic_zip(ordered_files),
    )


def skill_package_object_key(organization_id: str, workspace_id: str, content_digest: str) -> str:
    """Derive the private version-1 package key after an authorized tenant lookup."""

    if re.fullmatch(r"org_[a-z0-9]{16,64}", organization_id) is None:
        raise ValueError("organization_id must be an Organization ID")
    if re.fullmatch(r"ws_[a-z0-9]{16,64}", workspace_id) is None:
        raise ValueError("workspace_id must be a Workspace ID")
    if re.fullmatch(r"[0-9a-f]{64}", content_digest) is None:
        raise ValueError("content_digest must be a lowercase SHA-256 value")
    return f"tenants/{organization_id}/workspaces/{workspace_id}/skills/packages/version-1/{content_digest}.zip"


def normalize_skill_path(raw_path: str) -> str:
    """Return one canonical portable relative path under package-contract version 1."""

    if not isinstance(raw_path, str) or not raw_path:
        raise _invalid("package path must be a non-empty string")
    if "\\" in raw_path or raw_path.startswith("/") or _URI_OR_DRIVE_PREFIX.match(raw_path):
        raise _invalid("package path uses an absolute, URI, drive, or backslash form")
    path = unicodedata.normalize("NFC", raw_path)
    segments = path.split("/")
    if len(segments) > MAX_PATH_DEPTH:
        raise _limit("package path exceeds the depth limit")
    if any(not segment or segment in {".", ".."} for segment in segments):
        raise _invalid("package path contains an empty or relative segment")
    for segment in segments:
        _validate_path_segment(segment)
    if _utf8_size(path) > MAX_PATH_BYTES:
        raise _limit("package path exceeds the UTF-8 size limit")
    return path


def _validated_archive_entries(source: zipfile.ZipFile) -> tuple[_ArchiveEntry, ...]:
    infos = tuple(source.infolist())
    if len(infos) > MAX_ARCHIVE_MEMBERS:
        raise _limit("ZIP contains too many members")
    entries: list[_ArchiveEntry] = []
    normalized_paths: set[str] = set()
    folded_paths: set[str] = set()
    declared_total = 0
    regular_files = 0
    for info in infos:
        path = _validated_archive_path(info)
        folded = path.casefold()
        if path in normalized_paths or folded in folded_paths:
            raise _invalid("ZIP contains duplicate or case-folded-colliding members")
        normalized_paths.add(path)
        folded_paths.add(folded)
        if not info.is_dir():
            regular_files += 1
            if regular_files > MAX_FILES:
                raise _limit("package contains too many regular files")
            if info.file_size > MAX_FILE_BYTES:
                raise _limit("package contains a file larger than the per-file limit")
            declared_total += info.file_size
            if declared_total > MAX_TOTAL_BYTES:
                raise _limit("package expanded content exceeds the total size limit")
        entries.append(_ArchiveEntry(info=info, path=path))
    return tuple(entries)


def _validated_archive_path(info: zipfile.ZipInfo) -> str:
    if info.flag_bits & 0x1:
        raise _invalid("encrypted ZIP members are not supported")
    if info.compress_type not in _SUPPORTED_COMPRESSION:
        raise _invalid("ZIP member uses unsupported compression")
    if info.volume != 0:
        raise _invalid("multi-disk ZIP archives are not supported")
    if "\x00" in info.orig_filename:
        raise _invalid("ZIP member name contains NUL")
    _require_ordinary_zip_member(info)
    return normalize_skill_path(info.orig_filename.rstrip("/"))


def _wrapper_directory(entries: tuple[_ArchiveEntry, ...]) -> str | None:
    file_paths = tuple(item.path for item in entries if not item.info.is_dir())
    if "SKILL.md" in file_paths:
        return None
    top_levels = {item.path.split("/", 1)[0] for item in entries}
    if len(top_levels) != 1 or any("/" not in path for path in file_paths):
        raise _invalid("ZIP must contain SKILL.md at its root or beneath one wrapper directory")
    return next(iter(top_levels))


def _read_archive_files(
    source: zipfile.ZipFile,
    entries: tuple[_ArchiveEntry, ...],
    *,
    wrapper: str | None,
) -> tuple[NormalizedSkillFile, ...]:
    files: list[NormalizedSkillFile] = []
    actual_total = 0
    for entry in entries:
        if entry.info.is_dir():
            continue
        path = entry.path
        if wrapper is not None:
            prefix = f"{wrapper}/"
            if not path.startswith(prefix):
                raise _invalid("ZIP contains a root sibling outside its wrapper directory")
            path = path[len(prefix) :]
        try:
            with source.open(entry.info, "r") as member:
                content = member.read(MAX_FILE_BYTES + 1)
        except (RuntimeError, zipfile.BadZipFile) as error:
            raise _invalid("ZIP member content is invalid") from error
        if len(content) > MAX_FILE_BYTES:
            raise _limit("package contains a file larger than the per-file limit")
        if len(content) != entry.info.file_size:
            raise _invalid("ZIP member size does not match its metadata")
        actual_total += len(content)
        if actual_total > MAX_TOTAL_BYTES:
            raise _limit("package expanded content exceeds the total size limit")
        files.append(NormalizedSkillFile(path=path, content=content))
    return tuple(files)


def _require_ordinary_zip_member(info: zipfile.ZipInfo) -> None:
    if info.create_system != 3:
        return
    mode = info.external_attr >> 16
    kind = stat.S_IFMT(mode)
    expected = stat.S_IFDIR if info.is_dir() else stat.S_IFREG
    if kind not in {0, expected}:
        raise _invalid("ZIP contains a link or other non-regular member")


def _validate_path_segment(segment: str) -> None:
    if segment.endswith((" ", ".")):
        raise _invalid("package path segment ends in a space or dot")
    if any(unicodedata.category(character) == "Cc" for character in segment):
        raise _invalid("package path contains a control character")
    if _utf8_size(segment) > MAX_SEGMENT_BYTES:
        raise _limit("package path segment exceeds the UTF-8 size limit")
    if segment.casefold().split(".", 1)[0] in _WINDOWS_DEVICE_NAMES:
        raise _invalid("package path uses a reserved Windows device name")


def _utf8_size(value: str) -> int:
    try:
        return len(value.encode("utf-8"))
    except UnicodeEncodeError as error:
        raise _invalid("package path is not valid Unicode") from error


def _require_one_root_skill_document(files: Mapping[str, bytes]) -> None:
    if "SKILL.md" not in files:
        raise _invalid("package must contain one root SKILL.md")
    conflicting = [
        path for path in files if path != "SKILL.md" and path.rsplit("/", 1)[-1].casefold() == _SKILL_FILE_CASEFOLD
    ]
    if conflicting:
        raise _invalid("package must not contain another path ending in SKILL.md")


def _validate_skill_document(content: bytes) -> tuple[str, str]:
    try:
        document = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise _invalid("SKILL.md must be valid UTF-8") from error
    try:
        return parse_skill_frontmatter(document)
    except DefinitionError as error:
        raise _invalid("SKILL.md does not satisfy the Harness Skill contract") from error


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _deterministic_zip(files: tuple[NormalizedSkillFile, ...]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED, allowZip64=False) as archive:
        for item in files:
            info = zipfile.ZipInfo(item.path, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, item.content)
    return output.getvalue()


def _invalid(message: str) -> SkillPackageError:
    return SkillPackageError("skill_package_invalid", message)


def _limit(message: str) -> SkillPackageError:
    return SkillPackageError("skill_package_limit", message)
