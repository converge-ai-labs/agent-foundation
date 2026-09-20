"""Retained HTTP keys move to their business owners without copying responses."""

from datetime import timedelta

import pytest
from a13n_service.agent_configuration.domain import ConfigurationApplicationReceipt
from a13n_service.agent_configuration.models import ConfigurationApplicationRecord
from a13n_service.database import DatabaseMigrator
from a13n_service.durable_operations.entity_keys import scope_key
from a13n_service.durable_operations.idempotency import EvidenceScope, digest_visible_ascii_key
from a13n_service.interactions.records import run_record
from a13n_service.storage.relational import database_url
from sqlalchemy import MetaData, Table, create_engine, inspect, null, select
from sqlalchemy.exc import DBAPIError
from tests.interactions.conftest import (
    AGENT_ID,
    AGENT_REVISION_ID,
    NOW,
    ORGANIZATION_ID,
    SESSION_ID,
    THREAD_ID,
    USER_ID,
    WORKSPACE_ID,
    agent_config,
)
from tests.interactions.test_acceptance import _accepted_run


def test_populated_upgrade_preserves_keys_past_old_expiry(postgres_database):
    migrator = DatabaseMigrator(postgres_database)
    migrator.upgrade("ec56e4426fb7")
    engine = create_engine(database_url(postgres_database))
    metadata = MetaData()
    names = (
        "organizations",
        "workspaces",
        "sessions",
        "skill_uploads",
        "idempotency_evidence",
        "agents",
        "agent_revisions",
        "threads",
        "runs",
        "configuration_drafts",
        "configuration_applications",
    )
    tables = {name: Table(name, metadata, autoload_with=engine) for name in names}
    key_digest = digest_visible_ascii_key("retained-key")
    upload_id = "sku_1234567890abcdef"
    try:
        with engine.begin() as connection:
            connection.execute(
                tables["organizations"]
                .insert()
                .values(
                    id=ORGANIZATION_ID,
                    key="migration",
                    name="Migration",
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
            connection.execute(
                tables["workspaces"]
                .insert()
                .values(
                    id=WORKSPACE_ID,
                    organization_id=ORGANIZATION_ID,
                    key="migration",
                    name="Migration",
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
            connection.execute(
                tables["sessions"]
                .insert()
                .values(
                    id=SESSION_ID,
                    organization_id=ORGANIZATION_ID,
                    workspace_id=WORKSPACE_ID,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
            connection.execute(
                tables["skill_uploads"]
                .insert()
                .values(
                    id=upload_id,
                    organization_id=ORGANIZATION_ID,
                    workspace_id=WORKSPACE_ID,
                    uploader_type="user",
                    uploader_id=USER_ID,
                    archive_sha256="a" * 64,
                    manifest={},
                    created_at=NOW,
                    expires_at=NOW + timedelta(hours=24),
                )
            )
            application = _seed_historical_results(connection, tables, key_digest)
            for index, operation, result, receipt in (
                (0, "configuration.session.create", SESSION_ID, {"id": SESSION_ID}),
                (1, "skill_upload.stage", WORKSPACE_ID, {"response": {"upload_id": upload_id}, "created": True}),
                (2, "queue.reorder", "thread_retained", {"queue_version": 1}),
                (3, "run.accept", "run_1234567890abcdef", {}),
                (4, "run.interrupt", "run_1234567890abcdef", {}),
                (5, "configuration.draft.apply", application.draft_id, application.model_dump(mode="json")),
                (6, "configuration.draft.apply", application.draft_id, application.model_dump(mode="json")),
            ):
                connection.execute(
                    tables["idempotency_evidence"]
                    .insert()
                    .values(
                        id=f"idem_migration_{index}",
                        organization_id=ORGANIZATION_ID,
                        workspace_id=WORKSPACE_ID,
                        boundary_scope_id=WORKSPACE_ID,
                        actor_type="user",
                        actor_id=USER_ID,
                        operation=operation,
                        scope_id=application.draft_id if index in (5, 6) else WORKSPACE_ID,
                        key_digest=digest_visible_ascii_key("alias-key") if index == 6 else key_digest,
                        request_digest="b" * 64,
                        result_kind="test",
                        result_ref=result,
                        receipt_json=receipt,
                        created_at=NOW,
                        expires_at=NOW + timedelta(hours=24),
                    )
                )
        migrator.upgrade()
        current = MetaData()
        sessions = Table("sessions", current, autoload_with=engine)
        uploads = Table("skill_uploads", current, autoload_with=engine)
        evidence = Table("idempotency_evidence", current, autoload_with=engine)
        runs = Table("runs", current, autoload_with=engine)
        applications = Table("configuration_applications", current, autoload_with=engine)
        with engine.connect() as connection:
            for table, operation in ((sessions, "configuration.session.create"), (uploads, "skill_upload.stage")):
                expected = scope_key(EvidenceScope(WORKSPACE_ID, "user", USER_ID, operation, WORKSPACE_ID), key_digest)
                assert connection.scalar(select(table.c.request_key)) == expected
            remaining = connection.execute(select(evidence).order_by(evidence.c.id)).mappings().all()
            assert [(row["operation"], row["result_ref"]) for row in remaining] == [
                ("queue.reorder", "thread_retained"),
                ("configuration.draft.apply", "cap_1234567890abcdef"),
            ]
            assert remaining[1]["key_digest"] == digest_visible_ascii_key("alias-key")
            migrated = ConfigurationApplicationRecord(**connection.execute(select(applications)).mappings().one())
            assert migrated.to_resource() == application
            run = connection.execute(select(runs)).mappings().one()
            for column, operation in (("request_key", "run.accept"), ("interrupt_key", "run.interrupt")):
                assert run[column] == scope_key(
                    EvidenceScope(WORKSPACE_ID, "user", USER_ID, operation, WORKSPACE_ID), key_digest
                )
            assert run["sealed_at"] == NOW
            columns = {item["name"] for item in inspect(connection).get_columns("idempotency_evidence")}
            assert {"request_digest", "receipt_json", "expires_at"}.isdisjoint(columns)
        with pytest.raises(DBAPIError, match="sealed"):
            with engine.begin() as connection:
                connection.execute(runs.update().values(version=2))
        with pytest.raises(RuntimeError, match="forward repair or database restore"):
            migrator.downgrade("ec56e4426fb7")
        with engine.connect() as connection:
            assert connection.scalar(select(runs.c.request_key)) == run["request_key"]
    finally:
        engine.dispose()


def _seed_historical_results(connection, tables, key_digest):
    connection.execute(
        tables["agents"]
        .insert()
        .values(
            id=AGENT_ID,
            organization_id=ORGANIZATION_ID,
            workspace_id=WORKSPACE_ID,
            source="custom",
            name="Migration",
            key="migration",
            default_revision_id=AGENT_REVISION_ID,
            enabled=True,
            created_by_type="user",
            created_by_id=USER_ID,
            updated_by_type="user",
            updated_by_id=USER_ID,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    connection.execute(
        tables["agent_revisions"]
        .insert()
        .values(
            id=AGENT_REVISION_ID,
            organization_id=ORGANIZATION_ID,
            workspace_id=WORKSPACE_ID,
            agent_id=AGENT_ID,
            version=1,
            config=agent_config().model_dump(mode="json", by_alias=True),
            config_digest="b" * 64,
            content_digest="c" * 64,
            resolved_model={},
            resolved_skills=[],
            connection_tools=[],
            resolved_subagents=[],
            created_by_type="user",
            created_by_id=USER_ID,
            created_at=NOW,
        )
    )
    connection.execute(
        tables["threads"]
        .insert()
        .values(
            id=THREAD_ID,
            organization_id=ORGANIZATION_ID,
            session_id=SESSION_ID,
            role="root",
            origin_kind="new",
            version=1,
            queue_version=0,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    record = run_record(
        _accepted_run(
            run_id="run_1234567890abcdef",
            thread_id=THREAD_ID,
            idempotency_key="internal-run-key",
        )
    )
    values = {
        name: value if value is not None else null() for name, value in vars(record).items() if name in tables["runs"].c
    }
    values.update(
        request_fingerprint="d" * 64,
        status="cancelled",
        sealed_at=NOW,
        failure_json={"code": "cancelled", "message": "Cancelled"},
    )
    connection.execute(tables["runs"].insert().values(**values))
    draft_id = "cfd_1234567890abcdef"
    connection.execute(
        tables["configuration_drafts"]
        .insert()
        .values(
            id=draft_id,
            organization_id=ORGANIZATION_ID,
            workspace_id=WORKSPACE_ID,
            session_id=SESSION_ID,
            mode="create",
            source_selector="empty",
            version=1,
            config={},
            content_digest="e" * 64,
            status="open",
            evidence_refs=[],
            created_at=NOW,
            updated_at=NOW,
        )
    )
    application = ConfigurationApplicationReceipt(
        draft_id=draft_id,
        reviewed_version=1,
        reviewed_digest="e" * 64,
        reviewed_mode="create",
        reviewed_target_agent_id=None,
        reviewed_base_agent_revision_id=None,
        reviewed_creation_metadata=None,
        agent_id=AGENT_ID,
        agent_revision_id=AGENT_REVISION_ID,
        agent_revision_version=1,
        applied_by_user_id=USER_ID,
        applied_at=NOW,
        no_change=False,
    )
    connection.execute(
        tables["configuration_applications"]
        .insert()
        .values(
            id="cap_1234567890abcdef",
            draft_id=draft_id,
            organization_id=ORGANIZATION_ID,
            workspace_id=WORKSPACE_ID,
            reviewed_version=1,
            agent_revision_id=AGENT_REVISION_ID,
            key_hash=key_digest,
            request_digest="f" * 64,
            receipt=application.model_dump(mode="json"),
            created_at=NOW,
        )
    )
    return application
