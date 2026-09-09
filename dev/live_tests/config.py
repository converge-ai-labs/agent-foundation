"""Persistent, private configuration for the local live-test installation."""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlsplit

CONFIG = Path(os.environ.get("LIVE_TEST_CONFIG", Path(__file__).parent / ".state" / "config.json")).resolve()
STATE = CONFIG.parent


def local_origin(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Live tests require a loopback HTTP origin without credentials or a path")
    return value.rstrip("/")


def load_config() -> dict:
    if not CONFIG.is_file():
        raise RuntimeError("Missing live-test configuration. Run make live-test-init first.")
    config = json.loads(CONFIG.read_text())
    for role in ("control", "worker"):
        config[f"{role}_url"] = local_origin(os.environ.get(f"LIVE_TEST_{role.upper()}_URL", config[f"{role}_url"]))
    if config["control_url"] == config["worker_url"]:
        raise ValueError("Control and Worker must be separate service origins")
    return config


def save_config(config: dict) -> None:
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = CONFIG.with_suffix(".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as output:
        json.dump(config, output, indent=2)
        output.write("\n")
    temporary.replace(CONFIG)
