"""Shared test infrastructure: one PostgreSQL and one Redis per session, a fresh database per test.

The schema is built once from metadata plus table rules into a template database; each test clones it with
`CREATE DATABASE ... TEMPLATE`, which takes milliseconds. `test_migrations.py` proves the generated
revisions build the same schema, so tests using the template exercise the migrated schema too.
"""

import asyncio
import base64
import json
import os
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import httpx2
import pytest
import uvicorn
from a13n_service.app import build_app, open_runtime
from a13n_service.distribution import OSS, Distribution
from a13n_service.infra.db import transaction
from a13n_service.migrations.runner import heads
from a13n_service.runs.attempts import AttemptControl
from a13n_service.runs.claim import claim
from a13n_service.runs.execute import execute
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.tables import RunRow
from a13n_service.settings import Database, ProcessRole, Settings
from a13n_service.tenancy import credentials
from a13n_service.tenancy.bootstrap import BootstrapInput, Bootstrapped, bootstrap
from argon2 import PasswordHasher
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from pydantic import SecretStr
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from testcontainers.core.config import testcontainers_config
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

TEMPLATE = "a13n_template"
EMAIL = "admin@example.com"
PASSWORD = "test-password-1234"

# Check container readiness every 0.1 s instead of every second, within the same two-minute budget.
testcontainers_config.sleep_time = 0.1
testcontainers_config.max_tries = 1200


def database_url(base: str, name: str) -> str:
    return make_url(base).set(database=name).render_as_string(hide_password=False)


def _admin(base: str) -> Engine:
    return create_engine(database_url(base, "postgres"), isolation_level="AUTOCOMMIT")


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    container = (
        PostgresContainer("postgres:17-alpine", driver="psycopg")
        # `DROP DATABASE` waits until every backend acknowledges a barrier, and a backend left in password
        # authentication by a cancelled connect acknowledges it only at `authentication_timeout` (60 s).
        .with_env("POSTGRES_HOST_AUTH_METHOD", "trust")
        # A disposable cluster needs no crash safety; skipping fsync starts it seconds sooner.
        .with_env("POSTGRES_INITDB_ARGS", "--no-sync")
        .with_command("postgres -c fsync=off")
    )
    with container as postgres:
        yield postgres.get_connection_url()


@pytest.fixture(scope="session")
def template_database(postgres_url: str) -> str:
    admin = _admin(postgres_url)
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{TEMPLATE}"'))
    admin.dispose()
    engine = create_engine(database_url(postgres_url, TEMPLATE))
    with engine.begin() as connection:
        OSS.metadata().create_all(connection)
        for statement in OSS.rules():
            connection.execute(text(statement))
        # The application checks the recorded head before serving; the template is at head by construction.
        connection.execute(text("CREATE TABLE alembic_version (version_num varchar(32) PRIMARY KEY)"))
        for head in heads(OSS):
            connection.execute(text("INSERT INTO alembic_version VALUES (:head)"), {"head": head})
    engine.dispose()
    return TEMPLATE


def _scratch_database(postgres_url: str, template: str | None) -> Iterator[Database]:
    name = f"t_{uuid4().hex}"
    admin = _admin(postgres_url)
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"' + (f' TEMPLATE "{template}"' if template else "")))
    try:
        yield Database(url=SecretStr(database_url(postgres_url, name)), auto_migrate=False)
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture
def empty_database(postgres_url: str) -> Iterator[Database]:
    """A database without schema, for migration tests."""
    yield from _scratch_database(postgres_url, None)


@pytest.fixture
def database(postgres_url: str, template_database: str) -> Iterator[Database]:
    yield from _scratch_database(postgres_url, template_database)


@pytest.fixture(scope="session")
def redis_url() -> Iterator[str]:
    # One Redis for the session; fixtures that use it flush it first.
    with RedisContainer("redis:8-alpine") as redis:
        yield f"redis://{redis.get_container_host_ip()}:{redis.get_exposed_port(6379)}/0"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session", autouse=True)
