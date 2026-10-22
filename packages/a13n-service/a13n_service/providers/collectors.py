"""Small protected-file adapters for operator-owned vmagent and Alloy installations."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from collections.abc import AsyncGenerator, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit

from a13n_harness.providers.endpoint_policy import EndpointPolicy
from anyio.to_thread import run_sync
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StringConstraints, model_validator

from a13n_service.infra.outbound import open_http
from a13n_service.providers.environment.metrics import ScrapeTarget

type Kind = Literal["vmagent", "alloy"]
Receiver = Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)+$")]


class Configuration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    # Configure one control writer; overlapping replacements share this directory
    # with the collector and each other, including POSIX advisory locks.
    directory: Path
    endpoint: str = Field(min_length=1, max_length=2048)
    headers: dict[str, SecretStr] = Field(default_factory=dict, repr=False)
    writer: bool = False
    # An explicit deployment exception for this reload origin only, not scrape/query destinations.
    allow_http_reload: bool = False
    # An existing Alloy receiver expression; Service never edits its destination.
    receiver: Receiver | None = None

    @model_validator(mode="after")
    def valid(self) -> Self:
        url = urlsplit(self.endpoint)
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise ValueError("Collector endpoint must be an HTTP(S) origin or gateway prefix")
        if not self.directory.is_absolute():
            raise ValueError("Collector directory must be absolute")
        return self


@dataclass(frozen=True, slots=True)
class Job:
    environment_id: str
    workspace_id: str
    organization_id: str
    target: ScrapeTarget = field(repr=False)

    @property
    def labels(self) -> dict[str, str]:
        return {
            "environment_id": self.environment_id,
            "workspace_id": self.workspace_id,
            "organization_id": self.organization_id,
        }


def render(kind: Kind, jobs: Sequence[Job], *, receiver: str | None, interval: float, timeout: float) -> str:
    """One independent scrape job per credential. Tokens never enter discovery labels."""
    configs = []
    components = []
    for job in sorted(jobs, key=lambda job: job.environment_id):
        url = urlsplit(job.target.endpoint)
        name = "a13n_service_" + job.environment_id
        path = url.path.rstrip("/") + "/metrics"
        if kind == "vmagent":
            configs.append(
                {
                    "job_name": name,
                    "scheme": url.scheme,
                    "metrics_path": path,
                    "scrape_interval": f"{interval:g}s",
                    "scrape_timeout": f"{timeout:g}s",
                    "follow_redirects": False,
                    "honor_labels": False,
                    "authorization": {"type": "Bearer", "credentials": job.target.token.get_secret_value()},
                    "headers": [f"{key}: {value.get_secret_value()}" for key, value in job.target.headers.items()],
                    "tls_config": {"insecure_skip_verify": not job.target.verify_tls},
                    "static_configs": [{"targets": [url.netloc], "labels": job.labels}],
                }
            )
        else:
            if receiver is None:
                raise ValueError("Alloy requires an existing receiver")
            labels = {"__address__": url.netloc, **job.labels}
            target = ", ".join(f"{json.dumps(key)} = {json.dumps(value)}" for key, value in labels.items())
            extra_headers = ", ".join(
                f"{json.dumps(key)} = [{json.dumps(value.get_secret_value())}]"
                for key, value in job.target.headers.items()
            )
            components.append(
                "\n".join(
                    [
                        f"prometheus.scrape {json.dumps(name)} {{",
                        f"  targets = [{{{target}}}]",
                        f"  forward_to = [{receiver}]",
                        f"  scheme = {json.dumps(url.scheme)}",
                        f"  metrics_path = {json.dumps(path)}",
                        f'  scrape_interval = "{interval:g}s"',
                        f'  scrape_timeout = "{timeout:g}s"',
                        "  follow_redirects = false",
                        "  honor_labels = false",
                        f"  bearer_token = {json.dumps(job.target.token.get_secret_value())}",
                        f"  http_headers = {{{extra_headers}}}",
                        "  tls_config {",
                        f"    insecure_skip_verify = {str(not job.target.verify_tls).lower()}",
                        "  }",
                        "}",
                    ]
                )
            )
    # JSON is a YAML subset accepted by vmagent's scrape_config_files. Its root
    # is a list, unlike a full Prometheus configuration's scrape_configs object.
    return json.dumps(configs, indent=2) + "\n" if kind == "vmagent" else "\n\n".join(components) + "\n"


def fragment_path(config: Configuration, kind: Kind) -> Path:
    return config.directory / ("a13n-managed.yml" if kind == "vmagent" else "a13n-managed.alloy")


async def _file_io[T](function: Callable[..., T], *args: object) -> T:
    # Service shutdown uses asyncio cancellation. Joining the shielded thread task
    # also covers that case; AnyIO shielding alone only covers cancel scopes.
    task = asyncio.create_task(run_sync(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


@dataclass
class _Lock:
    path: Path
    fd: int = -1

    def acquire(self) -> bool:
        import fcntl

        self.fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1


@asynccontextmanager
async def writer_lock(config: Configuration, kind: Kind) -> AsyncGenerator[bool]:
    """A nonblocking POSIX lock covers discovery, replacement and reload.

    Never unlink the lock inode. Cancellation waits for file work to finish before
    releasing ownership, including cancellation during acquisition itself.
    """
    lock = _Lock(config.directory / f".a13n-{kind}.lock")
    try:
        yield await _file_io(lock.acquire)
    finally:
        await _file_io(lock.close)


def _replace(path: Path, content: str) -> None:
    # mkstemp creates mode 0600 before writing any listener credentials. Mount
    # the directory, not this file: replacement must be visible to the collector.
    # The caller holds this collector's writer lock. Recover only its own staging
    # files after a process crash; another collector may share the directory.
    prefix = f".{path.name}.write-"
    for abandoned in path.parent.glob(f"{prefix}*"):
        abandoned.unlink()
    fd, name = tempfile.mkstemp(prefix=prefix, dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


async def apply(policy: EndpointPolicy, config: Configuration, kind: Kind, content: str, *, timeout: float) -> None:
    await _file_io(_replace, fragment_path(config, kind), content)
    endpoint = urlsplit(config.endpoint)
    if config.allow_http_reload and endpoint.scheme == "http":
        exception = EndpointPolicy.from_http_origins(http_origins=[f"http://{endpoint.netloc}"])
        policy = replace(policy, allowed_http_origins=policy.allowed_http_origins | exception.allowed_http_origins)
    async with open_http(policy, timeout=timeout, max_bytes=65536) as client:
        response = await client.post(
            config.endpoint.rstrip("/") + "/-/reload",
            headers={key: secret.get_secret_value() for key, secret in config.headers.items()},
        )
        response.raise_for_status()
