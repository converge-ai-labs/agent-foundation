"""Ownership and lifecycle of the repository's local Service infrastructure."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from a13n_service.settings import Settings
from sqlalchemy.engine import make_url

from .instance import Instance, Ports

ROOT = Path(__file__).resolve().parents[2]
LOCAL_CONFIG = Path(__file__).with_name("local.toml")


@dataclass(frozen=True)
class Environment:
    settings: Settings
    instance: Instance
    root: Path = ROOT

    @property
    def state(self) -> Path:
        return self.root / "var/dev/service"

    @property
    def incomplete(self) -> Path:
        return self.root / "var/dev/reset-incomplete"

    @property
    def project(self) -> str:
        return f"a13n-dev-v2-{self.local_id}"

    @property
    def legacy_project(self) -> str:
        return f"a13n-local-{self.local_id}"

    @property
    def local_id(self) -> str:
        return self.instance.id

    @property
    def ports(self) -> Ports:
        return self.instance.ports

    def validate(self) -> None:
        """Never infer ownership just from localhost or a filename."""
        config = self.settings
        database = config.database
        redis = config.redis
        if redis.backend != "redis" or redis.url is None:
            raise ValueError("Local tools require their dedicated PostgreSQL and Redis services")
        sql = make_url(database.url.get_secret_value())
        cache = urlsplit(redis.url.get_secret_value())
        if (
            sql.drivername != "postgresql+psycopg"
            or sql.host != "127.0.0.1"
            or sql.database != "a13n_service_dev"
            or sql.username != "a13n_service_dev"
            or sql.password != "local-only-password"
            or sql.query
            or not sql.port
            or cache.scheme != "redis"
            or cache.hostname != "127.0.0.1"
            or cache.path != "/0"
            or cache.username
            or cache.password
            or cache.query
            or cache.fragment
            or not cache.port
        ):
            raise ValueError("Effective storage configuration does not match the owned local development services")
        if sql.port == cache.port:
            raise ValueError("PostgreSQL and Redis ports must differ")
        ports = self.ports
        if (
            config.service.host != "127.0.0.1"
            or config.service.port != ports.service
            or sql.port != ports.postgres
            or cache.port != ports.redis
            or urlsplit(config.iam.public_origin).port != ports.console
            or config.iam.session_cookie_name != f"a13n_session_{self.local_id}"
        ):
            raise ValueError("Effective settings do not match this checkout's local instance")
        if config.objects.backend != "local":
            raise ValueError("Local reset only owns the local object backend, never an external bucket")
        for value, expected in (
            (config.objects.local_root, self.state / "objects"),
            (config.filesystem.root, self.state / "files"),
        ):
            if value.absolute() != expected.absolute() or value.resolve() != expected.absolute():
                raise ValueError("Local storage must use the owned var/dev/service directories without symlinks")
        if self.state.resolve() != self.state.absolute():
            raise ValueError("The local state directory must not traverse symlinks")
        if self.incomplete.is_symlink():
            raise ValueError("The reset marker must not be a symlink")

    def compose(self, *args: str, capture: bool = False) -> str:
        self.validate()
        assert self.settings.database.url is not None and self.settings.redis.url is not None
        sql = make_url(self.settings.database.url.get_secret_value())
        cache = urlsplit(self.settings.redis.url.get_secret_value())
        env = {**os.environ, "A13N_DEV_POSTGRES_PORT": str(sql.port), "A13N_DEV_REDIS_PORT": str(cache.port)}
        result = subprocess.run(
            [
                "docker",
                "compose",
                "--env-file",
                os.devnull,
                "--project-name",
                self.project,
                "--file",
                str(ROOT / "dev/service/compose.yaml"),
                *args,
            ],
            env=env,
            check=True,
            text=True,
            stdout=subprocess.PIPE if capture else None,
        )
        return result.stdout or ""

    def legacy_resources(self) -> tuple[str, ...]:
        """Find the retired v1 namespace without operating it."""
        commands = (
            (
                "docker",
                "ps",
                "-a",
                "--filter",
                f"label=com.docker.compose.project={self.legacy_project}",
                "--format",
                "container {{.Names}}",
            ),
            (
                "docker",
                "volume",
                "ls",
                "--filter",
                f"label=com.docker.compose.project={self.legacy_project}",
                "--format",
                "volume {{.Name}}",
            ),
        )
        return tuple(
            line
            for command in commands
            for line in subprocess.run(command, check=True, capture_output=True, text=True).stdout.splitlines()
            if line
        )

    def report_legacy_resources(self) -> None:
        resources = self.legacy_resources()
        if resources:
            print(
                f"Retained legacy local resources ({self.legacy_project}) were detected and will not be used, "
                "stopped, reset, or migrated:\n  " + "\n  ".join(resources),
                flush=True,
            )

    def require_stopped(self) -> None:
        """Also catch Service processes started outside the development launcher."""
        running = set(self.compose("ps", "--services", "--status", "running", capture=True).split())
        if "postgres" in running:
            clients = self.compose(
                "exec",
                "-T",
                "postgres",
                "psql",
                "-U",
                "a13n_service_dev",
                "-d",
                "a13n_service_dev",
                "-Atc",
                "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() AND pid <> pg_backend_pid()",
                capture=True,
            )
            if int(clients.strip()):
                raise ValueError("The local database has active connections; stop Service and database clients first")
        if "redis" in running:
            clients = self.compose("exec", "-T", "redis", "redis-cli", "CLIENT", "LIST", capture=True)
            if any("cmd=client|list" not in line and "cmd=ping" not in line for line in clients.splitlines()):
                raise ValueError("Local Redis has active clients; stop Service before resetting")
