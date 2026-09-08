"""Initialize local identities, run explicit service roles, and provision through HTTP."""

from __future__ import annotations

import argparse
import base64
import secrets
import sys
from datetime import UTC, datetime
from pathlib import Path

import anyio
import httpx2
import uvicorn
from a13n_service.database import DatabaseMigrator
from a13n_service.iam.models import OrganizationRecord, RoleBindingRecord, UserRecord, WorkspaceRecord
from a13n_service.ids import new_object_id
from a13n_service.log import configure_logging
from a13n_service.settings import Settings
from a13n_service.storage import transaction
from a13n_service.storage.relational import create_session_factory, create_sql_engine

from .client import LiveClient
from .config import CONFIG, STATE, load_config, save_config
from .host import authenticated_control, local_app, settings_for
from .wheel import approval_wheel


async def initialize() -> None:
    if CONFIG.exists():
        config = load_config()
    else:
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
    Path(config["workspace_root"]).mkdir(parents=True, exist_ok=True, mode=0o700)
    settings = Settings()
    # Apply only committed migrations using the repository's make db-upgrade first.
    await anyio.to_thread.run_sync(
        lambda: DatabaseMigrator(settings.database_config(), settings.migration_config()).current(check_heads=True)
    )
    engine = create_sql_engine(settings.database_config())
    try:
        async with transaction(create_session_factory(engine)) as session:
            if await session.get(WorkspaceRecord, config["workspace_id"]) is not None:
                print("Live-test identity already exists; retaining its credentials and resources.")
                return
            now = datetime.now(UTC)
            if await session.get(OrganizationRecord, config["organization_id"]) is None:
                session.add(
                    OrganizationRecord(id=config["organization_id"], name="Live tests", created_at=now, updated_at=now)
                )
            await session.flush()
            session.add(
                UserRecord(
                    id=config["user_id"],
                    email=f"{config['user_id']}@live.test",
                    normalized_email=f"{config['user_id']}@live.test",
                    name="Live test runner",
                    status="active",
                    email_verified_at=now,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.add(
                WorkspaceRecord(
                    id=config["workspace_id"],
                    organization_id=config["organization_id"],
                    name="Live tests",
                    normalized_name="live tests",
                    created_at=now,
                    updated_at=now,
                    deleted_at=None,
                )
            )
            await session.flush()
            for kind, resource_id in (
                ("organization", config["organization_id"]),
                ("workspace", config["workspace_id"]),
            ):
                if kind == "organization" and config.get("workspace_only"):
                    continue
                session.add(
                    RoleBindingRecord(
                        id=new_object_id("rb"),
                        organization_id=config["organization_id"],
                        workspace_id=config["workspace_id"] if kind == "workspace" else None,
                        principal_type="user",
                        principal_id=config["user_id"],
                        resource_type=kind,
                        resource_id=resource_id,
                        role_key="admin",
                        created_by_user_id=config["user_id"],
                        created_at=now,
                        updated_at=now,
                    )
                )
    finally:
        await engine.dispose()
    print(f"Created local Workspace {config['workspace_id']}; private configuration: {CONFIG}")


async def provision() -> None:
    config = load_config()
    async with httpx2.AsyncClient(
        base_url=config["control_url"],
        headers={"Authorization": "Bearer " + config["token"]},
        timeout=120,
        trust_env=False,
        follow_redirects=False,
    ) as http:
        client = LiveClient(config, http)
        base = f"/api/v1/workspaces/{config['workspace_id']}"

        async def create_once(key: str, path: str, body: dict) -> str:
            if key not in config:
                result = await client.request(
                    "POST",
                    path,
                    expected=201,
                    json=body,
                    headers={"Idempotency-Key": "live-setup-" + config["workspace_id"] + "-" + key},
                )
                config[key] = result["id"]
                save_config(config)
            return config[key]

        provider_id = await create_once(
            "model_provider_id",
            base + "/model-providers",
            {
                "type": "openai_compatible",
                "name": "Local live-test model",
                "credential": config["token"],
                "configuration": {"base_url": config["control_url"] + "/__live__/model/v1", "auth_mode": "bearer"},
            },
        )
        await create_once(
            "model_id",
            base + "/models",
            {
                "key": "live-fixture",
                "name": "Deterministic live model",
                "provider_id": provider_id,
                "upstream_model": "live-fixture",
                "model_api": "openai.chat_completions",
                "settings": {},
            },
        )
        environment_provider_id = await create_once(
            "environment_provider_id",
            base + "/environment-providers",
            {
                "type": "a13n.direct-local",
                "name": "Live-test local files",
                "configuration": {},
            },
        )
        await create_once(
            "environment_id",
            base + "/environments",
            {
                "provider_id": environment_provider_id,
                "configuration_schema_version": "1",
                "access": "full",
                "configuration": {
                    "root": {"path": config["workspace_root"]},
                    "shell_profiles": [{"profile_id": "sh", "executable": "/bin/sh", "fixed_arguments": ["-c"]}],
                    "allowed_executables": [sys.executable],
                    "max_wall_time_seconds": 180,
                },
            },
        )
        agent_config = {
            "model": {"model_key": "live-fixture", "characteristics": {"context_window": 32768}},
            "instructions": "Execute the local live-test scenario. Preserve the full conversation history.",
            "input_adapter": {"adapter_key": "native", "config": {}},
            "protocol": {"schema_version": "1", "public_name": "Live test", "output_modes": ["text"], "limits": {}},
        }
        await create_once("agent_id", base + "/agents", {"name": "Live test agent", "config": agent_config})
        if "approval_plugin_version_id" not in config:
            filename, body = approval_wheel()
            version = await client.request(
                "POST",
                "/api/v1/plugins",
                expected=201,
                params={"filename": filename},
                content=body,
                headers={"Content-Type": "application/octet-stream", "Idempotency-Key": "live-approval-" + filename},
            )
            config["approval_plugin_version_id"] = version["id"]
            save_config(config)
        await create_once(
            "approval_agent_id",
            base + "/agents",
            {
                "name": "Live approval agent",
                "config": {
                    **agent_config,
                    "plugins": [
                        {
                            "mode": "on_demand",
                            "instance_name": "approval",
                            "plugin_version_id": config["approval_plugin_version_id"],
                            "config": {"root": config["workspace_root"]},
                        }
                    ],
                },
            },
        )
    print(f"Provisioned local Agent {config['agent_id']} and approval Agent {config['approval_agent_id']}.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("init", "setup", "control", "worker", "authenticated-control"))
    args = parser.parse_args()
    if args.command == "init":
        anyio.run(initialize)
    elif args.command == "setup":
        anyio.run(provision)
    elif args.command == "authenticated-control":
        settings, app = authenticated_control(load_config())
        configure_logging(settings)
        uvicorn.run(app, host=settings.host, port=settings.port, workers=1, log_config=None)
    else:
        config = load_config()
        settings = settings_for(config, args.command)
        configure_logging(settings)
        uvicorn.run(local_app(config, args.command), host=settings.host, port=settings.port, workers=1, log_config=None)


if __name__ == "__main__":
    main()
