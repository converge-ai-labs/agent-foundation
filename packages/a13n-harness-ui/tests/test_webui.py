from __future__ import annotations

import json
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
import uvicorn
from a13n_harness_ui.app import AppState, HarnessUiApp, open_harness_ui_app
from a13n_harness_ui.model_accounts import (
    AccountProjection,
    Availability,
    CodexAccountStore,
    ExpiryStatus,
    Provider,
    StoreKind,
)
from a13n_harness_ui.webui import create_webui, openapi_document
from ag_ui.core import EventType, TextMessageContentEvent
from anyio import Event, create_task_group, fail_after, sleep

from .test_app import _CompletedReconstructor, _settings, _SlowReconstructor, _write_configuration


@pytest.fixture
def account_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "codex").mkdir()
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))


@asynccontextmanager
async def _network_server(server) -> AsyncIterator[str]:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        runner = uvicorn.Server(
            uvicorn.Config(server, access_log=False, log_level="error", timeout_graceful_shutdown=1)
        )
        async with create_task_group() as tasks:

            async def serve() -> None:
                await runner.serve(sockets=[listener])

            tasks.start_soon(serve)
            with fail_after(5):
                while not runner.started:
                    await sleep(0.01)
            try:
                yield f"http://127.0.0.1:{port}"
            finally:
                runner.should_exit = True


async def _first_frame(response: httpx.Response) -> dict:
    response.raise_for_status()
    assert response.headers["content-type"].startswith("text/event-stream")
    with fail_after(5):
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                return json.loads(line[6:])
    raise AssertionError("No stream frame")


@pytest.mark.anyio
async def test_webui_auth_static_strict_json_and_one_lifetime(tmp_path: Path, account_home: None) -> None:
    del account_home
    opened: list[HarnessUiApp] = []
    assets = tmp_path / "static"
    assets.mkdir()
    (assets / "index.html").write_text("<h1>Static app without secrets</h1>")

    @asynccontextmanager
    async def factory():
        async with open_harness_ui_app(
            _settings(tmp_path / "state"), configuration_path=_write_configuration(tmp_path), host_mode="webui"
        ) as app:
            opened.append(app)
            yield app

    server = create_webui(factory, api_key="test-secret", static_root=assets)
    async with (
        server.router.lifespan_context(server),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=server), base_url="http://127.0.0.1") as client,
    ):
        assert len(opened) == 1
        assert (await client.get("/threads/new")).status_code == 200
        assert (await client.get("/api/status")).status_code == 401
        assert (await client.post("/api/setup/apply", content="bad")).status_code == 401
        client.headers["Authorization"] = "Bearer test-secret"
        response = await client.get("/api/status")
        assert response.status_code == 200
        assert response.json()["access"] == "api_key"
        assert response.headers["cache-control"] == "no-store"
        assert "test-secret" not in response.text
        assert (await client.get("/api/status", headers={"Origin": "https://evil.example"})).status_code == 403
        assert (await client.get("/api/status", headers={"Host": "evil.example"})).status_code == 400
        assert (await client.get("/api/missing")).status_code == 404
        assert (await client.get("/assets/missing.js")).status_code == 404
        assert (await client.get("/health/missing")).status_code == 404
        invalid = await client.post("/api/threads", json={"title": 12})
        assert invalid.status_code == 400
        created = await client.post("/api/threads", json={"title": "First"})
        assert created.status_code == 200
        assert (await client.get("/api/threads")).json()["total"] == 1
        schema = (await client.get("/api/openapi.json")).json()
        assert schema["info"]["version"] == "1"
        assert "SetupSelection" in schema["components"]["schemas"]
        assert "test-secret" not in json.dumps(schema)
    assert opened[0].state is AppState.closed


