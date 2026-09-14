"""Native IAM HTTP clients over disposable lab storage, without changing fixture authentication."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx2
from a13n_service.app import create_app
from a13n_service.configuration.sources import load_settings
from a13n_service.iam.auth.passwords import csrf_token, new_token, token_hash
from a13n_service.iam.models import AuthSessionRecord
from a13n_service.ids import new_object_id
from a13n_service.log import configure_logging
from a13n_service.process.server import serve_app
from a13n_service.storage import open_storage, transaction

from ..infrastructure.client import LiveClient
from ..infrastructure.config import load_config
from ..infrastructure.host import settings_for
from ..infrastructure.management_support import ManagementJourney
from ..infrastructure.round_two_lab import free_origin, private_json

ORIGIN = "http://127.0.0.1:8000"


async def seed_admin():
    """Bootstrap only the fixture administrator; test Users use real invitation acceptance."""
    config = load_config()
    token, now = new_token(), datetime.now(UTC)
    async with open_storage(load_settings().storage_settings()) as storage:
        async with transaction(storage.sessions) as session:
            session.add(
                AuthSessionRecord(
                    id=new_object_id("ses"),
                    user_id=config["user_id"],
                    token_hash=token_hash(token),
                    created_at=now,
                    expires_at=now + timedelta(hours=1),
                    revoked_at=None,
                )
            )
    private_json(
        Path(os.environ["LIVE_TEST_CONFIG"]).parent / "native-admin.json", {"cookie": token, "csrf": csrf_token(token)}
    )


@asynccontextmanager
async def native_clients(lab):
    origin = free_origin()
    process = await lab.spawn(
        "dev.live_tests.iam.native_iam",
        "serve",
        environment={**lab.environment, "LIVE_TEST_NATIVE_CONTROL_URL": origin},
    )
    await lab.ready(process, origin)
    await lab.command("dev.live_tests.iam.native_iam", "seed")
    admin = json.loads((lab.root / "native-admin.json").read_text())
    # Loopback HTTP carries the cookie explicitly. Secure-cookie browser transport is not under test.
    headers = {
        "Cookie": "a13n_session=" + admin["cookie"],
        "X-A13N-CSRF-Token": admin["csrf"],
        "Origin": ORIGIN,
        "X-A13N-Workspace-ID": lab.config["workspace_id"],
    }
    async with (
        httpx2.AsyncClient(base_url=origin, headers=headers, timeout=45, trust_env=False) as admin_http,
        httpx2.AsyncClient(base_url=origin, timeout=45, trust_env=False) as user,
    ):
        journey = ManagementJourney(lab)
        journey.live = LiveClient(lab.config, admin_http)
        yield journey, user


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("seed", "serve"))
    if parser.parse_args().command == "seed":
        asyncio.run(seed_admin())
    else:
        config = load_config()
        config["control_url"] = os.environ["LIVE_TEST_NATIVE_CONTROL_URL"]
        settings = settings_for(config, "control")
        settings = settings.model_copy(update={"iam": settings.iam.model_copy(update={"public_origin": ORIGIN})})
        if config.get("run_faults", {}).get("skills"):
            from ..infrastructure.run_faults import Faults
            from ..skills.fault_host import install

            install(Faults(Path(os.environ["LIVE_TEST_CONFIG"]).parent / "faults", "control"))
        configure_logging(settings)
        serve_app(create_app(settings))


if __name__ == "__main__":
    main()
