"""Fixture-owned infrastructure for storage contract tests."""

from __future__ import annotations

from base64 import b64encode
from collections.abc import AsyncGenerator, AsyncIterator, Iterator
from contextlib import AsyncExitStack, asynccontextmanager, nullcontext
from dataclasses import dataclass
from pathlib import Path
from shutil import copyfile
from time import monotonic, sleep
from typing import TYPE_CHECKING
from unittest.mock import Mock
from uuid import uuid4

import anyio
import psycopg
import pytest
from a13n_service.connectivity.runtime import ConnectivityDataRuntime, ConnectivityRuntime
from a13n_service.database import DatabaseMigrator
from a13n_service.database.metadata import service_metadata
from a13n_service.observability import ObservabilityRuntime
from a13n_service.process.runtime import ControlRuntime, ProcessRuntime, ProcessStatus, SharedRuntime
from a13n_service.settings import Settings
from a13n_service.storage.config import RedisMemoryConfig, RedisServerConfig
from a13n_service.storage.object_store import LocalObjectStore, ObjectStore, S3ObjectStore
from a13n_service.storage.redis import open_redis
from aiobotocore.config import AioConfig
from aiobotocore.httpxsession import HttpxSession
from aiobotocore.session import get_session
from botocore.exceptions import BotoCoreError, ClientError
from psycopg import sql
from pydantic_ai import prices
from redis.asyncio import Redis
from sqlalchemy import create_engine
from testcontainers.core.container import DockerContainer

from .process.s3_fixture import runner_s3_endpoint as _runner_s3_endpoint  # noqa: F401

if TYPE_CHECKING:
    from a13n_service.connectivity.ingress.admission import IngressEventService
    from a13n_service.iam import RequestAuthenticator

MINIO_IMAGE = "quay.io/minio/minio@sha256:14cea493d9a34af32f524e538b8346cf79f3321eff8e708c1e2960462bd8936e"


@pytest.fixture(autouse=True)
def no_background_price_downloads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(prices, "update_in_background", nullcontext)


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="package", autouse=True)
async def foundation_async_runner(anyio_backend: str) -> AsyncIterator[None]:
    # Selecting a session-scoped backend alone does not keep AnyIO's runner alive.
    # Lease it through Foundation fixture teardown so late SQLite worker callbacks
    # do not target a loop closed after an individual test. Other packages keep
    # their own runner lifetimes; database/session fixtures remain function-scoped.
    del anyio_backend
    yield


@pytest.fixture(scope="session")
def service_sqlite_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("service-database") / "template.sqlite3"
    engine = create_engine(f"sqlite:///{path}")
    service_metadata().create_all(engine)
    engine.dispose()
    return path


@pytest.fixture
def service_sqlite_database(tmp_path: Path, service_sqlite_template: Path) -> Path:
    path = tmp_path / "service.sqlite3"
    copyfile(service_sqlite_template, path)
    return path


@pytest.fixture(scope="session")
def pg_url() -> Iterator[str]:
    from testcontainers.community.postgres import PostgresContainer

    with PostgresContainer("postgres:17-alpine") as container:
        yield (
            f"postgresql+psycopg://{container.username}:{container.password}"
            f"@{container.get_container_host_ip()}:{container.get_exposed_port(5432)}/{container.dbname}"
        )


@pytest.fixture(scope="session")
def redis_url() -> Iterator[str]:
    from testcontainers.community.redis import RedisContainer

    with RedisContainer("redis:8-alpine") as container:
        yield f"redis://{container.get_container_host_ip()}:{container.get_exposed_port(6379)}/0"


@dataclass(frozen=True, slots=True)
class S3Service:
    endpoint_url: str
    access_key: str
    secret_key: str


@dataclass(frozen=True, slots=True)
class ProcessRuntimeFactory:
    """Build real process-runtime dataclasses for isolated router tests."""

    def __call__(
        self,
        *,
        settings: Settings | None = None,
        request_authenticator: RequestAuthenticator | None = None,
        agents: object | None = None,
        trace_queries: object | None = None,
        hook_subscriptions: object | None = None,
        lifecycle_events: object | None = None,
        gateway: object | None = None,
        ingress_events: IngressEventService | None = None,
    ) -> ProcessRuntime:
        placeholder = Mock()
        control = (
            ControlRuntime(
                trace_queries=trace_queries if trace_queries is not None else placeholder,
                environments=placeholder,
                plugins=placeholder,
                skill_uploads=placeholder,
                skill_publication=placeholder,
                skill_catalog=placeholder,
                agents=agents if agents is not None else placeholder,
                models=placeholder,
                model_providers=placeholder,
                assets=placeholder,
                hook_subscriptions=hook_subscriptions if hook_subscriptions is not None else placeholder,
                lifecycle_events=lifecycle_events if lifecycle_events is not None else placeholder,
                gateway=gateway if gateway is not None else placeholder,
            )
            if any(
                value is not None for value in (agents, trace_queries, hook_subscriptions, lifecycle_events, gateway)
            )
            else None
        )
        connectivity = (
            ConnectivityRuntime(
                control=None,
                data=ConnectivityDataRuntime(ingress_events=ingress_events),
            )
            if ingress_events is not None
            else None
        )
        return ProcessRuntime(
            settings=settings or Settings(_env_file=None),
            status=ProcessStatus(startup_complete=True),
            request_authenticator=request_authenticator,
            observability=Mock(spec=ObservabilityRuntime),
            shared=Mock(spec=SharedRuntime),
            control=control,
            worker=None,
            connectivity=connectivity,
        )


