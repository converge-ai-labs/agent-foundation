"""Operate local Service independently of any browser application."""

import argparse
import subprocess
import sys
from pathlib import Path

from a13n_service.configuration.sources import load_settings
from a13n_service.database import DatabaseMigrator
from a13n_service.log import configure_logging

from .environment import LOCAL_CONFIG, Environment
from .reset import reset


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=LOCAL_CONFIG)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("setup", help="Start owned PostgreSQL and Redis, preserving data")
    commands.add_parser("stop", help="Stop owned infrastructure, preserving data")
    commands.add_parser("serve", help="Run Service with the local deterministic model")
    commands.add_parser("model", help="Run only the local deterministic model")
    command = commands.add_parser("reset", help="Rebuild all owned stores; application processes must be stopped")
    command.add_argument("state", choices=("empty", "seeded"))
    args = parser.parse_args()
    try:
        settings = load_settings(args.config)
        environment = Environment(settings)
        environment.validate()
        if args.command == "reset":
            reset(environment, args.state)
        elif args.command in {"setup", "stop"}:
            with environment.lock():
                if args.command == "stop":
                    environment.require_stopped()
                environment.compose(*(["up", "-d", "--wait"] if args.command == "setup" else ["stop"]))
        elif args.command == "model":
            from .model import serve_model

            serve_model()
        else:
            from a13n_service.app import create_app
            from a13n_service.process.server import serve_app

            from .model import model_process

            with environment.lock(shared=True):
                if environment.incomplete.exists():
                    raise ValueError("The previous reset did not complete; run reset again before starting Service")
                DatabaseMigrator(settings.database_config(), settings.migration_config()).upgrade()
                configure_logging(settings)
                with model_process():
                    serve_app(create_app(settings))
    except (ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        # Do not print command output or settings; they may contain injected secrets.
        print(
            str(error) if isinstance(error, (ValueError, RuntimeError)) else "Local infrastructure command failed",
            file=sys.stderr,
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
