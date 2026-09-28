"""The disposable Service a journey runs against: Control and two Workers started through the installed CLI.

Stores are created once per session: a PostgreSQL template database migrated and bootstrapped through the CLI,
and one Redis. A Service stack clones the template and owns an object directory; ordinary journeys reuse that
stack with a fresh workspace and scripted model process, while process-fault journeys own a dedicated stack.
Clients talk to Control over HTTPS as the bootstrapped administrator. Workers serve health and readiness only.
"""

import base64
import ipaddress
import json
import os
import secrets
import signal
import socket
import ssl
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx2
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from docker.constants import DEFAULT_UNIX_SOCKET
from docker.context import ContextAPI
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[2]
CLI = Path(sys.executable).with_name("a13n-service")
EMAIL, PASSWORD = "e2e@example.com", "e2e-fixture-password"
# The shortest lease the Service accepts; every other interval is set well below it.
LEASE_SECONDS = 3
DRAIN_SECONDS = 8
STARTUP_SECONDS = 60


@dataclass(frozen=True)
class Stores:
    """Session-wide infrastructure: the PostgreSQL server, the migrated template, Redis and the TLS identity."""

    postgres_url: str
    template: str
    redis_url: str
    certificate: Path
    key: Path
    encryption_key: str
    tenant: dict[str, str]


def unavailable(config: pytest.Config, reason: str) -> None:
    """Skip a journey whose dependency is missing on this host, unless the run requires every journey (CI)."""
    if config.getoption("--require-all"):
        pytest.fail(reason)
    pytest.skip(reason)


def issue_certificate(directory: Path) -> tuple[Path, Path]:
    """A self-signed certificate for 127.0.0.1; clients trust exactly this certificate."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), False)
        .sign(key, hashes.SHA256())
    )
    certificate_path, key_path = directory / "certificate.pem", directory / "key.pem"
    certificate_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    key_path.chmod(0o600)
    return certificate_path, key_path


def database_url(server_url: str, name: str) -> str:
    return make_url(server_url).set(database=name).render_as_string(hide_password=False)


def admin_engine(server_url: str) -> Engine:
    return create_engine(database_url(server_url, "postgres"), isolation_level="AUTOCOMMIT")


def docker_host() -> str:
    """The Engine the Docker CLI would use: `DOCKER_HOST`, else the current context, else the default socket."""
    return os.environ.get("DOCKER_HOST") or ContextAPI.get_current_context().Host or DEFAULT_UNIX_SOCKET


def write_config(path: Path, stores: Stores, database: str, objects: Path, *, worker_slots: int = 4) -> Path:
    """One configuration for every process of a stack; each process sets its own port through the environment."""
    path.write_text(
        f"""[server]
tls_certificate = {json.dumps(str(stores.certificate))}
tls_key = {json.dumps(str(stores.key))}
shutdown_timeout = {DRAIN_SECONDS + 5}
[database]
url = {json.dumps(database_url(stores.postgres_url, database))}
auto_migrate = false
[redis]
url = {json.dumps(stores.redis_url)}
[objects]
root = {json.dumps(str(objects))}
# Settings require three object timeouts to fit in one lease.
timeout = 0.9
[encryption]
active_key_id = "e2e"
[encryption.keys]
e2e = {json.dumps(stores.encryption_key)}
[providers]
private_cidrs = ["127.0.0.0/8"]
require_https = false
[worker]
slots = {worker_slots}
lease_seconds = {LEASE_SECONDS}
scan_seconds = 0.2
authority_seconds = 0.2
drain_seconds = {DRAIN_SECONDS}
[control]
scan_seconds = 0.2
stream_refresh_seconds = 0.2
[environments]
scan_seconds = 0.5
# The operator's engine, which Docker accounts that name none use.
docker_host = {json.dumps(docker_host())}
[provisioning.local]
enabled = true
root = {json.dumps(str(objects.parent / "environments"))}
[telemetry]
log_format = "pretty"
"""
    )
    path.chmod(0o600)
    return path


def service_environment(**overrides: str) -> dict[str, str]:
    """The caller's environment without inherited Service settings, plus explicit overrides."""
    inherited = {name: value for name, value in os.environ.items() if not name.startswith("A13N_")}
    return {**inherited, **overrides}


