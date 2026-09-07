from datetime import timedelta

import pytest
from a13n_service.durable_operations.models import IdempotencyEvidenceRecord
from a13n_service.iam.models import SecurityAuditRecord, WorkspaceRecord
from a13n_service.secrets.cleanup import SecretOwnerCleanup
from a13n_service.secrets.models import SecretRecord
from a13n_service.skills.models import SkillUploadRecord
from a13n_service.skills.retention import SkillUploadRetention
from a13n_service.storage import short_session, transaction
from a13n_service.storage.config import SQLiteConfig
from a13n_service.storage.relational import create_session_factory, create_sql_engine
from a13n_service.temporal import utc_now
from sqlalchemy import select

from tests.interactions.conftest import ORGANIZATION_ID, USER_ID, WORKSPACE_ID, _seed_interaction_database

pytestmark = pytest.mark.anyio


@pytest.fixture(params=("sqlite", "postgres"))
async def collection_sessions(request, service_sqlite_database):
    from a13n_service.database.metadata import service_metadata
    from a13n_service.storage.config import PostgreSQLConfig

    config = (
        SQLiteConfig(path=service_sqlite_database)
        if request.param == "sqlite"
        else PostgreSQLConfig(url=request.getfixturevalue("pg_url"))
    )
    engine = create_sql_engine(config)
    if request.param == "postgres":
        async with engine.begin() as connection:
            await connection.run_sync(service_metadata().create_all)
    sessions = create_session_factory(engine)
    await _seed_interaction_database(sessions)
    try:
        yield sessions
    finally:
        if request.param == "postgres":
            async with engine.begin() as connection:
                await connection.run_sync(service_metadata().drop_all)
        await engine.dispose()


async def test_owner_cleanup_is_bounded_idempotent_and_audited(collection_sessions):
    sessions = collection_sessions
    now = utc_now()
    async with transaction(sessions) as database:
        for index in range(2):
            database.add(
                SecretRecord(
                    id=f"sec_cleanup{index}",
                    organization_id=ORGANIZATION_ID,
                    workspace_id=WORKSPACE_ID,
                    owner_type="workspace" if index == 0 else "user",
                    owner_id=WORKSPACE_ID if index == 0 else USER_ID,
                    key=f"test-{index}",
                    version=1,
                    ciphertext=b"encrypted",
                    nonce=b"0" * 12,
                    encryption_key_id="test-key",
                    created_at=now,
                    value_updated_at=now,
                )
            )
    cleanup = SecretOwnerCleanup(sessions, batch_limit=1)
    assert (await cleanup.scan()).completed == 0
    async with transaction(sessions) as database:
        workspace = await database.get(WorkspaceRecord, WORKSPACE_ID)
        workspace.deleted_at = now
    assert (await cleanup.scan()).completed == 1
    assert (await cleanup.scan()).completed == 1
    assert (await cleanup.scan()).completed == 0
    async with short_session(sessions) as database:
        records = tuple(await database.scalars(select(SecretRecord)))
        assert len(records) == 2
        assert all(
            row.deleted_at is not None
            and row.ciphertext is None
            and row.nonce is None
            and row.encryption_key_id is None
            and row.version == 1
            for row in records
        )
        audits = tuple(await database.scalars(select(SecurityAuditRecord)))
        assert len(audits) == 2
        assert {row.resource_id for row in audits} == {row.id for row in records}
        assert all(row.actor_type == "system" and row.details == {"reason": "owner_deleted"} for row in audits)


async def test_upload_retention_waits_for_receipt_replay_then_progresses(collection_sessions):
    sessions = collection_sessions
    now = utc_now()
    async with transaction(sessions) as database:
        for name in ("pinned", "expired", "live"):
            database.add(
                SkillUploadRecord(
                    id=f"su_{name}",
                    organization_id=ORGANIZATION_ID,
                    workspace_id=WORKSPACE_ID,
                    uploader_type="user",
                    uploader_id=USER_ID,
                    archive_sha256="a" * 64,
                    manifest={},
                    created_at=now - timedelta(days=2),
                    expires_at=now + timedelta(days=1) if name == "live" else now - timedelta(days=1),
                )
            )
        database.add(
            IdempotencyEvidenceRecord(
                id="ide_upload_retention",
                organization_id=ORGANIZATION_ID,
                workspace_id=WORKSPACE_ID,
                boundary_scope_id=WORKSPACE_ID,
                actor_type="user",
                actor_id=USER_ID,
                operation="skill.upload",
                scope_id=WORKSPACE_ID,
                key_digest="b" * 64,
                request_digest="c" * 64,
                result_kind="skill_upload",
                result_ref=WORKSPACE_ID,
                receipt_json={"upload_id": "su_pinned"},
                created_at=now - timedelta(days=2),
                expires_at=now + timedelta(days=1),
            )
        )
    collector = SkillUploadRetention(sessions, batch_limit=1)
    assert (await collector.scan()).completed == 1
    assert (await collector.scan()).completed == 0
    async with transaction(sessions) as database:
        assert await database.get(SkillUploadRecord, "su_expired") is None
        assert await database.get(SkillUploadRecord, "su_pinned") is not None
        assert await database.get(SkillUploadRecord, "su_live") is not None
        evidence = await database.get(IdempotencyEvidenceRecord, "ide_upload_retention")
        evidence.expires_at = now - timedelta(hours=1)
    assert (await collector.scan()).completed == 1
    async with short_session(sessions) as database:
        assert tuple(await database.scalars(select(SkillUploadRecord.id))) == ("su_live",)