@pytest.fixture
def process_runtime_factory() -> ProcessRuntimeFactory:
    return ProcessRuntimeFactory()


@pytest.fixture(scope="session")
def s3_service() -> Iterator[S3Service]:
    access_key = "a13n-test-access"
    secret_key = "a13n-test-secret"
    container = (
        DockerContainer(MINIO_IMAGE)
        .with_env("MINIO_ROOT_USER", access_key)
        .with_env("MINIO_ROOT_PASSWORD", secret_key)
        .with_command("server /data")
        .with_exposed_ports(9000)
    )
    with container:
        host = container.get_container_host_ip()
        if host == "localhost":
            host = "127.0.0.1"
        endpoint = f"http://{host}:{_mapped_port(container, 9000)}"
        yield S3Service(endpoint, access_key, secret_key)


def _mapped_port(container: DockerContainer, port: int) -> int:
    deadline = monotonic() + 10
    while True:
        try:
            return container.get_exposed_port(port)
        except ConnectionError:
            if monotonic() >= deadline:
                raise
            sleep(0.05)


@pytest.fixture(params=["memory", "redis"])
async def redis_client(request: pytest.FixtureRequest) -> AsyncIterator[Redis]:
    if request.param == "memory":
        config = RedisMemoryConfig()
    else:
        config = RedisServerConfig(url=request.getfixturevalue("redis_url"))
    async with open_redis(config) as client:
        await client.flushdb()
        yield client
        await client.flushdb()


@pytest.fixture(params=["local", "s3"])
async def object_store(request: pytest.FixtureRequest, tmp_path: Path) -> AsyncIterator[ObjectStore]:
    if request.param == "local":
        yield await LocalObjectStore.create(tmp_path / "objects")
        return

    service: S3Service = request.getfixturevalue("s3_service")
    async with _open_s3_store(service) as store:
        yield store


@pytest.fixture
async def s3_object_store(s3_service: S3Service) -> AsyncIterator[S3ObjectStore]:
    async with _open_s3_store(s3_service) as store:
        yield store


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
        model_private_endpoint_cidrs=("127.0.0.0/8",),
        pricing_auto_update=False,
        observability_tracing=False,
        worker_poll_interval_seconds=0.05,
        worker_lease_seconds=3,
        worker_renewal_interval_seconds=0.3,
        worker_renewal_timeout_seconds=0.5,
        worker_drain_timeout_seconds=0.5,
        worker_cleanup_timeout_seconds=2,
        plugin_runner_shutdown_timeout_seconds=2,
    )
    try:
        await anyio.to_thread.run_sync(DatabaseMigrator(settings.database_config()).upgrade)
        yield settings
    finally:
        await anyio.to_thread.run_sync(database_command, sql.SQL("DROP DATABASE {} WITH (FORCE)"))


@asynccontextmanager
async def _open_s3_store(service: S3Service) -> AsyncGenerator[S3ObjectStore]:
    bucket = f"a13n-storage-{uuid4().hex}"
    config = AioConfig(
        connect_timeout=5,
        read_timeout=30,
        retries={"total_max_attempts": 1, "mode": "standard"},
        s3={"addressing_style": "path"},
        http_session_cls=HttpxSession,
    )
    async with AsyncExitStack() as stack:
        client = await stack.enter_async_context(
            get_session().create_client(
                "s3",
                endpoint_url=service.endpoint_url,
                region_name="us-east-1",
                aws_access_key_id=service.access_key,
                aws_secret_access_key=service.secret_key,
                config=config,
            )
        )
        await _wait_for_s3(client)
        await client.create_bucket(Bucket=bucket)
        store = S3ObjectStore(client, bucket)
        try:
            yield store
        finally:
            listed = await client.list_objects_v2(Bucket=bucket)
            for item in listed.get("Contents", []):
                await client.delete_object(Bucket=bucket, Key=item["Key"])
            await client.delete_bucket(Bucket=bucket)


async def _wait_for_s3(client) -> None:
    with anyio.fail_after(30):
        while True:
            try:
                await client.list_buckets()
            except ClientError as error:
                if error.response.get("Error", {}).get("Code") != "XMinioServerNotInitialized":
                    raise
            except BotoCoreError:
                pass
            else:
                return
            await anyio.sleep(0.25)