@pytest.mark.anyio
async def test_webui_setup_preview_apply_and_account_retry(
    tmp_path: Path, account_home: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    del account_home

    async def available(self):
        return AccountProjection(
            provider=Provider.CODEX,
            availability=Availability.AVAILABLE,
            source=StoreKind.FILE,
            usable=True,
            expiry=ExpiryStatus.VALID,
        )

    monkeypatch.setattr(CodexAccountStore, "inspect", available)
    path = tmp_path / "config" / "config.yaml"
    server = create_webui(
        lambda: open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=path, host_mode="webui"),
        api_key=None,
    )
    async with (
        server.router.lifespan_context(server),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=server), base_url="http://127.0.0.1") as client,
    ):
        status = (await client.get("/api/setup?rediscover=true")).json()
        assert status["needed"]
        assert status["providers"][0]["selected"]
        selection = {
            "providers": ["codex"],
            "default_agent": "agent-codex",
            "project_path": str(tmp_path),
            "environment_profile": "environment-native",
        }
        preview = await client.post("/api/setup/preview", json=selection)
        assert preview.status_code == 200, preview.text
        assert not path.exists()
        assert "gpt-5.6-luna" in preview.json()["files"]["models/codex-review.yaml"]
        result = await client.post(
            "/api/setup/apply", json={"selection": selection, "expected_generation": preview.json()["generation"]}
        )
        assert result.status_code == 200, result.text
        assert result.json()["completed"]
        assert not (await client.get("/api/setup")).json()["needed"]
        assert (await client.get("/api/threads")).json()["total"] == 0
        assert (
            await client.post(
                "/api/environments/preflight", json={"profile_id": "environment-native", "project_path": str(tmp_path)}
            )
        ).json()["ready"]


@pytest.mark.anyio
async def test_real_sse_disconnect_cleanup_resume_and_wrong_lineage(tmp_path: Path, account_home: None) -> None:
    del account_home
    opened: list[HarnessUiApp] = []

    @asynccontextmanager
    async def factory():
        async with open_harness_ui_app(
            _settings(tmp_path / "state"), configuration_path=_write_configuration(tmp_path), host_mode="webui"
        ) as app:
            app._root_runs._executor._agents = _CompletedReconstructor()
            opened.append(app)
            yield app

    server = create_webui(factory, api_key="test-key")
    async with (
        _network_server(server) as url,
        httpx.AsyncClient(trust_env=False, base_url=url, headers={"Authorization": "Bearer test-key"}) as client,
    ):
        app = opened[0]
        first = await app.create_thread(title="First")
        second = await app.create_thread(title="Second")
        for _ in range(3):
            async with client.stream("GET", "/api/events") as response:
                assert (await _first_frame(response))["kind"] == "open"
            with fail_after(2):
                while app._summary_hub._subscribers:
                    await sleep(0.01)
        async with client.stream("GET", f"/api/threads/{first.thread_id}/events") as response:
            frame = await _first_frame(response)
            assert frame["kind"] == "snapshot"
            cursor = frame["resume_cursor"]
        with fail_after(2):
            while app._live_hub._subscribers:
                await sleep(0.01)
        await app._live_hub.publish(
            run_kind="root",
            root_thread_id=first.thread_id,
            thread_id=first.thread_id,
            run_id="run-test",
            parent_thread_id=None,
            events=[
                TextMessageContentEvent(
                    type=EventType.TEXT_MESSAGE_CONTENT, message_id="message-test", delta="while disconnected"
                )
            ],
        )
        async with client.stream("GET", f"/api/threads/{first.thread_id}/events", params={"after": cursor}) as response:
            resumed = await _first_frame(response)
            assert resumed["kind"] == "event"
            assert resumed["event"]["payload"]["delta"] == "while disconnected"
        async with client.stream(
            "GET", f"/api/threads/{second.thread_id}/events", params={"after": cursor}
        ) as response:
            assert (await _first_frame(response))["kind"] == "reset"
        async with client.stream("GET", f"/api/threads/{first.thread_id}/events") as response:
            fresh = await _first_frame(response)
            assert fresh["snapshot"]["recent_events"][-1]["payload"]["delta"] == "while disconnected"
        receipt = await app.submit_thread(thread_id=first.thread_id, prompt="Still works")
        assert (await app.wait_root_operation(receipt.receipt_id)).status.value == "completed"
        started = Event()
        app._root_runs._executor._agents = _SlowReconstructor(started)
        running = await app.submit_thread(thread_id=first.thread_id, prompt="Keep running")
        with fail_after(3):
            await started.wait()
        async with client.stream("GET", f"/api/threads/{first.thread_id}/events") as response:
            assert (await _first_frame(response))["kind"] == "snapshot"
        assert (await app.active_root_operation(first.thread_id)).receipt.receipt_id == running.receipt_id
        assert (await app.cancel_root_operation(running.receipt_id)).accepted
        await app.wait_root_operation(running.receipt_id)
    assert opened[0].state is AppState.closed


