"""The applications a checkout runs (scripted model, Service, Console) and the Service CLI operations it uses."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from dev.service.checkout import ADMIN_EMAIL, ADMIN_PASSWORD, Checkout
from dev.service.lifecycle import Application


def service_command(checkout: Checkout, *arguments: str) -> tuple[str, ...]:
    executable = Path(sys.executable).parent / "a13n-service"
    return (str(executable), "--config", str(checkout.settings_file), *arguments)


def service_environment(checkout: Checkout) -> dict[str, str]:
    # The settings file describes the whole instance: inherited deployment settings and collectors must not
    # redirect it (the Service also rejects unknown A13N_ variables).
    inherited = {name: value for name, value in os.environ.items() if not name.startswith(("A13N_", "OTEL_"))}
    loopback = ",".join(filter(None, (os.environ.get("NO_PROXY"), "127.0.0.1,localhost")))
    return {
        **inherited,
        "NO_PROXY": loopback,
        "no_proxy": loopback,
        "OTEL_RESOURCE_ATTRIBUTES": f"deployment.environment.name={checkout.trace_environment}",
    }


def applications(checkout: Checkout, *, console: bool) -> tuple[Application, ...]:
    ports = checkout.instance.ports
    serve_model = f"from dev.fixtures.model import serve_model; serve_model({ports.model})"
    selected = [
        Application("model", (sys.executable, "-c", serve_model), ports.model, dict(os.environ)),
        Application(
            "service", service_command(checkout, "run", "--role", "all"), ports.service, service_environment(checkout)
        ),
    ]
    if console:
        command = ("pnpm", "--dir", str(checkout.root / "frontend"), "--filter", "a13n-console", "dev")
        environment = {**os.environ, "A13N_CONSOLE_SERVICE_URL": checkout.service_url}
        selected.append(Application("console", (*command, "--port", str(ports.console)), ports.console, environment))
    return tuple(selected)


def migrate(checkout: Checkout) -> None:
    command = service_command(checkout, "migrate")
    if subprocess.run(command, cwd=checkout.root, env=service_environment(checkout)).returncode:
        raise RuntimeError(
            "Migration failed (output above). A database whose migration history does not match this checkout "
            "needs make dev-reset STATE=empty or STATE=seeded."
        )


def create_administrator(checkout: Checkout) -> bool:
    """Bootstrap the local administrator; False when the database already has one."""
    command = service_command(checkout, "bootstrap", "--email", ADMIN_EMAIL, "--password-stdin")
    result = subprocess.run(
        command,
        cwd=checkout.root,
        env=service_environment(checkout),
        input=f"{ADMIN_PASSWORD}\n",
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return True
    if result.returncode == 3:  # already initialized
        return False
    raise RuntimeError(f"Creating the local administrator failed:\n{result.stderr.strip()}")
