"""Terminal-only logging and post-cleanup resume hints."""

from __future__ import annotations

import asyncio
import logging
import os
import shlex
import subprocess
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path

from a13n_logging import JsonFormatter

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
