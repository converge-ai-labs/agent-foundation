"""Migrate an owned lab and seed identities without another interpreter."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import anyio
from a13n_service.database import DatabaseMigrator
from a13n_service.iam.models import OrganizationRecord, RoleBindingRecord, UserRecord, WorkspaceRecord
from a13n_service.ids import new_object_id
from a13n_service.settings import Settings
from a13n_service.storage import transaction
from a13n_service.storage.relational import create_session_factory, create_sql_engine


async def initialize(settings: Settings, identities: list[dict], *, migrate: bool = False) -> None:
    """Upgrade owned databases, or check pre-migrated databases before CLI seeding."""
    migrator = DatabaseMigrator(settings.database_config(), settings.migration_config())
    await anyio.to_thread.run_sync(migrator.upgrade if migrate else lambda: migrator.current(check_heads=True))
    engine = create_sql_engine(settings.database_config())
    try:
        for config in identities:
            Path(config["workspace_root"]).mkdir(parents=True, exist_ok=True, mode=0o700)
            async with transaction(create_session_factory(engine)) as session:
                if await session.get(WorkspaceRecord, config["workspace_id"]) is not None:
                    print("Live-test identity already exists; retaining its credentials and resources.")
                    continue
                now = datetime.now(UTC)
                if await session.get(OrganizationRecord, config["organization_id"]) is None:
                    session.add(
                        OrganizationRecord(
                            id=config["organization_id"],
                            key=config["organization_id"].replace("_", "-"),
                            name="Live tests",
                            created_at=now,
                            updated_at=now,
                        )
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
                        name=f"Live tests {config['workspace_id']}",
                        key=config["workspace_id"].replace("_", "-"),
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
                    session.add(
                        RoleBindingRecord(
                            id=new_object_id("rb"),
                            organization_id=config["organization_id"],
                            workspace_id=config["workspace_id"] if kind == "workspace" else None,
                            principal_type="user",
                            principal_id=config["user_id"],
                            resource_type=kind,
                            resource_id=resource_id,
                            role_key="member" if kind == "organization" and config.get("workspace_only") else "admin",
                            created_by_user_id=config["user_id"],
                            created_at=now,
                            updated_at=now,
                        )
                    )
    finally:
        await engine.dispose()


async def bootstrap(settings: Settings, config: dict) -> None:
    identities = [config]
    if "other_identity" in config:
        identities.append({**config, **config["other_identity"]})
    await initialize(settings, identities, migrate=True)
