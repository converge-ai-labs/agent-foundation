"""Ensure the selected Docker daemon is ready for local development."""

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


def _ready(timeout: float = 5) -> bool:
    try:
        return (
            subprocess.run(
                ["docker", "info"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout,
                check=False,
            ).returncode
            == 0
        )
    except subprocess.TimeoutExpired:
        return False


def ensure_docker() -> None:
    if shutil.which("docker") is None:
        raise RuntimeError("Docker CLI is missing. Install Docker before starting local development.")
    if _ready():
        return
    guidance = "Docker is unavailable. Start the daemon selected by your Docker context or DOCKER_HOST, then retry."
    if sys.platform != "darwin":
        raise RuntimeError(guidance)

    # Respect Docker's context override precedence; never switch the user's context.
    host = os.environ.get("DOCKER_HOST") if not os.environ.get("DOCKER_CONTEXT") else None
    if not host:
        try:
            host = subprocess.run(
                ["docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"],
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            ).stdout.strip()
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            raise RuntimeError(guidance) from error
    if host not in {"unix:///var/run/docker.sock", f"unix://{Path.home()}/.docker/run/docker.sock"}:
        raise RuntimeError(guidance)

    print("Docker is not ready; starting Docker Desktop...", flush=True)
    try:
        subprocess.run(["open", "-g", "-a", "Docker"], check=True, timeout=10)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise RuntimeError("Could not open Docker Desktop. Install or start Docker Desktop, then retry.") from error

    print("Waiting up to 120 seconds for Docker...", flush=True)
    deadline = time.monotonic() + 120
    while (remaining := deadline - time.monotonic()) > 0:
        if _ready(timeout=min(5, remaining)):
            print("Docker is ready.", flush=True)
            return
        time.sleep(min(2, max(0, deadline - time.monotonic())))
    raise RuntimeError("Docker did not become ready within 120 seconds. Check Docker Desktop and retry.")
