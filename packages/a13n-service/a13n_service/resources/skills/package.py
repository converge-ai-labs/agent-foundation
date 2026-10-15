"""The skill package contract: a zip whose root, or only top-level directory, holds SKILL.md.

Every member is checked before it is decompressed, and never beyond its declared size, so a hostile archive
cannot expand without bound, carry links, or place a file outside its package directory when materialized.
These functions are CPU-bound; callers run them in a worker thread.
"""

import io
import ntpath
import re
import stat
import zipfile
import zlib
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass

import yaml
from a13n_harness.capabilities.skills import SkillCatalogItem
from pydantic import ValidationError

from a13n_service.infra.errors import ServiceError, invalid

MAX_FILES = 1000
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_EXPANDED_BYTES = 32 * 1024 * 1024
MAX_DOCUMENT_BYTES = 256 * 1024
MAX_PATH_BYTES = 1024

DOCUMENT = "SKILL.md"
# macOS adds resource forks here when compressing a folder; they are never package content.
_IGNORED = "__MACOSX/"
_DRIVE = re.compile(r"^[A-Za-z]:")


@dataclass(frozen=True, slots=True)
class Package:
    name: str
    description: str
    root: str
    files: dict[str, bytes]


def read_package(archive: bytes) -> Package:
    """Validate an archive against the package contract and return its files by path below the root."""
    with _open(archive) as source:
        root = _root(source)
        files = _read(source, root)
    name, description = _frontmatter(files[DOCUMENT])
    return Package(name=name, description=description, root=root, files=files)


def read_directory(archive: bytes, directory: str) -> dict[str, bytes]:
    """The files below `directory` of an archive with one top-level directory, as GitHub zipballs have."""
    with _open(archive) as source:
        names = source.namelist()
        top = names[0].split("/", 1)[0] + "/" if names else ""
        return _read(source, f"{top}{directory}/" if directory else top)


def pack(files: Mapping[str, bytes]) -> bytes:
    """A deterministic archive: the same files always produce the same bytes, so staging them again is a no-op."""
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        for path in sorted(files):
            info = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, files[path])
    return output.getvalue()


def extract(archive: bytes, root: str, paths: Iterable[str]) -> dict[str, bytes]:
    """Files of an already validated package, by path below its root."""
    with _open(archive) as source:
        return {path: source.read(root + path) for path in paths}


@contextmanager
def _open(archive: bytes) -> Iterator[zipfile.ZipFile]:
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as source:
            yield source
    except (zipfile.BadZipFile, zipfile.LargeZipFile, EOFError, zlib.error, NotImplementedError, ValueError):
        raise invalid("package", "archive is not a readable zip") from None


def _root(source: zipfile.ZipFile) -> str:
    names = {
        info.filename for info in source.infolist() if not info.is_dir() and not info.filename.startswith(_IGNORED)
    }
    if DOCUMENT in names:
        return ""
    tops = {name.split("/", 1)[0] for name in names}
    if len(tops) == 1 and f"{next(iter(tops))}/{DOCUMENT}" in names:
        return f"{tops.pop()}/"
    raise invalid("package", "SKILL.md must be at the archive root or in its only top-level directory")


def _read(source: zipfile.ZipFile, prefix: str) -> dict[str, bytes]:
    members = [
        info
        for info in source.infolist()
        if not info.is_dir() and info.filename.startswith(prefix) and not info.filename.startswith(_IGNORED)
    ]
    if len(members) > MAX_FILES:
        raise _too_large("file count", MAX_FILES)
    files: dict[str, bytes] = {}
    folded: set[str] = set()
    total = 0
    for info in members:
        path = _checked_path(info, prefix)
        if path.casefold() in folded:
            raise invalid("package", "archive paths collide")
        folded.add(path.casefold())
        if info.file_size > MAX_FILE_BYTES:
            raise _too_large("file size", MAX_FILE_BYTES)
        total += info.file_size
        if total > MAX_EXPANDED_BYTES:
            raise _too_large("expanded size", MAX_EXPANDED_BYTES)
        with source.open(info) as member:
            content = member.read(info.file_size + 1)
        if len(content) != info.file_size:
            raise invalid("package", "an archive member does not match its declared size")
        files[path] = content
    return files


def _checked_path(info: zipfile.ZipInfo, prefix: str) -> str:
    if info.flag_bits & 0x1:
        raise invalid("package", "encrypted archive members are not supported")
    if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
        raise invalid("package", "archive members must be stored or deflated")
    if info.create_system == 3 and stat.S_IFMT(info.external_attr >> 16) not in {0, stat.S_IFREG}:
        raise invalid("package", "archives may contain only regular files")
    path = info.filename[len(prefix) :]
    # zipfile truncates names at NUL and rewrites separators; any rewrite means the stored name was unsafe.
    if (
        info.filename != info.orig_filename
        or "\\" in path
        or _DRIVE.match(path)
        or len(path.encode()) > MAX_PATH_BYTES
        or any(segment in {"", ".", ".."} for segment in path.split("/"))
        or any(ord(character) < 32 or character == "\x7f" for character in path)
    ):
        raise invalid("package", "archive paths must be relative and stay inside the package")
    return path


def _frontmatter(document: bytes) -> tuple[str, str]:
    if len(document) > MAX_DOCUMENT_BYTES:
        raise _too_large("SKILL.md size", MAX_DOCUMENT_BYTES)
    try:
        lines = document.decode().lstrip("\ufeff").splitlines()
    except UnicodeDecodeError:
        raise invalid("package", "SKILL.md must be UTF-8") from None
    if not lines or lines[0].strip() != "---":
        raise invalid("package", "SKILL.md must begin with YAML frontmatter")
    closing = next((index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"), None)
    if closing is None:
        raise invalid("package", "SKILL.md frontmatter is not closed")
    try:
        value = yaml.safe_load("\n".join(lines[1:closing]))
    except yaml.YAMLError:
        raise invalid("package", "SKILL.md frontmatter is not valid YAML") from None
    if not isinstance(value, dict):
        raise invalid("package", "SKILL.md frontmatter must be a mapping")
    try:
        # The Harness owns what a skill catalog entry may carry.
        item = SkillCatalogItem.model_validate(
            {"name": value.get("name"), "description": value.get("description"), "path": DOCUMENT}
        )
    except ValidationError:
        raise invalid("package", "SKILL.md frontmatter needs a valid name and description") from None
    validate_name(item.name)
    return item.name, item.description


def validate_name(name: str) -> None:
    """A skill name is a portable directory component, including on Windows."""
    if (
        not name
        or name in {".", ".."}
        or "/" in name
        or "\\" in name
        or ntpath.isreserved(name)
        or len(name.encode()) > 255
    ):
        raise invalid("package", "skill name must be a portable directory name")


def _too_large(limit: str, value: int) -> ServiceError:
    return ServiceError("payload_too_large", f"Skill package exceeds its {limit} limit", {"limit": value})
