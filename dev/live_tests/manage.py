"""Initialize local identities, run explicit service roles, and provision through HTTP."""

from __future__ import annotations

import argparse
import base64
import secrets

import anyio
import httpx2
from a13n_service.configuration.sources import load_settings
from a13n_service.ids import new_object_id
from a13n_service.log import configure_logging

from .infrastructure.bootstrap import bootstrap as bootstrap_lab
from .infrastructure.bootstrap import initialize as initialize_identities
from .infrastructure.client import LiveClient
from .infrastructure.config import CONFIG, STATE, load_config, save_config
from .infrastructure.core_resources import provision as provision_core


async def initialize(config=None) -> None:
    if config is None and CONFIG.exists():
        config = load_config()
    if config is None:
        config = {
            "control_url": "http://127.0.0.1:18000",
            "worker_url": "http://127.0.0.1:18001",
            "workspace_root": str((STATE / "workspace").resolve()),
            "organization_id": new_object_id("org"),
            "workspace_id": new_object_id("ws"),
            "user_id": new_object_id("usr"),
            "token": secrets.token_urlsafe(32),
            "encryption_key": base64.b64encode(secrets.token_bytes(32)).decode(),
            "timeout_seconds": 120,
        }
        save_config(config)
    await initialize_identities(load_settings(), [config])
    print(f"Initialized local Workspace {config['workspace_id']}; private configuration: {CONFIG}")


async def bootstrap() -> None:
    await bootstrap_lab(load_settings(), load_config())


async def provision() -> None:
    config = load_config()
    async with httpx2.AsyncClient(
        base_url=config["control_url"],
        headers={"Authorization": "Bearer " + config["token"]},
        timeout=120,
        trust_env=False,
        follow_redirects=False,
    ) as http:
        await provision_core(LiveClient(config, http), on_created=save_config)
    print(f"Provisioned local Agent {config['agent_id']} and approval Agent {config['approval_agent_id']}.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("init", "bootstrap", "setup", "control", "worker", "authenticated-control"))
    args = parser.parse_args()
    if args.command == "init":
        anyio.run(initialize)
    elif args.command == "bootstrap":
        anyio.run(bootstrap)
    elif args.command == "setup":
        anyio.run(provision)
    elif args.command == "authenticated-control":
        from a13n_service.process.server import serve_app

        from .infrastructure.host import authenticated_control

        settings, app = authenticated_control(load_config())
        configure_logging(settings)
        serve_app(app)
    else:
        from a13n_service.process.server import serve_app

        from .infrastructure.host import local_app, settings_for

        config = load_config()
        settings = settings_for(config, args.command)
        configure_logging(settings)
        serve_app(local_app(config, args.command))


if __name__ == "__main__":
    main()
