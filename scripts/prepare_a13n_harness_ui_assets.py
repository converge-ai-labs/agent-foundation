"""Importable implementation for the Harness UI asset preparation command."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from pathlib import Path

MANIFEST_NAME = "asset-manifest.json"


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare_assets(source: Path, target: Path) -> None:
    source = source.resolve()
    target = target.resolve()
    if not (source / "index.html").is_file():
        raise ValueError(f"Harness UI WebUI build is missing index.html: {source}")

    source_files = sorted(path for path in source.rglob("*") if path.is_file())
    if not source_files:
        raise ValueError(f"Harness UI WebUI build contains no files: {source}")
    if any(path.is_symlink() for path in source.rglob("*")):
        raise ValueError(f"Harness UI WebUI build must not contain symbolic links: {source}")

    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    temporary.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copytree(source, temporary)
        files = {path.relative_to(source).as_posix(): _digest(path) for path in source_files}
        manifest = {
            "schema_version": "1",
            "source": "apps/a13n-harness-ui",
            "files": files,
        }
        (temporary / MANIFEST_NAME).write_text(
            f"{json.dumps(manifest, indent=2, sort_keys=True)}\n",
            encoding="utf-8",
        )
        if target.exists():
            shutil.rmtree(target)
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