async def test_deleted_workspace_tombstones_assets_with_cleanup_intent_and_removes_grants(collection_sessions):
    from a13n_service.assets.models import AssetRecord
    from a13n_service.durable_operations.models import OutboxRecord
    from a13n_service.iam.cleanup import OwnerCleanup
    from a13n_service.iam.models import RoleBindingRecord

    now = utc_now()
    async with transaction(collection_sessions) as database:
        database.add(
            AssetRecord(
                id="ast_owner",
                organization_id=ORGANIZATION_ID,
                workspace_id=WORKSPACE_ID,
                filename="test.txt",
                media_type="text/plain",
                size_bytes=1,
                content_sha256="a" * 64,
                source_kind="upload",
                source_principal_type="user",
                source_principal_id=USER_ID,
                created_at=now,
            )
        )
        (await database.get(WorkspaceRecord, WORKSPACE_ID)).deleted_at = now
    cleanup = OwnerCleanup(collection_sessions, batch_limit=1)
    for _ in range(12):
        await cleanup.scan()
    async with short_session(collection_sessions) as database:
        assert (await database.get(AssetRecord, "ast_owner")).deleted_at is not None
        intents = tuple(await database.scalars(select(OutboxRecord).where(OutboxRecord.source_id == "ast_owner")))
        assert len(intents) == 1 and intents[0].status == "pending"
        assert not tuple(
            await database.scalars(select(RoleBindingRecord).where(RoleBindingRecord.workspace_id == WORKSPACE_ID))
        )


async def test_asset_tombstone_waits_for_cleanup_and_audit_without_starving_peer(collection_sessions):
    from a13n_service.assets.models import AssetRecord
    from a13n_service.assets.retention import AssetRetention
    from a13n_service.durable_operations.models import OutboxRecord
    from a13n_service.iam.audit import SystemAuditActor, security_audit_record

    now = utc_now()
    async with transaction(collection_sessions) as database:
        for name in ("audit", "cleanup", "free"):
            database.add(
                AssetRecord(
                    id=f"ast_{name}",
                    organization_id=ORGANIZATION_ID,
                    workspace_id=WORKSPACE_ID,
                    filename="test.txt",
                    media_type="text/plain",
                    size_bytes=1,
                    content_sha256="a" * 64,
                    source_kind="upload",
                    source_principal_type="user",
                    source_principal_id=USER_ID,
                    created_at=now - timedelta(days=4),
                    deleted_at=now - timedelta(days=3),
                )
            )
        database.add(
            OutboxRecord(
                id="obx_asset",
                source_kind="asset",
                source_id="ast_cleanup",
                destination_kind="asset_content_cleanup",
                destination_ref="primary",
                status="pending",
                available_at=now,
                claim_generation=0,
                attempt_count=0,
                created_at=now,
                updated_at=now,
            )
        )
        database.add(
            security_audit_record(
                audit_id="aud_asset",
                actor=SystemAuditActor(request_id=None),
                organization_id=ORGANIZATION_ID,
                workspace_id=WORKSPACE_ID,
                action="asset.delete",
                resource_type="asset",
                resource_id="ast_audit",
                outcome="success",
                occurred_at=now,
                details=None,
            )
        )
    collector = AssetRetention(collection_sessions, minimum_age=timedelta(days=1), batch_limit=1)
    for _ in range(5):
        await collector.scan()
    async with short_session(collection_sessions) as database:
        assert await database.get(AssetRecord, "ast_free") is None
        assert await database.get(AssetRecord, "ast_audit") is not None
        assert await database.get(AssetRecord, "ast_cleanup") is not None
