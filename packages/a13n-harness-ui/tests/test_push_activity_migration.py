"""Activity migration retains opt-ins without fabricating device activity."""

from a13n_harness_ui.storage.migration import DatabaseMigrator
from alembic import command
from sqlalchemy import create_engine, inspect, text


def test_push_activity_upgrade_and_downgrade_preserve_subscription_and_signing_key(tmp_path, before_comment_retirement):
    path = tmp_path / "metadata.sqlite3"
    migrator = DatabaseMigrator(path)
    migrator._run(lambda config: command.upgrade(config, "e416fbd4674c"), write=True)
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO web_push_key VALUES (1, 'private-key')"))
            connection.execute(
                text(
                    "INSERT INTO web_push_subscription "
                    "(subscription_id, endpoint, p256dh, auth, origin, thread_ids_json, updated_at) "
                    "VALUES ('device', 'endpoint', 'public-key', 'auth', 'origin', '[\"thread-one\"]', '2026-09-01')"
                )
            )
        migrator.upgrade()
        migrator.verify_current()
        with engine.connect() as connection:
            row = connection.execute(text("SELECT * FROM web_push_subscription")).mappings().one()
            assert row["last_active_at"] is None
            assert row["endpoint"] == "endpoint"
            assert row["p256dh"] == "public-key"
            assert row["auth"] == "auth"
            assert row["origin"] == "origin"
            assert row["updated_at"] == "2026-09-01"
            assert "thread_ids_json" not in row
            assert connection.execute(text("SELECT private_key FROM web_push_key")).scalar_one() == "private-key"
        migrator._run(lambda config: command.downgrade(config, "e416fbd4674c"), write=True)
        with engine.connect() as connection:
            assert connection.execute(text("SELECT thread_ids_json FROM web_push_subscription")).scalar_one() == "[]"
            columns = {column["name"]: column for column in inspect(connection).get_columns("web_push_subscription")}
            assert not columns["thread_ids_json"]["nullable"]
            assert columns["thread_ids_json"]["default"] is None
            assert "last_active_at" not in columns
        migrator.upgrade()
        migrator.verify_current()
    finally:
        engine.dispose()
