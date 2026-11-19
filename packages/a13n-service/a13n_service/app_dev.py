"""Foreground source development against an ordinary, loopback-only Service."""

import os
import signal
import socket
import subprocess
import sys
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager, suppress
from pathlib import Path
from time import monotonic, sleep
from urllib.parse import urlsplit

import click
import httpx2
from sqlalchemy.engine import make_url

from a13n_service.settings import Settings

ADMIN_EMAIL = "admin@example.com"
ADMIN_PASSWORD = "local-public-password-123"


def _loopback(name: str | None) -> bool:
    return name in {"localhost", "127.0.0.1", "::1"} or bool(name and name.endswith(".localhost"))


def require_local(settings: Settings) -> None:
    server = settings.server
    public = urlsplit(server.public_url)
    if (
        server.host not in {"127.0.0.1", "::1"}
        or public.scheme != "http"
        or not _loopback(public.hostname)
        or server.tls_certificate is not None
    ):
        raise click.ClickException("App development requires a loopback HTTP listener and Console URL.")
    database = make_url(settings.database.url.get_secret_value())
    if (
        database.drivername != "postgresql+psycopg"
        or database.host not in {"127.0.0.1", "::1"}
        or database.query
        or any(name in os.environ for name in ("PGHOSTADDR", "PGSERVICE"))
    ):
        raise click.ClickException("Use a local postgresql+psycopg URL with a loopback IP and no connection overrides.")
    endpoints = [settings.redis.url.get_secret_value()]
    if settings.objects.backend == "s3":
        endpoints.append(settings.objects.endpoint_url or "")
    if not all(_loopback(urlsplit(endpoint).hostname) for endpoint in endpoints):
        raise click.ClickException("App development requires local PostgreSQL, Redis and object storage.")


@contextmanager
def _process(command: list[str], timeout: int = 5) -> Iterator[subprocess.Popen[bytes]]:
    # The terminal signals the supervisor. Signal the whole child group, including pnpm's watch process.
    with subprocess.Popen(command, start_new_session=True) as child:
        try:
            yield child
        finally:
            with suppress(ProcessLookupError):
                os.killpg(child.pid, signal.SIGTERM)
            try:
                child.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                with suppress(ProcessLookupError):
                    os.killpg(child.pid, signal.SIGKILL)


def _snapshot(root: Path | None) -> dict[Path, int]:
    if root is None:
        return {}
    sources = {}
    for path in root.rglob("*.py"):
        try:
            sources[path] = path.stat().st_mtime_ns
        except FileNotFoundError:  # Editors may replace a file between the walk and stat.
            continue
    return sources


def _interrupt(*_: object) -> None:
    raise KeyboardInterrupt


def develop(
    settings: Settings,
    config: Path,
    app: str,
    source: Path | None,
    frontend: Path | None,
    assets: Path | None,
) -> None:
    require_local(settings)
    server = settings.server
    try:
        with socket.create_server(
            (server.host, server.port), family=socket.AF_INET6 if ":" in server.host else socket.AF_INET
        ):
            pass
    except OSError as error:
        raise click.ClickException(
            f"Cannot listen on {server.host}:{server.port}: {error.strerror}. Choose another port."
        ) from None
    previous_handler = signal.signal(signal.SIGTERM, _interrupt)
    try:
        _develop(settings, config, app, source, frontend, assets)
    finally:
        signal.signal(signal.SIGTERM, previous_handler)


def _develop(
    settings: Settings, config: Path, app: str, source: Path | None, frontend: Path | None, assets: Path | None
) -> None:
    cli = [str(Path(sys.executable).with_name("a13n-service")), "--config", str(config)]
    if subprocess.run([*cli, "migrate"]).returncode:
        raise click.ClickException("Could not migrate the development database.")
    bootstrap = subprocess.run(
        [*cli, "bootstrap", "--email", ADMIN_EMAIL, "--password-stdin"], input=ADMIN_PASSWORD + "\n", text=True
    )
    if bootstrap.returncode not in {0, 3}:
        raise click.ClickException("Could not initialize the development organization.")

    serve = [*cli, "run", "--role", "all", "--dev-app", app]
    if assets is not None:
        serve.extend(["--dev-assets", str(assets)])
    with ExitStack() as stack:
        watcher = stack.enter_context(_process(["pnpm", "--dir", str(frontend), "dev"])) if frontend else None
        client = stack.enter_context(httpx2.Client(timeout=1, trust_env=False))
        server = settings.server
        address = f"[{server.host}]" if server.host == "::1" else server.host
        ready_url = f"http://{address}:{server.port}/readyz"
        console = server.public_url.rstrip("/")
        previous = _snapshot(source)
        announced = False
        while True:
            with _process(serve, timeout=server.shutdown_timeout + 5) as service:
                deadline = monotonic() + 60
                ready = False
                while True:
                    if watcher is not None and watcher.poll() is not None:
                        raise click.ClickException("The frontend watcher exited; see its output above.")
                    current = _snapshot(source)
                    if current != previous:
                        previous = current
                        click.echo("Python source changed; restarting Service.")
                        break
                    if service.poll() is not None:
                        if source is None or not announced or ready:
                            raise click.ClickException("Service exited; see its output above.")
                        # An edit can temporarily break imports. Keep watching for the next save.
                        sleep(0.5)
                        continue
                    if not ready:
                        try:
                            ready = client.get(ready_url).status_code == 200
                        except httpx2.RequestError:
                            pass
                        if ready and not announced:
                            announced = True
                            click.echo(f"Console: {console}\nApp plugins: {console}/organization/settings/app-plugins")
                            click.echo(f"Local sign-in: {ADMIN_EMAIL} / {ADMIN_PASSWORD}. Enable {app} in App plugins.")
                        elif not ready and monotonic() >= deadline:
                            raise click.ClickException("Service did not become ready; see its output above.")
                    sleep(0.5)
