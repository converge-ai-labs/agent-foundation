import json

from a13n_harness_ui.storage.migration import DatabaseMigrator
from alembic import command
from sqlalchemy import create_engine, inspect, text


def test_expansion_moves_obligations_once_and_keeps_old_writers_compatible(tmp_path):
    path = tmp_path / "metadata.sqlite3"
    migrator = DatabaseMigrator(path)
    migrator._run(lambda config: command.upgrade(config, "2a7cffc5be82"), write=True)
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO bot_inbox (input_id,source,event_type,source_event_id,text,created_at) "
                    "VALUES ('tick','heartbeat','heartbeat','tick','Read HEARTBEAT.md','2026-11-12')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO tool_execution (execution_id,thread_id,record_json,terminal,created_at,"
                    "foreground_released,delivery_acknowledged,completion_state) VALUES "
                    "('exec-one','thread-a',:record,1,'2026-11-12',1,0,'pending')"
                ),
                {"record": json.dumps({"tool_name": "shell_exec", "status": "succeeded"})},
            )
        migrator.upgrade()
        migrator.upgrade()
        migrator.verify_current()
        with engine.begin() as connection:
            assert "bot_inbox" in inspect(connection).get_table_names()
            assert "completion_state" in {
                column["name"] for column in inspect(connection).get_columns("tool_execution")
            }
            rows = connection.execute(text("SELECT id,thread_id FROM thread_inbox ORDER BY id")).all()
            assert rows == [("result:exec-one", "thread-a"), ("tick", "thread_bot")]
            assert connection.execute(text("SELECT count(*) FROM bot_inbox")).scalar_one() == 0
            assert connection.execute(text("SELECT completion_state FROM tool_execution")).scalar_one() == "consumed"
            # Legacy structures remain writable; new code never imports their later inputs.
            connection.execute(
                text(
                    "INSERT INTO bot_inbox (input_id,source,event_type,source_event_id,text,created_at) "
                    "VALUES ('old-tick','heartbeat','heartbeat','next','Legacy owner','2026-11-13')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO tool_execution (execution_id,thread_id,record_json,terminal,created_at,"
                    "foreground_released,delivery_acknowledged) VALUES ('exec-new','thread-a','{}',0,'2026-11-13',0,0)"
                )
            )
        migrator.upgrade()
        with engine.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM thread_inbox")).scalar_one() == 2
            assert connection.execute(text("SELECT count(*) FROM bot_inbox")).scalar_one() == 1
    finally:
        engine.dispose()
