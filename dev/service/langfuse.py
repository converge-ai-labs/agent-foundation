"""Wire the owned local Langfuse stack without changing Service configuration."""

from __future__ import annotations

import base64
import os
import shlex
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx2

from .environment import ROOT, Environment

# These are public local-only identities, not deployment credentials.
USER_EMAIL = "dev@agent-foundation.local"
USER_PASSWORD = "agent-foundation-local"


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

    def validate(self) -> None:
        if self.query.provider == "none":
            return
        if not self.enabled:
            raise ValueError("Local development supports observability.query.provider = 'langfuse' or 'none'")
        url = urlsplit(self.query.langfuse_base_url or "")
        if (
            url.scheme != "http"
            or url.hostname != "127.0.0.1"
            or not url.port
            or url.port >= 65535
            or url.username is not None
            or url.password is not None
            or url.path not in {"", "/"}
            or url.query
            or url.fragment
        ):
            raise ValueError(
                "Local Langfuse requires http://127.0.0.1:PORT (PORT < 65535), without a path or credentials"
            )
        if self.query.langfuse_public_key is None or self.query.langfuse_secret_key is None:
            raise ValueError("Local Langfuse requires its public and secret project keys in observability.query")
        # The adjacent port exposes Langfuse media storage, not Service objects.
        settings = self.environment.settings
        assert settings.database.url is not None and settings.redis.url is not None
        occupied = {
            urlsplit(settings.database.url.get_secret_value()).port,
            urlsplit(settings.redis.url.get_secret_value()).port,
            settings.service.port,
            urlsplit(settings.iam.public_origin).port,
            18080,
        }
        if {self.port, self.port + 1} & occupied:
            raise ValueError("Langfuse HTTP/media ports overlap Service, Console, model, PostgreSQL or Redis ports")

    def compose(self, *args: str, capture: bool = False) -> str:
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
                self.environment.project + "-langfuse",
                "--file",
                str(ROOT / "dev/observability/langfuse.compose.yaml"),
                *args,
            ],
            env=env,
            check=True,
            text=True,
            stdout=subprocess.PIPE if capture else None,
        )
        return result.stdout or ""

    def warn_about_legacy_stack(self) -> None:
        legacy_project = "agent-foundation-langfuse-dev"
        running = subprocess.run(
            ["docker", "ps", "--filter", f"label=com.docker.compose.project={legacy_project}", "--format", "{{.ID}}"],
            check=True,
            capture_output=True,
            text=True,
        )
        if running.stdout.strip():
            command = shlex.join(
                [
                    "docker",
                    "compose",
                    "--env-file",
                    os.devnull,
                    "--project-name",
                    legacy_project,
                    "--file",
                    str(ROOT / "dev/observability/langfuse.compose.yaml"),
                    "stop",
                ]
            )
            print(
                "A legacy shared Langfuse stack is running. This checkout uses separate volumes; old traces are not migrated.\n"
                "If its ports conflict, stop it without deleting data, then rerun setup:\n" + command,
                flush=True,
            )

    def start(self) -> None:
        if not self.enabled:
            return
        self.warn_about_legacy_stack()
        self.compose("up", "-d", "--wait", "--wait-timeout", "180")
        self.check_credentials()

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
    backend = os.environ.get("A13N_DEV_TRACE_BACKEND", "langfuse")
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
            OTEL_EXPORTER_OTLP_ENDPOINT=os.environ.get("LOGFIRE_BASE_URL", "https://logfire-us.pydantic.dev").rstrip(
                "/"
            ),
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
