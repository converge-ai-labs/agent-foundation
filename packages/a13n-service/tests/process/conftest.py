"""Independent process databases copied from a fully migrated, closed template."""

from base64 import b64encode
from collections.abc import Callable
from functools import partial
from pathlib import Path
from uuid import uuid4

import anyio
import psycopg
import pytest
from a13n_service.database import DatabaseMigrator
from a13n_service.settings import Settings
from psycopg import sql

from .s3_fixture import runner_s3_endpoint as _runner_s3_endpoint  # noqa: F401
from .support import local_settings as build_local_settings


@pytest.fixture(scope="session")
def migrated_process_database(tmp_path_factory: pytest.TempPathFactory) -> Path:
    settings = build_local_settings(tmp_path_factory.mktemp("process-database-template"))
    return settings.database_sqlite_path


@pytest.fixture
def local_settings(migrated_process_database: Path) -> Callable[..., Settings]:
    return partial(build_local_settings, database_template=migrated_process_database)


@pytest.fixture
async def runner_settings(pg_url, redis_url, runner_s3_endpoint, tmp_path, monkeypatch):
    """One disposable database and bucket shared by the Supervisor and real children."""
    database_name = f"runner_{uuid4().hex}"
    dsn = pg_url.replace("postgresql+psycopg://", "postgresql://")

    def database_command(command):
        with psycopg.connect(dsn, autocommit=True) as connection:
            connection.execute(command.format(sql.Identifier(database_name)))

    await anyio.to_thread.run_sync(database_command, sql.SQL("CREATE DATABASE {}"))
    # Bypass OS-level proxies too: these tests assert direct socket teardown.
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "runner-test-access")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "runner-test-secret")
    # Keep the production Runner exit budget: interpreter GC follows the EXITING acknowledgement.
    settings = Settings(
        _env_file=None,
        plugin_runtime_mode="runner",
        database_backend="postgresql",
        database_url=pg_url.rsplit("/", 1)[0] + "/" + database_name,
        redis_backend="redis",
        redis_url=redis_url,
        object_backend="s3",
        object_bucket="bucket",
        object_endpoint_url=runner_s3_endpoint,
        object_force_path_style=True,
        filesystem_root=tmp_path / "files",
        secret_master_key_base64=b64encode(b"0123456789abcdef0123456789abcdef").decode(),
        secret_encryption_key_id="runner-test-key",
        connectivity_public_origin="http://testserver",
        connectivity_http_origins=("http://testserver",),
        iam_initial_admin_email="admin@example.com",
        iam_public_origin="https://testserver",
        model_private_endpoint_cidrs=("127.0.0.0/8",),
        pricing_auto_update=False,
        observability_tracing=False,
        worker_poll_interval_seconds=0.05,
        worker_lease_seconds=12,
        worker_drain_seconds=0.5,
        worker_cleanup_seconds=2,
    )
    try:
        await anyio.to_thread.run_sync(DatabaseMigrator(settings.database_config()).upgrade)
        yield settings
    finally:
        await anyio.to_thread.run_sync(database_command, sql.SQL("DROP DATABASE {} WITH (FORCE)"))
