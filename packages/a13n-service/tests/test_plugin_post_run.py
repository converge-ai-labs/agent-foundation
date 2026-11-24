"""Exercise both plugin phases through the real Worker and provider transport."""

import asyncio
from dataclasses import replace

import pytest
from a13n_harness import AbstractHarnessPlugin
from a13n_harness.model_affinity import derive_model_affinity_id
from a13n_service import app as assembly
from a13n_service.distribution import OSS
from a13n_service.infra.db import short_session, transaction
from a13n_service.migrations import apps
from a13n_service.resources.models import runtime as model_runtime
from a13n_service.runs.tables import ThreadRow, UsageRecordRow
from a13n_service.tenancy.app_plugins import app_router
from a13n_service.tenancy.requests import CurrentRuntime
from sqlalchemy import Column, String, Table, select

from .conftest import _skill_service
from .test_agents import PLUGINS, _Factory
from .test_app_database import app_database  # noqa: F401
from .test_app_plugins import APP_ID, installed_apps, toggle

pytestmark = pytest.mark.anyio


async def test_worker_post_run_seals_before_replay_and_publishes_in_app_db(
    app_database,  # noqa: F811
    settings,
    service_app,
    scripted_model,
    runs_kit,
    monkeypatch,
):
    db = app_database(APP_ID)
    reports = Table(
        db.name("reports"),
        db.metadata,
        Column("run_id", String, primary_key=True),
        Column("text", String, nullable=False),
    )

    def migrate():
        apps.initialize(db)
        apps.generate(settings.database, OSS, db, "create reports")
        apps.upgrade(settings.database, OSS, db)

    await asyncio.to_thread(migrate)
    router = app_router(APP_ID)

    @router.get("/report/{run_id}")
    async def report(run_id: str, runtime: CurrentRuntime):
        async with short_session(runtime.storage) as session:
            return {"text": await session.scalar(select(reports.c.text).where(reports.c.run_id == run_id))}

    (installed,) = installed_apps(routers=(router,))
    monkeypatch.setattr(
        assembly, "discover_apps", lambda: (replace(installed, definition=replace(installed.definition, database=db)),)
    )
    monkeypatch.setattr(assembly, "build_harness_plugin_factory_catalog", lambda **_: PLUGINS)
    connections = []

    @scripted_model.app.middleware("http")
    async def capture(request, call_next):
        connections.append((request.client, request.headers["x-thread"]))
        return await call_next(request)

    opened = []
    open_http = model_runtime.open_http

    def track_client(*args, **kwargs):
        opened.append(True)
        return open_http(*args, **kwargs)

    monkeypatch.setattr(model_runtime, "open_http", track_client)
    replay_gate = asyncio.Event()
    published = asyncio.Event()

    class Reports(AbstractHarnessPlugin):
        plugin_id = "reports"

        async def for_run(self, context):
            return Reports()

        async def wrap_run(self, exchange, call_next):
            files = exchange.context.environment.files
            await files.write_text("post-run.txt", "workspace data", mode="create")
            result = await call_next(exchange)
            self.contents = (await files.read_text("post-run.txt")).text
            return result

        async def post_run(self, context):
            text = await context.agent.complete_text(f"Report: {self.contents}")
            async with transaction(service.runtime.storage) as session:
                await session.execute(reports.insert().values(run_id=run_id, text=text))
            published.set()

    monkeypatch.setattr(_Factory, "create_plugin", lambda *_: Reports())
    worker = settings.worker.model_copy(update={"slots": 1})
    async with _skill_service(settings.model_copy(update={"worker": worker}), service_app, "all") as service:
        await toggle(service, True)
        provider = await service.client.post(
            f"{service.api}/model-providers",
            json={
                "type": "openai",
                "name": "Scripted",
                "config": {"base_url": scripted_model.url, "session_affinity_header": "x-thread"},
                "credential": {"api_key": "sk-scripted"},
            },
        )
        assert provider.status_code == 201, provider.text
        model = await service.client.post(
            f"{service.api}/models",
            json={
                "provider_id": provider.json()["id"],
                "key": "scripted",
                "name": "Scripted",
                "config": {"model_name": "scripted", "model_api": "openai.chat_completions"},
            },
        )
        assert model.status_code == 201, model.text
        agent = await runs_kit.add_agent(
            service, "reports", "scripted", plugins=[{"plugin_key": "test.audit", "instance_name": "reports"}]
        )
        scripted_model.say("Main answer")
        scripted_model.say("App report", gate=replay_gate, to="Report: workspace data")
        initial = await runs_kit.start_thread(service, agent, "Start", options={"max_usage": {"requests": 1}})
        run_id = initial["run"]["id"]
        thread_id = initial["thread"]["id"]
        await scripted_model.request()
        replay = await scripted_model.request()
        assert replay["messages"][-1]["content"] == "Report: workspace data"
        assert not replay.get("stream", False)
        run = await runs_kit.get_run(service, run_id)
        assert run["status"] == "completed", run
        async with short_session(service.runtime.storage) as session:
            thread = await session.get_one(ThreadRow, thread_id)
            assert thread.current_run_id is None
            usage = select(UsageRecordRow.digest).where(UsageRecordRow.run_id == run_id)
            sealed_usage = set(await session.scalars(usage))
            assert sealed_usage
        assert not published.is_set()
        assert len(opened) == 1
        assert connections[0] == connections[1]
        assert connections[0][1] == derive_model_affinity_id(thread_id)

        # A second Run on the only Worker slot can finish while the first hook is still in flight.
        plain = await runs_kit.add_agent(service, "plain", "scripted")
        scripted_model.say("Second answer", to="Second run")
        successor = await runs_kit.start_thread(service, plain, "Second run")
        assert (await runs_kit.sealed(service, successor["run"]["id"]))["status"] == "completed"
        assert not published.is_set()
        replay_gate.set()
        await asyncio.wait_for(published.wait(), timeout=5)
        response = await service.client.get(f"/api/v1/apps/{APP_ID}/report/{run_id}")
        assert response.status_code == 200, response.text
        assert response.json() == {"text": "App report"}
        async with short_session(service.runtime.storage) as session:
            assert set(await session.scalars(usage)) == sealed_usage