def _cheap_password_hashing() -> Iterator[None]:
    """Minimal Argon2 cost: a default-cost hash takes tens of milliseconds of CPU, and most tests log in."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(credentials, "_HASHER", PasswordHasher(time_cost=1, memory_cost=8, parallelism=1))
        yield


@pytest.fixture
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Removes inherited `A13N_` variables, which loading settings refuses, such as the impact recorder's."""
    for name in list(os.environ):
        if name.startswith("A13N_"):
            monkeypatch.delenv(name)


@pytest.fixture
def settings(database: Database, redis_url: str, tmp_path: Path) -> Settings:
    return Settings(
        database=database,
        redis={"url": redis_url},
        objects={"root": tmp_path / "objects"},
        encryption={"active_key_id": "test", "keys": {"test": base64.b64encode(b"k" * 32).decode()}},
        providers={"private_cidrs": ["127.0.0.0/8"], "require_https": False},
    )


@pytest.fixture
async def runtime(settings: Settings) -> AsyncIterator[Runtime]:
    async with AsyncExitStack() as stack:
        runtime = await open_runtime(stack, OSS, settings, executes=False)
        await runtime.redis.flushdb()
        yield runtime


@pytest.fixture
async def tenant(runtime: Runtime) -> Bootstrapped:
    return await bootstrap(runtime.storage, BootstrapInput(email=EMAIL, password=SecretStr(PASSWORD)))


