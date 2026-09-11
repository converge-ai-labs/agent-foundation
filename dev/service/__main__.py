"""Operate local Service independently of any browser application."""

import argparse
import os
import socket
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

from a13n_service.configuration.sources import load_settings
from a13n_service.database import DatabaseMigrator
from a13n_service.log import configure_logging

from .docker import ensure_docker
from .environment import LOCAL_CONFIG, ROOT, Environment
from .langfuse import USER_EMAIL, USER_PASSWORD, Langfuse, local_traces
from .reset import reset


def check_ports(environment: Environment, *, console: bool = False) -> None:
    settings = environment.settings
    ports = [(settings.service.host, settings.service.port, "Service"), ("127.0.0.1", 18080, "scripted model")]
    if settings.service.host != "127.0.0.1":
        raise ValueError("Local development requires service.host = '127.0.0.1'")
    if console:
        origin = urlsplit(settings.iam.public_origin)
        if origin.scheme != "http" or origin.hostname != "127.0.0.1" or not origin.port or origin.path:
            raise ValueError("Local Console requires iam.public_origin = 'http://127.0.0.1:PORT'")
        ports.append(("127.0.0.1", origin.port, "Console"))
    if len({port for _, port, _ in ports}) != len(ports):
        raise ValueError("Service, scripted model and Console require distinct ports")
    for host, port, name in ports:
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                listener.bind((host, port))
            except OSError:
                raise ValueError(
                    f"{name} port {host}:{port} is in use; stop its owner before starting development"
                ) from None


def setup(environment: Environment, langfuse: Langfuse, config: Path) -> None:
    with environment.lock():
        if environment.incomplete.exists():
            raise ValueError("The previous reset did not complete; rerun make dev-reset with the intended STATE")
        langfuse.validate()
        ensure_docker()
        environment.compose("up", "-d", "--wait")
        langfuse.start()
        DatabaseMigrator(environment.settings.database_config(), environment.settings.migration_config()).upgrade()
    print(f"Configuration: {config.resolve()}")
    print(f"Service: http://{environment.settings.service.host}:{environment.settings.service.port}")
    print(f"Console: {environment.settings.iam.public_origin}")
    if langfuse.enabled:
        print(f"Langfuse: {langfuse.base_url} (media: http://127.0.0.1:{langfuse.port + 1})")
        print(f"Public local Langfuse account: {USER_EMAIL} / {USER_PASSWORD}")
        print("Inspect traces in Langfuse or query authorized Run traces through Console.")
    print("Infrastructure and schema ready; Service and Console have not been started.")
    print("Run make dev (Service + Console) or make service-dev (Service only).")
    if not (environment.state / "seed.json").exists():
        print("For fictional accounts/content, explicitly run make dev-reset STATE=seeded; this deletes Service data.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=LOCAL_CONFIG)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("setup", help="Prepare owned stores, Langfuse and schema, preserving data")
    commands.add_parser("stop", help="Stop owned infrastructure including Langfuse, preserving data")
    commands.add_parser("serve", help="Run Service with the local deterministic model")
    commands.add_parser("console", help="Run Console against the selected local Service")
    commands.add_parser("model", help="Run only the local deterministic model")
    command = commands.add_parser("check-ports", help="Check application ports without starting listeners")
    command.add_argument("--console", action="store_true")
    command = commands.add_parser("reset", help="Rebuild Service stores only; application processes must be stopped")
    command.add_argument("state", choices=("empty", "seeded"))
    command = commands.add_parser("langfuse", help="Operate the selected checkout's local Langfuse")
    command.add_argument("action", choices=("up", "down", "reset", "test"))
    args = parser.parse_args()
    try:
        settings = load_settings(args.config)
        environment = Environment(settings)
        environment.validate()
        langfuse = Langfuse(environment)
        langfuse.validate()
        if args.command == "reset":
            if args.state == "seeded":
                langfuse.start()
            with local_traces(langfuse):
                reset(environment, args.state)
        elif args.command == "setup":
            setup(environment, langfuse, args.config)
        elif args.command == "stop":
            with environment.lock():
                environment.require_stopped()
                environment.compose("stop")
                langfuse.compose("stop")
        elif args.command == "langfuse":
            if not langfuse.enabled and args.action in {"up", "test"}:
                raise ValueError("Select observability.query.provider = 'langfuse' in SERVICE_CONFIG first")
            if args.action == "up":
                langfuse.start()
                print(f"Langfuse: {langfuse.base_url}\nPublic local account: {USER_EMAIL} / {USER_PASSWORD}")
            elif args.action == "test":
                langfuse.test()
            else:
                langfuse.compose("down", *(["--volumes"] if args.action == "reset" else []), "--remove-orphans")
        elif args.command == "check-ports":
            check_ports(environment, console=args.console)
        elif args.command == "console":
            origin = urlsplit(settings.iam.public_origin)
            # The outer launcher already checked both listeners before starting Service.
            assert origin.port is not None
            os.execvpe(
                "pnpm",
                [
                    "pnpm",
                    "--dir",
                    str(ROOT / "frontend"),
                    "--filter",
                    "a13n-console",
                    "dev",
                    "--port",
                    str(origin.port),
                ],
                {**os.environ, "A13N_CONSOLE_SERVICE_URL": f"http://{settings.service.host}:{settings.service.port}"},
            )
        elif args.command == "model":
            from .model import serve_model

            serve_model()
        else:
            from a13n_service.app import create_app
            from a13n_service.process.server import serve_app

            from .model import model_process

            check_ports(environment)
            with environment.lock(shared=True), local_traces(langfuse):
                if environment.incomplete.exists():
                    raise ValueError("The previous reset did not complete; run reset again before starting Service")
                DatabaseMigrator(settings.database_config(), settings.migration_config()).upgrade()
                configure_logging(settings)
                with model_process():
                    serve_app(create_app(settings))
    except (ValueError, RuntimeError, subprocess.CalledProcessError, OSError) as error:
        # Do not print command output or settings; they may contain injected secrets.
        print(
            str(error)
            if isinstance(error, (ValueError, RuntimeError))
            else "Local command failed; check Docker and required tools",
            file=sys.stderr,
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
