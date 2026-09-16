"""Wire the owned local Langfuse stack without changing Service configuration."""

from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import os
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx2

from .environment import ROOT, Environment
from .state import atomic_write as _atomic_write
from .state import machine_directory as _machine_directory

# These are public local-only identities, not deployment credentials.
USER_EMAIL = "dev@agent-foundation.local"
USER_PASSWORD = "agent-foundation-local"
PUBLIC_KEY = "lf_pk_agent_foundation_local"
SECRET_KEY = "lf_sk_agent_foundation_local"
SHARED_PROJECT = "agent-foundation-local-langfuse-v2"
SHARED_CONFIG_VERSION = 1


@dataclass(frozen=True)
class Langfuse:
    environment: Environment

    @property
    def query(self):
        return self.environment.settings.observability.query

    @property
    def enabled(self) -> bool:
        return self.query.provider == "langfuse"

    @property
    def port(self) -> int:
        assert self.query.langfuse_base_url is not None
        return urlsplit(self.query.langfuse_base_url).port or 80

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

    def validate(self) -> None:
        if self.query.provider == "none":
            return
        if self.query.provider == "logfire":
            self.environment.settings.validate_trace_query_configuration()
            return
        if not self.enabled:
            raise ValueError(
                "Local development supports observability.query.provider = 'langfuse', 'logfire' or 'none'"
            )
        url = urlsplit(self.query.langfuse_base_url or "")
        if (
            url.scheme != "http"
            or url.hostname != "127.0.0.1"
            or url.port != 3000
            or url.username is not None
            or url.password is not None
            or url.path not in {"", "/"}
            or url.query
            or url.fragment
        ):
            raise ValueError("Shared local Langfuse requires http://127.0.0.1:3000 without a path or credentials")
        if self.query.langfuse_public_key is None or self.query.langfuse_secret_key is None:
            raise ValueError("Local Langfuse requires its public and secret project keys in observability.query")
        if (
            self.query.langfuse_public_key.get_secret_value() != PUBLIC_KEY
            or self.query.langfuse_secret_key.get_secret_value() != SECRET_KEY
        ):
            raise ValueError("Shared local Langfuse requires the repository's public fixture project keys")
        # The adjacent port exposes Langfuse media storage, not Service objects.
        settings = self.environment.settings
        assert settings.database.url is not None and settings.redis.url is not None
        occupied = {
            urlsplit(settings.database.url.get_secret_value()).port,
            urlsplit(settings.redis.url.get_secret_value()).port,
            settings.service.port,
            urlsplit(settings.iam.public_origin).port,
            self.environment.ports.model,
        }
        if {self.port, self.port + 1} & occupied:
            raise ValueError("Langfuse HTTP/media ports overlap Service, Console, model, PostgreSQL or Redis ports")

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
        self.validate()
        # Stopping an existing checkout stack must still work after opting out.
        if not self.enabled and (not args or args[0] not in {"stop", "down"}):
            raise ValueError("Select observability.query.provider = 'langfuse' in SERVICE_CONFIG first")
        env = {key: value for key, value in os.environ.items() if not key.startswith("LANGFUSE_LOCAL_")}
        if self.enabled:
            assert self.query.langfuse_public_key is not None and self.query.langfuse_secret_key is not None
            env.update(
                LANGFUSE_LOCAL_PORT=str(self.port),
                LANGFUSE_LOCAL_MINIO_PORT=str(self.port + 1),
                LANGFUSE_LOCAL_PUBLIC_KEY=self.query.langfuse_public_key.get_secret_value(),
                LANGFUSE_LOCAL_SECRET_KEY=self.query.langfuse_secret_key.get_secret_value(),
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
        if not self.enabled:
            return
        from .docker import ensure_docker

        ensure_docker()
        with self.lock():
            compose = self._shared_compose(initialize=True, require_compatible_source=True)
            assert compose is not None
            self._compose(compose, "up", "-d", "--wait", "--wait-timeout", "180")
            self.check_credentials()

    def stop(self, *, reset: bool = False) -> None:
        from .docker import ensure_docker

        ensure_docker()
        with self.lock():
            compose = self._shared_compose(initialize=False, require_compatible_source=False)
            if compose is not None:
                self._compose(compose, "down", *(["--volumes"] if reset else []), "--remove-orphans")
                if reset:
                    (_machine_directory() / "langfuse-v2.json").unlink()
                    compose.unlink()

    def check_credentials(self) -> None:
        assert self.query.langfuse_public_key is not None and self.query.langfuse_secret_key is not None
        try:
            with httpx2.Client(trust_env=False, follow_redirects=False, timeout=5) as client:
                response = client.get(
                    self.base_url + "/api/public/projects",
                    auth=(
                        self.query.langfuse_public_key.get_secret_value(),
                        self.query.langfuse_secret_key.get_secret_value(),
                    ),
                )
                response.raise_for_status()
        except httpx2.HTTPError:
            raise RuntimeError(
                "Local Langfuse project authentication failed. Check SERVICE_CONFIG project keys; "
                "changing initialization keys does not rotate an existing project's credentials. "
                "Existing Langfuse data has not been reset."
            ) from None

    def test(self) -> None:
        self.start()
        assert self.query.langfuse_public_key is not None and self.query.langfuse_secret_key is not None
        env = {
            **trace_environment(self),
            "A13N_TEST_LANGFUSE_BASE_URL": self.base_url,
            "A13N_TEST_LANGFUSE_PUBLIC_KEY": self.query.langfuse_public_key.get_secret_value(),
            "A13N_TEST_LANGFUSE_SECRET_KEY": self.query.langfuse_secret_key.get_secret_value(),
        }
        subprocess.run(
            [
                "uv",
                "run",
                "--locked",
                "python",
                "-m",
                "pytest",
                "packages/a13n-service/tests/trace_query/test_langfuse_integration.py",
            ],
            cwd=ROOT,
            env=env,
            check=True,
        )


def trace_environment(langfuse: Langfuse) -> dict[str, str]:
    """Local launchers own OTEL wiring; ambient collectors must never receive local data."""
    langfuse.validate()
    env = {key: value for key, value in os.environ.items() if not key.startswith("OTEL_")}
    env.update(
        OTEL_TRACES_EXPORTER="none",
        OTEL_METRICS_EXPORTER="none",
        OTEL_LOGS_EXPORTER="none",
        OTEL_RESOURCE_ATTRIBUTES=(
            "deployment.environment.name=" + langfuse.environment.settings.service.deployment_environment_name
        ),
    )
    # Requests (OTLP) and HTTPX (query/model) honor different casing precedence.
    # Preserve remote proxy policy while forcing local development traffic direct.
    bypass = ",".join(filter(None, [env.get("NO_PROXY"), env.get("no_proxy"), "127.0.0.1,localhost,::1"]))
    env.update(NO_PROXY=bypass, no_proxy=bypass)
    backend = os.environ.get("A13N_DEV_TRACE_BACKEND", langfuse.query.provider)
    if backend not in {"langfuse", "logfire", "none"}:
        raise ValueError("A13N_DEV_TRACE_BACKEND must be langfuse, logfire or none")
    tracing = langfuse.environment.settings.observability.tracing
    if backend == "logfire" and tracing:
        token = os.environ.get("LOGFIRE_TOKEN", "").strip()
        if not token:
            raise ValueError("Set LOGFIRE_TOKEN to a project write token before selecting the Logfire dev profile")
        env.update(
            OTEL_TRACES_EXPORTER="otlp",
            OTEL_EXPORTER_OTLP_PROTOCOL="http/protobuf",
            OTEL_EXPORTER_OTLP_ENDPOINT=os.environ.get(
                "LOGFIRE_BASE_URL", langfuse.query.logfire_base_url or "https://logfire-us.pydantic.dev"
            ).rstrip("/"),
            OTEL_EXPORTER_OTLP_HEADERS=f"Authorization={token}",
            OTEL_TRACES_SAMPLER="parentbased_always_on",
            OTEL_BSP_SCHEDULE_DELAY="500",
        )
    elif backend == "langfuse" and langfuse.enabled and tracing:
        assert langfuse.query.langfuse_public_key is not None and langfuse.query.langfuse_secret_key is not None
        token = base64.b64encode(
            f"{langfuse.query.langfuse_public_key.get_secret_value()}:{langfuse.query.langfuse_secret_key.get_secret_value()}".encode()
        ).decode()
        env.update(
            OTEL_TRACES_EXPORTER="otlp",
            OTEL_EXPORTER_OTLP_PROTOCOL="http/protobuf",
            OTEL_EXPORTER_OTLP_ENDPOINT=langfuse.base_url + "/api/public/otel",
            OTEL_EXPORTER_OTLP_HEADERS=f"Authorization=Basic%20{token},x-langfuse-ingestion-version=4",
            OTEL_TRACES_SAMPLER="always_on",
            OTEL_BSP_SCHEDULE_DELAY="500",
        )
    return env


@contextmanager
def local_traces(langfuse: Langfuse):
    """Apply only to the development executable and restore when its runtime closes."""
    keys = {key for key in os.environ if key.startswith("OTEL_")} | {"NO_PROXY", "no_proxy"}
    original = {key: os.environ[key] for key in keys if key in os.environ}
    resolved = trace_environment(langfuse)
    if any(key.startswith("OTEL_") for key in original):
        print("Local development replaces inherited OTEL_* settings with the selected dev trace profile.", flush=True)
    for key in original:
        del os.environ[key]
    os.environ.update({key: value for key, value in resolved.items() if key.startswith("OTEL_") or key in keys})
    try:
        yield
    finally:
        for key in list(os.environ):
            if key.startswith("OTEL_") or key in keys:
                del os.environ[key]
        os.environ.update(original)