@asynccontextmanager
async def _serve(
    settings: Settings, role: ProcessRole, distribution: Distribution = OSS
) -> AsyncIterator[SimpleNamespace]:
    app = build_app(distribution, role=role, settings=settings)
    async with app.router.lifespan_context(app):
        runtime = app.state.runtime
        await runtime.redis.flushdb()
        tenant = await bootstrap(runtime.storage, BootstrapInput(email=EMAIL, password=SecretStr(PASSWORD)))
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="https://service.test"
        ) as client:
            response = await client.post("/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
            assert response.status_code == 200, response.text
            client.headers["x-csrf-token"] = response.json()["csrf_token"]
            yield SimpleNamespace(
                app=app,
                runtime=runtime,
                client=client,
                tenant=tenant,
                workspace=f"/api/v1/workspaces/{tenant.workspace_id}",
                organization=f"/api/v1/organizations/{tenant.organization_id}",
            )


@pytest.fixture
async def service(settings: Settings) -> AsyncIterator[SimpleNamespace]:
    """The control-role application with a bootstrapped tenant and a logged-in administrator."""
    async with _serve(settings, "control") as served:
        yield served


@pytest.fixture
def serve(settings: Settings) -> Callable[..., AbstractAsyncContextManager[SimpleNamespace]]:
    """`service` for another `distribution` or other `settings`, entered by the test."""
    return partial(_serve, settings=settings, role="control")


@pytest.fixture
async def executing(settings: Settings) -> AsyncIterator[SimpleNamespace]:
    """The all-role application: `service` plus a worker executing runs, polling at test speed."""
    worker = settings.worker.model_copy(update={"scan_seconds": 0.05, "authority_seconds": 0.05})
    control = settings.control.model_copy(update={"scan_seconds": 0.05, "stream_refresh_seconds": 0.1})
    fast = settings.model_copy(update={"worker": worker, "control": control})
    async with _serve(fast, "all") as served:
        yield served


class ScriptedModel:
    """An OpenAI-compatible Chat Completions endpoint answering each request with the next scripted turn.

    Runs execute through the real provider adapter, HTTP client and endpoint policy. `requests` receives each
    request body as it arrives; a turn with a `gate` answers only once the test sets it. A turn scripted `to` a
    marker answers only a request whose body contains it, so concurrent agents each get their own turns. A reply
    said in several pieces streams them as separate chunks, `interval` seconds apart.
    """

    def __init__(self) -> None:
        self.turns: list[dict[str, Any]] = []
        self.scripted = asyncio.Event()
        self.requests: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.url = ""
        app = FastAPI()
        app.post("/v1/chat/completions")(self._complete)
        self.app = app

    def say(self, *pieces: str, gate: asyncio.Event | None = None, to: str | None = None, interval: float = 0) -> None:
        deltas = [{"content": piece} for piece in pieces]
        self._script({"deltas": deltas, "interval": interval, "finish": "stop", "gate": gate, "to": to})

    def call(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        call_id: str,
        gate: asyncio.Event | None = None,
        to: str | None = None,
    ) -> None:
        function = {"name": name, "arguments": json.dumps(arguments)}
        delta = {"tool_calls": [{"index": 0, "id": call_id, "type": "function", "function": function}]}
        self._script({"deltas": [delta], "interval": 0, "finish": "tool_calls", "gate": gate, "to": to})

    async def request(self) -> dict[str, Any]:
        return await asyncio.wait_for(self.requests.get(), timeout=10)

    def _script(self, turn: dict[str, Any]) -> None:
        self.turns.append(turn)
        self.scripted.set()

    async def _next(self, body: str) -> dict[str, Any]:
        async with asyncio.timeout(10):
            while True:
                for turn in self.turns:
                    if turn["to"] is None or turn["to"] in body:
                        self.turns.remove(turn)
                        return turn
                self.scripted.clear()
                await self.scripted.wait()

    async def _complete(self, request: Request) -> StreamingResponse:
        body = await request.json()
        self.requests.put_nowait(body)
        turn = await self._next(json.dumps(body))
        if turn["gate"] is not None:
            await turn["gate"].wait()
        base = {"id": "chatcmpl-scripted", "object": "chat.completion.chunk", "created": 0, "model": "scripted"}
        deltas = [{"role": "assistant", **turn["deltas"][0]}, *turn["deltas"][1:]]

        async def stream() -> AsyncIterator[str]:
            for index, delta in enumerate(deltas):
                if index and turn["interval"]:
                    await asyncio.sleep(turn["interval"])
                yield f"data: {json.dumps({**base, 'choices': [{'index': 0, 'delta': delta}]})}\n\n"
            ending = [
                {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": turn["finish"]}]},
                {**base, "choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 17}},
            ]
            yield "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in ending) + "data: [DONE]\n\n"

        return StreamingResponse(stream(), media_type="text/event-stream")


@asynccontextmanager
async def listening(app: Any) -> AsyncIterator[str]:
    """Serve an ASGI app on a loopback port, for clients that need a real socket (streams, provider adapters)."""
    config = uvicorn.Config(
        app, host="127.0.0.1", port=0, log_level="warning", lifespan="off", timeout_graceful_shutdown=1
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)
    try:
        yield f"http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}"
    finally:
        server.should_exit = True
        await task


@pytest.fixture
def listen() -> Any:
    return listening


@pytest.fixture
async def scripted_model() -> AsyncIterator[ScriptedModel]:
    model = ScriptedModel()
    async with listening(model.app) as url:
        model.url = f"{url}/v1"
        yield model


# Runs: helpers the test_runs*.py modules share through the `runs_kit` fixture.

SEALED = frozenset({"waiting", "completed", "failed", "cancelled"})
# A question for `ask_user_question`, the only tool of a question-only wait.
QUESTION = {
    "question": "Which color?",
    "header": "Color",
    "options": [{"label": "red", "description": "Red"}, {"label": "blue", "description": "Blue"}],
    "multiSelect": False,
}


async def create_model(service: SimpleNamespace, model: ScriptedModel) -> str:
    provider = await service.client.post(
        f"{service.organization}/model-providers",
        json={
            "workspace_id": None,
            "type": "openai",
            "name": "Scripted",
            "config": {"base_url": model.url},
            "credential": {"api_key": "sk-scripted"},
        },
    )
    assert provider.status_code == 201, provider.text
    created = await service.client.post(
        f"{service.organization}/models",
        json={
            "workspace_id": None,
            "provider_id": provider.json()["id"],
            "key": "scripted",
            "name": "Scripted",
            "config": {"model_name": "scripted", "model_api": "openai.chat_completions"},
        },
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


async def add_agent(service: SimpleNamespace, key: str, model_id: str, **config: Any) -> dict[str, Any]:
    agent = await service.client.post(
        f"{service.workspace}/agents",
        json={"key": key, "name": key.title(), "config": {"model": {"model_id": model_id}, **config}},
    )
    assert agent.status_code == 201, agent.text
    return agent.json()


async def create_agent(service: SimpleNamespace, model: ScriptedModel, **config: Any) -> dict[str, Any]:
    return await add_agent(service, "helper", await create_model(service, model), **config)


async def delegating(service: SimpleNamespace, model: ScriptedModel, mode: str, **config: Any) -> dict[str, Any]:
    """A coordinator delegating to a worker; each agent's requests carry its own role marker.

    `config` extends the coordinator's; `worker` and `edge` in it configure the worker agent and its edge.
    """
    model_id = await create_model(service, model)
    worker_config = config.pop("worker", {})
    edge = config.pop("edge", {})
    worker = await add_agent(service, "worker", model_id, instructions="Role: worker", **worker_config)
    return await add_agent(
        service,
        "coordinator",
        model_id,
        instructions="Role: coordinator",
        subagent_mode=mode,
        subagents={"helper": {"agent_id": worker["id"], "description": "Computes answers", **edge}},
        **config,
    )


def message(agent: dict[str, Any], text: str, **fields: Any) -> dict[str, Any]:
    return {"agent_id": agent["id"], "payload": {"content": [{"type": "text", "text": text}]}, **fields}


def fresh_key() -> dict[str, str]:
    return {"idempotency-key": uuid4().hex}


def if_match(resource: dict[str, Any]) -> dict[str, str]:
    return {"if-match": f'"{resource["id"]}:{resource["version"]}"'}


async def start_thread(service: SimpleNamespace, agent: dict[str, Any], text: str, **fields: Any) -> dict[str, Any]:
    response = await service.client.post(
        f"{service.workspace}/threads", json=message(agent, text, **fields), headers=fresh_key()
    )
    assert response.status_code == 201, response.text
    return response.json()


async def submit(service: SimpleNamespace, thread_id: str, body: dict[str, Any]) -> httpx2.Response:
    return await service.client.post(f"{service.workspace}/threads/{thread_id}/inbox", json=body, headers=fresh_key())


async def get_thread(service: SimpleNamespace, thread_id: str) -> dict[str, Any]:
    response = await service.client.get(f"{service.workspace}/threads/{thread_id}")
    assert response.status_code == 200, response.text
    return response.json()


async def inbox(service: SimpleNamespace, thread_id: str) -> list[dict[str, Any]]:
    """The thread's entries in inbox order."""
    response = await service.client.get(f"{service.workspace}/threads/{thread_id}/inbox")
    assert response.status_code == 200, response.text
    return response.json()["items"]


async def get_run(service: SimpleNamespace, run_id: str) -> dict[str, Any]:
    response = await service.client.get(f"{service.workspace}/runs/{run_id}")
    assert response.status_code == 200, response.text
    return response.json()


async def sealed(service: SimpleNamespace, run_id: str) -> dict[str, Any]:
    async with asyncio.timeout(20):
        while (run := await get_run(service, run_id))["status"] not in SEALED:
            await asyncio.sleep(0.05)
    return run


async def checkpointed(service: SimpleNamespace, run_id: str) -> None:
    """Wait until the run committed its first checkpoint: the one before its first model call."""
    async with asyncio.timeout(10):
        while True:
            async with transaction(service.runtime.storage) as session:
                run = await session.get(RunRow, run_id)
                if run is not None and run.checkpoint is not None:
                    return
            await asyncio.sleep(0.02)


async def items(service: SimpleNamespace, run_id: str) -> dict[str, Any]:
    response = await service.client.get(f"{service.workspace}/runs/{run_id}/items")
    assert response.status_code == 200, response.text
    return response.json()


def texts(listing: dict[str, Any]) -> list[tuple[str | None, str]]:
    return [
        (item["content"].get("role"), item["content"]["text"])
        for item in listing["items"]
        if item["kind"] == "text_message" and item["content"].get("metadata", {}).get("display", True)
    ]


def user_texts(request: dict[str, Any]) -> list[str]:
    return [message["content"] for message in request["messages"] if message["role"] == "user"]


async def attempt(
    service: SimpleNamespace, *, runtime: Runtime | None = None, handoff: bool = False
) -> asyncio.Task[None]:
    """Claim the next due run as a worker would and execute it in a task; `runtime` replaces the service's."""
    runtime = runtime or service.runtime
    async with asyncio.timeout(10):
        while not (leases := await claim(runtime, worker_id="worker-test", worker_build="test", limit=1)):
            await asyncio.sleep(0.05)
    control = AttemptControl(deadline=asyncio.get_running_loop().time() + runtime.settings.worker.lease_seconds)
    if handoff:
        control.handoff.set()
    return asyncio.create_task(execute(runtime, leases[0], control))


async def pause_sweeps(service: SimpleNamespace) -> None:
    """Stop the control sweeps, so the test alone drives acceptance, lease expiry and outbox delivery."""
    (sweeps,) = [task for task in service.app.state.background if task.get_name() == "control-sweeps"]
    sweeps.cancel()
    await asyncio.gather(sweeps, return_exceptions=True)


async def bearer(service: SimpleNamespace) -> dict[str, str]:
    issued = await service.client.post(
        "/api/v1/users/me/keys", json={"name": "stream", "workspace_id": service.tenant.workspace_id}
    )
    assert issued.status_code == 201, issued.text
    return {"authorization": f"Bearer {issued.json()['secret']}"}


type Frame = tuple[str, str | None, Any]


@asynccontextmanager
async def frames(url: str, headers: dict[str, str]) -> AsyncIterator[AsyncIterator[Frame]]:
    """SSE frames as (event, id, data)."""
    async with httpx2.AsyncClient(timeout=20) as client, client.stream("GET", url, headers=headers) as response:
        assert response.status_code == 200

        async def parse() -> AsyncIterator[Frame]:
            event, entry_id, data = "", None, ""
            async for line in response.aiter_lines():
                if line.startswith("event: "):
                    event = line.removeprefix("event: ")
                elif line.startswith("id: "):
                    entry_id = line.removeprefix("id: ")
                elif line.startswith("data: "):
                    data = line.removeprefix("data: ")
                elif line == "" and event:
                    yield event, entry_id, json.loads(data)
                    event, entry_id, data = "", None, ""

        yield parse()


async def until(stream: AsyncIterator[Frame], wanted: Callable[[Frame], bool]) -> list[Frame]:
    seen = []
    async with asyncio.timeout(20):
        async for frame in stream:
            seen.append(frame)
            if wanted(frame):
                return seen
    raise AssertionError("stream ended early")


@pytest.fixture
def runs_kit() -> SimpleNamespace:
    """Helpers for runs tests: agents on the scripted model, threads and their inbox, attempts and waits on runs."""
    return SimpleNamespace(
        SEALED=SEALED,
        QUESTION=QUESTION,
        create_model=create_model,
        add_agent=add_agent,
        create_agent=create_agent,
        delegating=delegating,
        message=message,
        fresh_key=fresh_key,
        if_match=if_match,
        start_thread=start_thread,
        submit=submit,
        get_thread=get_thread,
        inbox=inbox,
        get_run=get_run,
        sealed=sealed,
        checkpointed=checkpointed,
        items=items,
        texts=texts,
        user_texts=user_texts,
        attempt=attempt,
        pause_sweeps=pause_sweeps,
        bearer=bearer,
        frames=frames,
        until=until,
    )
