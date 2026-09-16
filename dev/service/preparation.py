"""Incremental checkout-local Python and frontend dependency installation."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .state import atomic_write


@dataclass(frozen=True, slots=True)
class Phase:
    name: str
    inputs: tuple[Path, ...]
    outputs: tuple[str, ...]
    command: tuple[str, ...]


def _fingerprint(root: Path, phase: Phase) -> str:
    digest = hashlib.sha256()
    for path in phase.inputs:
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _outputs_exist(root: Path, phase: Phase) -> bool:
    return all(any(path.exists() for path in root.glob(pattern)) for pattern in phase.outputs)


def _run_phase(root: Path, phase: Phase, stamps: dict[str, str]) -> tuple[str, str, float, bool]:
    started = time.monotonic()
    fingerprint = _fingerprint(root, phase)
    skipped = stamps.get(phase.name) == fingerprint and _outputs_exist(root, phase)
    if not skipped:
        subprocess.run(phase.command, cwd=root, check=True)
        if not _outputs_exist(root, phase):
            raise RuntimeError(f"Preparation phase did not create its required outputs: {phase.name}")
    return phase.name, fingerprint, time.monotonic() - started, skipped


@contextmanager
def _lock(root: Path):
    directory = root / "var/dev"
    directory.mkdir(parents=True, exist_ok=True)
    fd = os.open(directory / "preparation.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _phase_inputs(root: Path, patterns: tuple[str, ...]) -> tuple[Path, ...]:
    return tuple(path for pattern in patterns for path in sorted(root.glob(pattern)) if path.is_file())


def _phases(root: Path, *, console: bool) -> tuple[Phase, ...]:
    python = Phase(
        "python-install",
        _phase_inputs(root, ("pyproject.toml", "uv.lock", "uv.toml", ".python-version", "packages/*/pyproject.toml")),
        (
            ".venv/bin/python",
            ".venv/lib/python*/site-packages/_editable_impl_a13n_service.pth",
            ".venv/lib/python*/site-packages/a13n_service-*.dist-info",
        ),
        ("uv", "sync", "--quiet", "--locked", "--all-packages"),
    )
    if not console:
        return (python,)
    frontend = Phase(
        "frontend-install",
        _phase_inputs(
            root,
            (
                "frontend/package.json",
                "frontend/pnpm-lock.yaml",
                "frontend/pnpm-workspace.yaml",
                "frontend/.npmrc",
                "frontend/.pnpmfile.cjs",
                "frontend/apps/*/package.json",
                "frontend/packages/*/package.json",
            ),
        ),
        (
            "frontend/node_modules/.pnpm/lock.yaml",
            "frontend/apps/a13n-console/node_modules/vite/package.json",
            "frontend/apps/a13n-console/node_modules/react/package.json",
        ),
        ("pnpm", "--dir", "frontend", "install", "--frozen-lockfile"),
    )
    return (python, frontend)


def prepare(root: Path, *, console: bool) -> None:
    with _lock(root):
        state = root / "var/dev/preparation.json"
        try:
            value = json.loads(state.read_text())
            stamps = value if isinstance(value, dict) and all(type(item) is str for item in value.values()) else {}
        except (OSError, json.JSONDecodeError):
            stamps = {}
        phases = _phases(root, console=console)
        with ThreadPoolExecutor(max_workers=len(phases)) as pool:
            results = list(pool.map(lambda phase: _run_phase(root, phase, stamps), phases))
        for name, fingerprint, elapsed, skipped in results:
            stamps[name] = fingerprint
            status = "skipped" if skipped else "completed"
            print(f"Preparation {name}: {status} in {elapsed:.2f}s", flush=True)
        atomic_write(state, (json.dumps(stamps, indent=2, sort_keys=True) + "\n").encode())