def run_cli(config: Path, *arguments: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(CLI), "--config", str(config), *arguments],
        input=stdin,
        capture_output=True,
        text=True,
        env=service_environment(),
        timeout=STARTUP_SECONDS,
        check=False,
    )


def prepare_template(stores_directory: Path, postgres_url: str, redis_url: str) -> Stores:
    """Migrate and bootstrap the template database through the installed CLI, as an operator would."""
    certificate, key = issue_certificate(stores_directory)
    template = "e2e_template"
    with admin_engine(postgres_url).connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{template}"'))
    stores = Stores(
        postgres_url=postgres_url,
        template=template,
        redis_url=redis_url,
        certificate=certificate,
        key=key,
        encryption_key=base64.b64encode(secrets.token_bytes(32)).decode(),
        tenant={},
    )
    config = write_config(stores_directory / "template.toml", stores, template, stores_directory / "objects")
    migrated = run_cli(config, "migrate")
    assert migrated.returncode == 0, migrated.stderr
    bootstrapped = run_cli(config, "bootstrap", "--email", EMAIL, "--password-stdin", stdin=f"{PASSWORD}\n")
    assert bootstrapped.returncode == 0, bootstrapped.stderr
    return replace(stores, tenant=json.loads(bootstrapped.stdout))


@contextmanager
def cloned_database(stores: Stores) -> Iterator[str]:
    """A fresh database from the migrated, bootstrapped template, dropped afterwards."""
    name = f"e2e_{uuid4().hex}"
    admin = admin_engine(stores.postgres_url)
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}" TEMPLATE "{stores.template}"'))
    try:
        yield name
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        admin.dispose()


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


class ServiceProcess:
    """One `a13n-service run` process; tests signal it like an operator or a crashing host would."""

    def __init__(self, role: str, config: Path, log: Path):
        self.role, self.config, self.log = role, config, log
        self.port = free_port()
        self.url = f"https://127.0.0.1:{self.port}"
        self.process: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        with self.log.open("ab") as output:
            self.process = subprocess.Popen(
                [str(CLI), "--config", str(self.config), "run", "--role", self.role],
                env=service_environment(A13N_SERVER__PORT=str(self.port), A13N_SERVER__PUBLIC_URL=self.url),
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                cwd=ROOT,
                start_new_session=True,
            )

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def listening(self) -> bool:
        """Whether the process still accepts connections; it stops as soon as shutdown begins."""
        with socket.socket() as probe:
            return probe.connect_ex(("127.0.0.1", self.port)) == 0

    def signal(self, number: signal.Signals) -> None:
        assert self.process is not None
        self.process.send_signal(number)

    def wait(self, timeout: float) -> int:
        assert self.process is not None
        return self.process.wait(timeout=timeout)

    def kill(self) -> None:
        """End the process at once: a stack is disposable, and its journey already observed what it needed."""
        if self.running:
            assert self.process is not None
            self.process.kill()
            self.process.wait()

    def tail(self, lines: int = 40) -> str:
        return "\n".join(self.log.read_text(errors="replace").splitlines()[-lines:]) if self.log.exists() else ""


def trust(stores: Stores) -> ssl.SSLContext:
    return ssl.create_default_context(cafile=str(stores.certificate))


def wait_ready(processes: list[ServiceProcess], context: ssl.SSLContext) -> None:
    with httpx2.Client(verify=context, trust_env=False, timeout=2) as client:
        for process in processes:
            deadline = time.monotonic() + STARTUP_SECONDS
            while True:
                if not process.running:
                    raise RuntimeError(f"{process.role} exited during startup:\n{process.tail()}")
                try:
                    if client.get(f"{process.url}/readyz").status_code == 200:
                        break
                except httpx2.HTTPError:
                    pass
                if time.monotonic() > deadline:
                    raise RuntimeError(f"{process.role} did not become ready:\n{process.tail()}")
                time.sleep(0.1)


def read_rows(engine: Engine, statement: str, **parameters: Any) -> list[dict[str, Any]]:
    """Rows for invariants the public API does not show; journeys never write the database."""
    with engine.connect() as connection:
        return [dict(row) for row in connection.execute(text(statement), parameters).mappings()]
