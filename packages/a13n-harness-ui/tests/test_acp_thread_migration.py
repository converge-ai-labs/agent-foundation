"""ACP source migration retains existing Thread configuration rows."""

from pathlib import Path

import pytest
from a13n_harness_ui.storage.migration import DatabaseMigrator
from a13n_harness_ui.storage.models import ThreadConfigurationRecord
from a13n_harness_ui.storage.repositories import _configuration_value
from alembic import command
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


def test_acp_source_upgrade_preserves_native_rows(tmp_path: Path) -> None:
    path = tmp_path / "metadata.sqlite3"
    migrator = DatabaseMigrator(path)
    previous = "ae074f1f1a00"
    migrator._run(lambda config: command.upgrade(config, previous), write=True)
    engine = create_engine(f"sqlite:///{path}")
    insert_thread = text(
        "INSERT INTO thread (thread_id, metadata_version, archived, created_at, updated_at, "
        "initial_state_schema_version, initial_state_digest) "
        "VALUES (:id, 1, 0, '2026-01-01', '2026-01-01', '1', :digest)"
    )
    insert_config = text(
        "INSERT INTO thread_configuration (thread_id, version, agent_source_kind, agent_source_id, "
        "default_model_id, environment_profile_id, harness_plugin_ids_json, "
        "environment_run_extension_ids_json, mcp_server_ids_json) "
        "VALUES (:id, 3, :kind, :source, :model, 'full-control', '[]', '[]', '[\"mcp-test\"]')"
    )
    native = {"id": "native", "kind": "agent", "source": "agent-native", "model": "model-native"}
    acp = {"id": "external", "kind": "acp_agent", "source": "acp-example", "model": None}
    try:
        with engine.begin() as connection:
            connection.execute(insert_thread, {"id": "native", "digest": "1" * 64})
            connection.execute(insert_thread, {"id": "external", "digest": "2" * 64})
            connection.execute(insert_config, native)
            with pytest.raises(IntegrityError, match="CHECK constraint failed"):
                connection.execute(insert_config, acp)
        migrator.upgrade()
        migrator.verify_current()
        with engine.begin() as connection:
            connection.execute(insert_config, acp)
        with Session(engine) as session:
            values = {
                record.thread_id: _configuration_value(record)
                for record in session.scalars(select(ThreadConfigurationRecord))
            }
            assert values["native"].agent_source.model_dump() == {"kind": "agent", "id": "agent-native"}
            assert values["native"].default_model_id == "model-native"
            assert values["external"].agent_source.model_dump() == {"kind": "acp_agent", "id": "acp-example"}
            assert values["external"].version == 3
            assert values["external"].mcp_server_ids == ("mcp-test",)
        with pytest.raises(RuntimeError, match="external ACP Threads exist"):
            migrator._run(lambda config: command.downgrade(config, previous), write=True)
        migrator.verify_current()
    finally:
        engine.dispose()
