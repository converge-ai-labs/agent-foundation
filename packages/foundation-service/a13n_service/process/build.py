"""Immutable execution identity derived from installed artifacts, not deployment settings."""

from __future__ import annotations

import hashlib
from importlib.metadata import distributions
from pathlib import Path

import a13n_harness


def worker_build_id() -> str:
    """Hash executable package contents and the installed dependency version inventory once at startup.

    Call off the event loop. Relative paths and sorted versions make replicas of the
    same artifact agree; neither a runtime environment variable nor a checkout path
    can masquerade as another build. Source installs are intentionally content-addressed too.
    """

    digest = hashlib.sha256(b"foundation-worker-build-v1\0")
    roots = {"a13n_service": Path(__file__).resolve().parents[1], "a13n_harness": Path(a13n_harness.__file__).parent}
    for name, root in sorted(roots.items()):
        for path in sorted(root.rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
                continue
            digest.update(f"{name}/{path.relative_to(root).as_posix()}\0".encode())
            digest.update(hashlib.sha256(path.read_bytes()).digest())
    inventory = sorted((item.metadata["Name"].lower(), item.version) for item in distributions())
    for name, version in inventory:
        digest.update(f"{name}=={version}\0".encode())
    return f"build-{digest.hexdigest()}"
