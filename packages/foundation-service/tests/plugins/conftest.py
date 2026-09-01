from __future__ import annotations

import base64
import csv
import hashlib
import io
import zipfile
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from a13n_service.agent_presets.domain import PluginRuntimeMode
from a13n_service.database.metadata import service_metadata
from a13n_service.iam import AuthenticatedActor, PrincipalRef
from a13n_service.iam.models import OrganizationRecord, RoleBindingRecord, UserRecord, WorkspaceRecord
from a13n_service.plugins.objects import PluginObjectStore
from a13n_service.plugins.service import PluginService
from a13n_service.plugins.staging import PluginStaging
from a13n_service.storage import transaction
from a13n_service.storage.config import SQLiteConfig
from a13n_service.storage.object_store import LocalObjectStore
from a13n_service.storage.relational import create_session_factory, create_sql_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

NOW = datetime(2026, 9, 2, 10, 0, tzinfo=UTC)
ORG_ID = "org_1234567890abcdef"
WORKSPACE_ID = "ws_1234567890abcdef"
ADMIN_ID = "usr_admin12345678901"
BUILDER_ID = "usr_builder123456789"


def actor(user_id: str = ADMIN_ID) -> AuthenticatedActor:
    return AuthenticatedActor(
        principal=PrincipalRef(principal_type="user", principal_id=user_id),
        auth_method="session",
        credential_id="ses_plugin1234567890",
        boundary_workspace_id=WORKSPACE_ID,
        request_id="req-plugin-test",
    )


async def wheel_body(value: bytes) -> AsyncIterator[bytes]:
    for index in range(0, len(value), 137):
        yield value[index : index + 137]


def build_wheel(
    *,
    version: str = "1.0.0",
    plugin_key: str = "acme.audit",
    distribution_name: str = "acme-audit",
    package: str = "acme_audit",
    requires_dist: tuple[str, ...] = (),
    second_entry_point: bool = False,
    corrupt_record: bool = False,
    unsafe_member: bool = False,
) -> bytes:
    dist_info = f"{distribution_name.replace('-', '_')}-{version}.dist-info"
    requirement_lines = "".join(f"Requires-Dist: {value}\n" for value in requires_dist)
    entry_points = f"[a13n_harness.plugins]\n{plugin_key} = {package}.factory:Factory\n"
    if second_entry_point:
        entry_points += f"other.plugin = {package}.factory:OtherFactory\n"
    files: dict[str, bytes] = {
        f"{package}/__init__.py": b"",
        f"{package}/factory.py": b"class Factory:\n    pass\n",
        f"{dist_info}/METADATA": (
            "Metadata-Version: 2.4\n"
            f"Name: {distribution_name}\n"
            f"Version: {version}\n"
            "Requires-Python: >=3.13\n"
            f"{requirement_lines}\n"
        ).encode(),
        f"{dist_info}/WHEEL": (
            b"Wheel-Version: 1.0\nGenerator: agent-foundation-tests\nRoot-Is-Purelib: true\nTag: py3-none-any\n\n"
        ),
        f"{dist_info}/entry_points.txt": entry_points.encode(),
    }
    if unsafe_member:
        files["../escape.py"] = b"pass\n"
    record_name = f"{dist_info}/RECORD"
    rows: list[tuple[str, str, str]] = []
    for name, content in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=").decode()
        rows.append((name, f"sha256={digest}", str(len(content))))
    rows.append((record_name, "", ""))
    record_buffer = io.StringIO(newline="")
    csv.writer(record_buffer, lineterminator="\n").writerows(rows)
    files[record_name] = record_buffer.getvalue().encode()
    if corrupt_record:
        factory = files[f"{package}/factory.py"]
        files[f"{package}/factory.py"] = factory[:-1] + b"!"
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return output.getvalue()


@pytest.fixture
async def plugin_sessions(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_sql_engine(SQLiteConfig(path=tmp_path / "plugins.sqlite3"))
    async with engine.begin() as connection:
        await connection.run_sync(service_metadata().create_all)
    sessions = create_session_factory(engine)
    async with transaction(sessions) as session:
        session.add(OrganizationRecord(id=ORG_ID, name="Test", version=1, created_at=NOW, updated_at=NOW))
        await session.flush()
        session.add(
            WorkspaceRecord(
                id=WORKSPACE_ID,
                organization_id=ORG_ID,
                name="Default",
                normalized_name="default",
                version=1,
                created_at=NOW,
                updated_at=NOW,
                deleted_at=None,
            )
        )
        await session.flush()
        session.add_all(
            (
                UserRecord(
                    id=ADMIN_ID,
                    email="admin@example.com",
                    normalized_email="admin@example.com",
                    name="Admin",
                    status="active",
                    email_verified_at=NOW,
                    version=1,
                    created_at=NOW,
                    updated_at=NOW,
                ),
                UserRecord(
                    id=BUILDER_ID,
                    email="builder@example.com",
                    normalized_email="builder@example.com",
                    name="Builder",
                    status="active",
                    email_verified_at=NOW,
                    version=1,
                    created_at=NOW,
                    updated_at=NOW,
                ),
            )
        )
        await session.flush()
        session.add_all(
            (
                RoleBindingRecord(
                    id="rb_plugin_org_admin",
                    organization_id=ORG_ID,
                    workspace_id=None,
                    principal_type="user",
                    principal_id=ADMIN_ID,
                    resource_type="organization",
                    resource_id=ORG_ID,
                    role_key="admin",
                    created_by_user_id=ADMIN_ID,
                    created_at=NOW,
                    updated_at=NOW,
                ),
                RoleBindingRecord(
                    id="rb_plugin_org_member",
                    organization_id=ORG_ID,
                    workspace_id=None,
                    principal_type="user",
                    principal_id=BUILDER_ID,
                    resource_type="organization",
                    resource_id=ORG_ID,
                    role_key="member",
                    created_by_user_id=ADMIN_ID,
                    created_at=NOW,
                    updated_at=NOW,
                ),
                RoleBindingRecord(
                    id="rb_plugin_ws_builder",
                    organization_id=ORG_ID,
                    workspace_id=WORKSPACE_ID,
                    principal_type="user",
                    principal_id=BUILDER_ID,
                    resource_type="workspace",
                    resource_id=WORKSPACE_ID,
                    role_key="builder",
                    created_by_user_id=ADMIN_ID,
                    created_at=NOW,
                    updated_at=NOW,
                ),
            )
        )
    yield sessions
    await engine.dispose()


@pytest.fixture
async def plugin_service(
    plugin_sessions: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> PluginService:
    objects = await LocalObjectStore.create(tmp_path / "objects")
    staging = await PluginStaging.create(tmp_path / "files")
    service = PluginService(
        plugin_sessions,
        PluginObjectStore(objects),
        staging,
        runtime_mode=PluginRuntimeMode.on_demand,
        max_wheel_bytes=10 * 1024 * 1024,
        max_expanded_bytes=20 * 1024 * 1024,
        max_archive_members=1000,
        clock=lambda: NOW,
    )
    await service.ensure_runtime_mode()
    return service
