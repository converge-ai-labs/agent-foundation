"""Bounded ZIP extraction shared with the standard-library guest helper."""

import hashlib
import shutil
import stat
import zipfile
from pathlib import Path


def zip_members(
    source: zipfile.ZipFile, root: str, entries: tuple[tuple[str, int], ...]
) -> list[tuple[str, zipfile.ZipInfo]]:
    """Resolve an exact manifest before creating any destination files."""
    if root:
        zip_path(root.removesuffix("/"))
        if not root.endswith("/"):
            raise ValueError("archive root must end with a separator")
    members = {item.filename: item for item in source.infolist()}
    if len(members) != len(source.infolist()):
        raise ValueError("duplicate archive members")
    selected = []
    seen = set()
    for path, size in entries:
        zip_path(path)
        if path.casefold() in seen or size < 0:
            raise ValueError("invalid archive manifest")
        seen.add(path.casefold())
        item = members.get(root + path)
        if item is None or item.is_dir() or item.flag_bits & 1 or item.filename != item.orig_filename:
            raise ValueError("archive member is missing or unsupported")
        kind = stat.S_IFMT(item.external_attr >> 16)
        if kind not in (0, stat.S_IFREG) or item.file_size != size:
            raise ValueError("archive member differs from its manifest")
        selected.append((path, item))
    return selected


def zip_path(path: str) -> None:
    if not path or "\\" in path or "\x00" in path or ":" in path.split("/", 1)[0]:
        raise ValueError("invalid archive path")
    if any(part in ("", ".", "..") for part in path.split("/")):
        raise ValueError("archive path escapes its destination")


def extract_zip(
    archive: Path,
    destination: Path,
    *,
    digest: str,
    root: str,
    entries: tuple[tuple[str, int], ...],
) -> None:
    """Extract only the manifest into a new directory; never restore archive permissions or links."""
    with archive.open("rb") as data:
        checksum = hashlib.sha256()
        while chunk := data.read(65536):
            checksum.update(chunk)
        if checksum.hexdigest() != digest:
            raise ValueError("archive checksum mismatch")
        data.seek(0)
        with zipfile.ZipFile(data) as source:
            selected = zip_members(source, root, entries)
            destination.mkdir(parents=True)
            for path, item in selected:
                target = destination / path
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.open(item) as reader, target.open("xb") as writer:
                    # zipfile stops at the declared size and checks its CRC; a shorter member still differs.
                    shutil.copyfileobj(reader, writer)
                    if writer.tell() != item.file_size:
                        raise ValueError("archive member differs from its manifest")