@pytest.mark.anyio
async def test_preflight_disconnect_cancels_probe_without_readiness(
    tmp_path: Path, account_home: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    del account_home
    import a13n_harness_ui.app as app_module
    from anyio import sleep_forever

    started = Event()
    ended = Event()
    opened: list[HarnessUiApp] = []

    async def probe(*args, **kwargs):
        started.set()
        try:
            await sleep_forever()
        finally:
            ended.set()

    monkeypatch.setattr(app_module, "preflight_environment", probe)

    @asynccontextmanager
    async def factory():
        async with open_harness_ui_app(_settings(tmp_path / "state"), host_mode="webui") as app:
            opened.append(app)
            yield app

    async with (
        _network_server(create_webui(factory, api_key="key")) as url,
        httpx.AsyncClient(trust_env=False, base_url=url, headers={"Authorization": "Bearer key"}) as client,
    ):
        async with create_task_group() as tasks:

            async def request() -> None:
                await client.post(
                    "/api/environments/preflight",
                    json={"profile_id": "environment-sandbox", "project_path": str(tmp_path)},
                )

            tasks.start_soon(request)
            with fail_after(3):
                await started.wait()
            tasks.cancel_scope.cancel()
        with fail_after(3):
            await ended.wait()
        assert not opened[0]._sandbox_ready_paths


def test_openapi_export_does_not_open_app() -> None:
    def forbidden():
        raise AssertionError("Schema export opened the App")

    document = openapi_document(create_webui(forbidden, api_key="key"))
    assert "/api/setup/apply" in document["paths"]


@pytest.mark.anyio
async def test_webui_key_management_is_authenticated_and_never_returns_secret(
    tmp_path: Path, account_home: None
) -> None:
    @asynccontextmanager
    async def factory():
        async with open_harness_ui_app(_settings(tmp_path / "state"), host_mode="webui") as app:
            yield app

    server = create_webui(factory, api_key="server-access")
    async with (
        server.router.lifespan_context(server),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=server), base_url="http://127.0.0.1") as client,
    ):
        assert (
            await client.put("/api/auth/keys", json={"credential_ref": "key-test", "key": "private-key"})
        ).status_code == 401
        client.headers["Authorization"] = "Bearer server-access"
        saved = await client.put("/api/auth/keys", json={"credential_ref": "key-test", "key": "private-key"})
        assert saved.status_code == 200
        assert saved.json() == {"credential_ref": "key-test"}
        assert (await client.get("/api/auth/keys")).json() == [{"credential_ref": "key-test"}]
        bad = await client.put("/api/auth/keys", json={"credential_ref": "INVALID", "key": "private-key"})
        assert bad.status_code == 400
        assert "private-key" not in bad.text
        assert (await client.delete("/api/auth/keys/key-test")).status_code == 200
        assert (await client.get("/api/auth/keys")).json() == []


@pytest.mark.anyio
async def test_attachment_http_upload_download_submit_and_thread_scope(tmp_path: Path) -> None:
    opened = []

    @asynccontextmanager
    async def factory():
        async with open_harness_ui_app(
            _settings(tmp_path / "state"), configuration_path=_write_configuration(tmp_path), host_mode="webui"
        ) as app:
            app._root_runs._executor._agents = _CompletedReconstructor()
            opened.append(app)
            yield app

    server = create_webui(factory, api_key="test-secret")
    async with (
        server.router.lifespan_context(server),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=server), base_url="http://127.0.0.1") as client,
    ):
        thread = await opened[0].create_thread()
        url = f"/api/threads/{thread.thread_id}/attachments"
        assert (await client.post(url, params={"name": "notes.txt"}, content=b"notes")).status_code == 401
        client.headers["Authorization"] = "Bearer test-secret"
        response = await client.post(url, params={"name": "../notes.txt"}, content=b"notes")
        assert response.status_code == 200, response.text
        attachment = response.json()
        assert attachment["name"] == "notes.txt"
        attachment_id = attachment["attachment_id"]
        response = await client.get(f"{url}/{attachment_id}")
        assert response.status_code == 200 and response.content == b"notes"
        assert response.headers["content-disposition"].startswith("attachment;")
        other = await opened[0].create_thread()
        assert (await client.get(f"/api/threads/{other.thread_id}/attachments/{attachment_id}")).status_code == 400
        response = await client.post(
            f"/api/threads/{thread.thread_id}/submit", json={"attachment_ids": [attachment_id]}
        )
        assert response.status_code == 200, response.text
        await opened[0].wait_root_operation(response.json()["receipt_id"])
        assert (
            tmp_path / "state/threads" / thread.thread_id / "attachments" / attachment_id / "content"
        ).read_bytes() == b"notes"
        assert (
            await client.post(url, params={"name": "too-big"}, content=b"x" * (10 * 1024 * 1024 + 1))
        ).status_code == 413
        assert (await client.post(url, params={"name": "invalid.png"}, content=b"not image")).status_code == 400
