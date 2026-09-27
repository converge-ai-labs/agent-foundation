"""Shared Service processes, isolated workspaces, and dedicated stacks for process faults."""

import re
import signal
from collections.abc import AsyncIterator, Generator, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import httpx2
import pytest
from redis.asyncio import Redis
from sqlalchemy import Engine, create_engine
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

from dev.fixtures.process import fixture_process

from .api import Workspace, eventually, expect
from .scripted import ScriptedModel
from .stack import (
    EMAIL,
    PASSWORD,
    ServiceProcess,
    Stores,
    cloned_database,
    database_url,
    prepare_template,
    read_rows,
    trust,
    wait_ready,
    write_config,
)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--require-all",
        action="store_true",
        help="Fail instead of skipping journeys whose external dependency is unavailable (CI)",
    )
    parser.addoption(
        "--hosted",
        action="store_true",
        help="Also run the journeys on hosted sandbox vendors, which need their accounts and create billable sandboxes",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "hosted(type): a journey on a hosted sandbox vendor, run only when asked for")
    config.addinivalue_line(
        "markers", "isolated_service(worker_slots=4): owns Service processes for faults or custom worker capacity"
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Hosted journeys run only when asked for: with `--hosted`, a `-m` expression naming the `hosted` marker, or a
    `-k` expression naming the journey's vendor type; any other selection leaves them out."""
    if config.getoption("--hosted") or re.search(r"\bhosted\b", config.getoption("markexpr") or ""):
        return
    named = set(re.findall(r"\w+", config.getoption("keyword") or ""))
    left_out = [item for item in items if (marker := item.get_closest_marker("hosted")) and marker.args[0] not in named]
    if left_out:
        config.hook.pytest_deselected(items=left_out)
        items[:] = [item for item in items if item not in left_out]


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item: pytest.Item) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    report = yield
    if report.when == "call":
        item.stash[FAILED] = report.failed
    return report


FAILED = pytest.StashKey[bool]()


@pytest.fixture(scope="session")
def stores(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Stores]:
    with (
        PostgresContainer("postgres:17-alpine", driver="psycopg") as postgres,
        RedisContainer("redis:8-alpine") as redis,
    ):
        redis_url = f"redis://{redis.get_container_host_ip()}:{redis.get_exposed_port(6379)}/0"
        yield prepare_template(tmp_path_factory.mktemp("stores"), postgres.get_connection_url(), redis_url)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@dataclass
class Stack:
    """One journey's Service: its processes, its workspace client, the scripted model and read-only stores."""

    api: Workspace
    model: ScriptedModel
    control: ServiceProcess
    workers: list[ServiceProcess]
    redis: Redis
    database: Engine
    directory: Path
    stores: Stores

    def client(self, **headers: str) -> httpx2.AsyncClient:
        """Another HTTPS client of Control, such as one authenticated by an API key."""
        return httpx2.AsyncClient(
            base_url=self.control.url, verify=trust(self.stores), trust_env=False, timeout=10, headers=headers
        )

    @contextmanager
    def only(self, worker: ServiceProcess) -> Iterator[None]:
        """Suspend the other workers, so that runs accepted meanwhile are claimed by `worker`."""
        others = [other for other in self.workers if other is not worker]
        for other in others:
            other.signal(signal.SIGSTOP)
        try:
            yield
        finally:
            for other in others:
                other.signal(signal.SIGCONT)


@dataclass
class Service:
    """Processes and database owned together; no async clients cross test event loops."""

    control: ServiceProcess
    workers: list[ServiceProcess]
    database: Engine
    cookies: dict[str, str]
    csrf_token: str


