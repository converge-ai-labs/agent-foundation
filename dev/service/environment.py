"""Ownership and lifecycle of the repository's local Service infrastructure."""

from __future__ import annotations

import fcntl
import hashlib
import os
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from a13n_service.settings import Settings
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[2]
LOCAL_CONFIG = Path(__file__).with_name("local.toml")


@dataclass(frozen=True)
class Environment:
    settings: Settings
    root: Path = ROOT

    @property
    def state(self) -> Path:
        return self.root / "var" / "service"

    @property
    def incomplete(self) -> Path:
        return self.root / "var" / "service-reset-incomplete"

    @property
    def project(self) -> str:
        suffix = hashlib.sha256(str(self.root.resolve()).encode()).hexdigest()[:12]
        return f"a13n-local-{suffix}"

    def validate(self) -> None:
        """Never infer ownership just from localhost or a filename."""
        config = self.settings
        database = config.database
        redis = config.redis
        if database.backend != "postgresql" or database.url is None or redis.backend != "redis" or redis.url is None:
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
        if config.objects.backend != "local":
            raise ValueError("Local reset only owns the local object backend, never an external bucket")
        for value, expected in (
            (config.objects.local_root, self.state / "objects"),
            (config.filesystem.root, self.state / "files"),
        ):
            if value.absolute() != expected.absolute() or value.resolve() != expected.absolute():
                raise ValueError("Local storage must use the owned var/service directories without symlinks")
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

    @contextmanager
    def lock(self, *, shared: bool = False):
        self.validate()
        directory = self.root / "var"
        directory.mkdir(exist_ok=True)
        path = directory / "service.lock"
        # Do not follow a substituted lock file to another location.
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(fd, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError(
                    "Local Service or another state operation is running; stop it before resetting"
                ) from None
            yield
        finally:
            os.close(fd)

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
