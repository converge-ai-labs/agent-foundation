"""Disposable real Service with only the fixed Composio peer origins substituted."""

import argparse

from a13n_harness.providers.connector.composio import catalog, events, runtime
from a13n_service.cli import main


def run():
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--role", choices=("control", "worker"), default="worker")
    args = parser.parse_args()
    for module, name in (
        (catalog, "COMPOSIO_ENDPOINT"),
        (events, "COMPOSIO_ENDPOINT"),
        (runtime, "COMPOSIO_ENDPOINT"),
        (runtime, "COMPOSIO_CONNECT_ENDPOINT"),
    ):
        setattr(module, name, args.endpoint)
    main(["--config", args.config, "run", "--role", args.role])


if __name__ == "__main__":
    run()
