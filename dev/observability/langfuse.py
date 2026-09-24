"""Wire the owned local Langfuse stack without changing Service configuration."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import subprocess
from contextlib import contextmanager
from pathlib import Path

import httpx2

from .state import atomic_write as _atomic_write
from .state import machine_directory as _machine_directory

ROOT = Path(__file__).resolve().parents[2]

# These are public local-only identities, not deployment credentials.
USER_EMAIL = "dev@agent-foundation.local"
USER_PASSWORD = "agent-foundation-local"
PUBLIC_KEY = "lf_pk_agent_foundation_local"
SECRET_KEY = "lf_sk_agent_foundation_local"
SHARED_PROJECT = "agent-foundation-local-langfuse-v2"
SHARED_CONFIG_VERSION = 1


class Langfuse:
    port = 3000

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def project(self) -> str:
        return SHARED_PROJECT

    @contextmanager
    def lock(self):
        directory = _machine_directory()
        directory.mkdir(parents=True, exist_ok=True)
        fd = os.open(directory / "langfuse.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def _project_resources(self) -> tuple[str, ...]:
        commands = (
            (
                "docker",
                "ps",
                "-a",
                "--filter",
                f"label=com.docker.compose.project={self.project}",
                "--format",
                "{{.ID}}",
            ),
            (
                "docker",
                "volume",
                "ls",
                "--filter",
                f"label=com.docker.compose.project={self.project}",
                "--format",
                "{{.Name}}",
            ),
        )
        return tuple(
            line
            for command in commands
            for line in subprocess.run(command, check=True, capture_output=True, text=True).stdout.splitlines()
            if line
        )

    def _shared_compose(self, *, initialize: bool, require_compatible_source: bool) -> Path | None:
        directory = _machine_directory()
        directory.mkdir(parents=True, exist_ok=True)
        compose = directory / "langfuse-v2.compose.yaml"
        manifest = directory / "langfuse-v2.json"
        if manifest.exists():
            try:
                value = json.loads(manifest.read_text())
            except (OSError, json.JSONDecodeError):
                raise ValueError(f"Invalid shared Langfuse configuration record: {manifest}") from None
            if not isinstance(value, dict) or type(value.get("compose_sha256")) is not str:
                raise ValueError(f"Invalid shared Langfuse configuration record: {manifest}")
            source_hash = (
                hashlib.sha256((ROOT / "dev/observability/langfuse.compose.yaml").read_bytes()).hexdigest()
                if require_compatible_source
                else value["compose_sha256"]
            )
            expected = {
                "version": SHARED_CONFIG_VERSION,
                "project": self.project,
                "compose_sha256": source_hash,
            }
            if value != expected or not compose.is_file():
                raise ValueError("Shared Langfuse configuration is missing or incompatible; no resources were changed")
            if hashlib.sha256(compose.read_bytes()).hexdigest() != value["compose_sha256"]:
                raise ValueError("Shared Langfuse compose artifact changed unexpectedly; no resources were changed")
            return compose
        source_content = (ROOT / "dev/observability/langfuse.compose.yaml").read_bytes()
        source_hash = hashlib.sha256(source_content).hexdigest()
        if not initialize:
            if self._project_resources():
                raise ValueError("Unmanaged shared Langfuse resources exist; they were not changed")
            return None
        if self._project_resources():
            raise ValueError("Unmanaged shared Langfuse resources exist; they were not adopted or changed")
        if compose.exists():
            if hashlib.sha256(compose.read_bytes()).hexdigest() != source_hash:
                raise ValueError("Partial shared Langfuse configuration is incompatible; no resources were changed")
        else:
            _atomic_write(compose, source_content)
        _atomic_write(
            manifest,
            (
                json.dumps(
                    {"version": SHARED_CONFIG_VERSION, "project": self.project, "compose_sha256": source_hash},
                    indent=2,
                    sort_keys=True,
                )
                + "\n"
            ).encode(),
        )
        return compose

    def _compose(self, compose: Path, *args: str, capture: bool = False) -> str:
        env = {key: value for key, value in os.environ.items() if not key.startswith("LANGFUSE_LOCAL_")}
        env.update(
            LANGFUSE_LOCAL_PORT=str(self.port),
            LANGFUSE_LOCAL_MINIO_PORT=str(self.port + 1),
            LANGFUSE_LOCAL_PUBLIC_KEY=PUBLIC_KEY,
            LANGFUSE_LOCAL_SECRET_KEY=SECRET_KEY,
            LANGFUSE_LOCAL_USER_EMAIL=USER_EMAIL,
            LANGFUSE_LOCAL_USER_PASSWORD=USER_PASSWORD,
        )
        result = subprocess.run(
            [
                "docker",
                "compose",
                "--env-file",
                os.devnull,
                "--project-name",
                self.project,
                "--file",
                str(compose),
                *args,
            ],
            env=env,
            check=True,
            text=True,
            stdout=subprocess.PIPE if capture else None,
        )
        return result.stdout or ""

    def start(self) -> None:
        subprocess.run(["docker", "info"], check=True, stdout=subprocess.DEVNULL)
        with self.lock():
            compose = self._shared_compose(initialize=True, require_compatible_source=True)
            assert compose is not None
            self._compose(compose, "up", "-d", "--wait", "--wait-timeout", "180")
            self.check_credentials()

    def stop(self, *, reset: bool = False) -> None:
        subprocess.run(["docker", "info"], check=True, stdout=subprocess.DEVNULL)
        with self.lock():
            compose = self._shared_compose(initialize=False, require_compatible_source=False)
            if compose is not None:
                self._compose(compose, "down", *(["--volumes"] if reset else []), "--remove-orphans")
                if reset:
                    (_machine_directory() / "langfuse-v2.json").unlink()
                    compose.unlink()

    def check_credentials(self) -> None:
        try:
            with httpx2.Client(trust_env=False, follow_redirects=False, timeout=5) as client:
                response = client.get(
                    self.base_url + "/api/public/projects",
                    auth=(
                        PUBLIC_KEY,
                        SECRET_KEY,
                    ),
                )
                response.raise_for_status()
        except httpx2.HTTPError:
            raise RuntimeError(
                "Local Langfuse project authentication failed. Check the shared fixture project keys; "
                "changing initialization keys does not rotate an existing project's credentials. "
                "Existing Langfuse data has not been reset."
            ) from None


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("up", "down", "check", "reset"))
    args = parser.parse_args()
    stack = Langfuse()
    if args.command == "up":
        stack.start()
    elif args.command == "down":
        stack.stop()
    elif args.command == "check":
        stack.check_credentials()
    else:
        stack.stop(reset=True)


if __name__ == "__main__":
    main()