@contextmanager
def running_service(stores: Stores, directory: Path, *, worker_slots: int = 4) -> Iterator[Service]:
    with ExitStack() as cleanup:
        database = cleanup.enter_context(cloned_database(stores))
        config = write_config(
            directory / "service.toml", stores, database, directory / "objects", worker_slots=worker_slots
        )
        control = ServiceProcess("control", config, directory / "control.log")
        workers = [ServiceProcess("worker", config, directory / f"worker-{index}.log") for index in (1, 2)]
        for process in [control, *workers]:
            cleanup.callback(process.kill)
            process.start()
        wait_ready([control, *workers], trust(stores))
        engine = create_engine(database_url(stores.postgres_url, database))
        cleanup.callback(engine.dispose)
        with httpx2.Client(base_url=control.url, verify=trust(stores), trust_env=False, timeout=10) as client:
            login = expect(client.post("/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD}), 200)
            cookies = dict(client.cookies)
        yield Service(control, workers, engine, cookies, login["csrf_token"])


@pytest.fixture(scope="session")
def shared_service(stores: Stores, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Service]:
    with running_service(stores, tmp_path_factory.mktemp("service")) as service:
        yield service


async def settled(service: Service, workspace_id: str) -> None:
    """Do not reuse a Service while the previous journey can still do work."""
    remaining: list[dict] = []

    async def idle() -> bool:
        nonlocal remaining
        for process in [service.control, *service.workers]:
            if not process.running:
                pytest.exit(f"Shared Service process exited: {process.log}\n{process.tail()}", returncode=1)
        remaining = read_rows(
            service.database,
            "SELECT 'run' AS kind, id FROM runs WHERE workspace_id = :workspace AND status IN ('accepted', 'running')"
            " UNION ALL SELECT 'attempt', id FROM run_attempts"
            " WHERE workspace_id = :workspace AND status IN ('leased', 'running')"
            " UNION ALL SELECT 'entry', id FROM inbox_entries WHERE workspace_id = :workspace AND status = 'pending'"
            " UNION ALL SELECT 'outbox', id FROM outbox WHERE workspace_id = :workspace AND status = 'pending'",
            workspace=workspace_id,
        )
        return not remaining

    try:
        await eventually(idle)
    except TimeoutError:
        # Continuing would make subsequent failures depend on this journey's leftover work.
        pytest.exit(f"Service did not settle for workspace {workspace_id}: {remaining}", returncode=1)


@pytest.fixture
async def stack(stores: Stores, tmp_path: Path, request: pytest.FixtureRequest) -> AsyncIterator[Stack]:
    isolated = request.node.get_closest_marker("isolated_service")
    with ExitStack() as cleanup:
        service = (
            cleanup.enter_context(running_service(stores, tmp_path, **isolated.kwargs))
            if isolated
            else request.getfixturevalue("shared_service")
        )
        model_url = cleanup.enter_context(fixture_process("dev.fixtures.scripted_model"))
        async with (
            httpx2.AsyncClient(
                base_url=service.control.url,
                verify=trust(stores),
                trust_env=False,
                timeout=10,
                cookies=service.cookies,
                headers={"x-csrf-token": service.csrf_token},
            ) as client,
            Redis.from_url(stores.redis_url, decode_responses=True) as redis,
        ):
            workspace = expect(
                await client.post(
                    f"/api/v1/organizations/{stores.tenant['organization_id']}/workspaces",
                    json={"key": f"journey-{uuid4().hex}", "name": request.node.name},
                ),
                201,
            )
            model = ScriptedModel(model_url)
            try:
                yield Stack(
                    api=Workspace(client, {**stores.tenant, "workspace_id": workspace["id"]}),
                    model=model,
                    control=service.control,
                    workers=service.workers,
                    redis=redis,
                    database=service.database,
                    directory=tmp_path,
                    stores=stores,
                )
            finally:
                try:
                    if not isolated:
                        await settled(service, workspace["id"])
                finally:
                    await model.aclose()
                    if request.node.stash.get(FAILED, False):
                        for process in [service.control, *service.workers]:
                            print(f"--- {process.role} {process.log.name} ---\n{process.tail()}")
