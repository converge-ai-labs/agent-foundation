"""Terminal-only logging, advisory update checks, and post-cleanup resume hints."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
import subprocess
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from importlib.metadata import PackageNotFoundError, version
from logging.handlers import RotatingFileHandler
from pathlib import Path

import httpx2
from a13n_logging import JsonFormatter
from packaging.version import InvalidVersion, Version

from a13n_ui.cli import CliRequest


@contextmanager
def terminal_logging(root: Path, level: str, emit: Callable[[str], None]) -> Iterator[Path]:
    """Keep library logs off both terminal screens; restore embedding host state."""
    path = root / "logs" / "terminal.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    loop = asyncio.get_running_loop()
    seen: set[tuple[str, str]] = set()

    class Diagnostics(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            if record.getMessage() == "content_plugin_skipped":
                key = (str(record.__dict__.get("path", "")), str(record.__dict__.get("reason", "")))
                if key not in seen:
                    seen.add(key)
                    loop.call_soon_threadsafe(emit, f"Plugin skipped: {key[0]}\n{key[1]}\nDiagnostics: {path}")
            return True

    handler = RotatingFileHandler(path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    handler.setFormatter(JsonFormatter())
    handler.addFilter(Diagnostics())
    logger = logging.getLogger()
    previous, previous_level = logger.handlers[:], logger.level
    children = [
        (child, child.handlers[:], child.propagate)
        for child in logging.Logger.manager.loggerDict.copy().values()
        if isinstance(child, logging.Logger)
    ]
    # A previously configured namespaced logger can bypass the root entirely.
    # This executable owns the terminal while active, then restores host handlers.
    for child, _, _ in children:
        child.handlers = []
        child.propagate = True
    logger.handlers = [handler]
    logger.setLevel(level)
    try:
        yield path
    finally:
        logger.handlers = previous
        logger.setLevel(previous_level)
        for child, handlers, propagate in children:
            child.handlers = handlers
            child.propagate = propagate
        handler.close()


def installed_version() -> str | None:
    try:
        current = version("a13n-ui")
        parsed = Version(current)
        return None if parsed.base_version == "0.0.0" or parsed.is_devrelease else current
    except (PackageNotFoundError, InvalidVersion):
        return None


def update_notice(current: str, latest: str) -> str | None:
    try:
        candidate = Version(latest)
        if candidate.is_prerelease or candidate.is_devrelease or candidate <= Version(current):
            return None
    except InvalidVersion:
        return None
    return f"Update available: a13n-ui {current} → {candidate}. If installed with uv tool: uv tool upgrade a13n-ui"


async def check_update(root: Path, *, current: str | None = None) -> str | None:
    """Check public package metadata, with a daily cache and no install authority."""
    current = current or installed_version()
    if current is None:
        return None
    cache = root / "cache" / "terminal-update.json"
    try:
        with cache.open("rb") as stream:
            cached = json.loads(stream.read(8192))
        if isinstance(cached, dict) and 0 <= time.time() - float(cached["checked_at"]) < 86400:
            return update_notice(current, str(cached["latest"]))
    except (OSError, ValueError, KeyError, TypeError):
        pass
    try:
        async with asyncio.timeout(3), httpx2.AsyncClient(timeout=2, trust_env=False) as client:
            async with client.stream("GET", "https://pypi.org/pypi/a13n-ui/json") as response:
                response.raise_for_status()
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > 2 * 1024 * 1024:
                        return None
            payload = json.loads(body)
            latest = str(payload["info"]["version"])
            Version(latest)
        cache.parent.mkdir(parents=True, exist_ok=True)
        temporary = cache.with_suffix(f".{os.getpid()}.tmp")
        try:
            temporary.write_text(json.dumps({"checked_at": time.time(), "latest": latest}))
            temporary.replace(cache)
        finally:
            temporary.unlink(missing_ok=True)
        return update_notice(current, latest)
    except (TimeoutError, httpx2.HTTPError, OSError, ValueError, KeyError, TypeError):
        return None


def resume_hint(request: CliRequest, thread_id: str, directory: Path) -> str:
    args = ["a13n-ui"]
    if request.config_path is not None:
        args.extend(("--config", str(request.config_path.expanduser().resolve())))
    data_root = request.data_root or (
        Path(os.environ["A13N_UI_DATA_ROOT"]) if os.environ.get("A13N_UI_DATA_ROOT") else None
    )
    if data_root is not None:
        args.extend(("--data-root", str(data_root.expanduser().resolve())))
    args.extend(("--resume", thread_id))
    command = subprocess.list2cmdline(args) if os.name == "nt" else shlex.join(args)
    return f"To resume this session from {directory}:\n  {command}"
