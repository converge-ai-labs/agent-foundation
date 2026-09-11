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
            "project": "project-local",
            "project_path": str(tmp_path),
            "environment_profile": "environment-native",
        }
        preview = await client.post("/api/setup/preview", json=selection)
        assert preview.status_code == 200, preview.text
        assert not path.exists()
        assert "gpt-5.6-luna" in preview.json()["files"]["models/codex-review.yaml"]
        result = await client.post("/api/setup/apply", json={"selection": selection})
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


@pytest.mark.anyio
async def test_probes_runtime_version_and_unimplemented_features(
    tmp_path: Path, account_home: None, monkeypatch
) -> None:
    from importlib.metadata import version

    monkeypatch.setenv("A13N_HARNESS_UI_BUILD_REVISION", "source-revision")
    server = create_webui(
        lambda: open_harness_ui_app(_settings(tmp_path / "state"), host_mode="webui"),
        api_key="probe-secret",
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server), base_url="http://127.0.0.1") as client:
        assert (await client.get("/healthz")).json() == {"status": "ok"}
        assert (await client.get("/readyz")).status_code == 503
        async with server.router.lifespan_context(server):
            # A fresh instance without model configuration can serve setup.
            assert (await client.get("/readyz")).json() == {"status": "ready"}
            assert (await client.get("/api/status")).status_code == 401
            response = await client.get("/api/status", headers={"Authorization": "Bearer probe-secret"})
            assert response.status_code == 200
            status = response.json()
            assert status["version"] == version("a13n-harness-ui")
            assert status["build_revision"] == "source-revision"
            assert status["features"] == {
                "shared_drafts": False,
                "host_files": False,
                "host_git": False,
                "host_terminal": False,
            }
            assert "probe-secret" not in response.text
            assert (await client.get("/readyz", headers={"Host": "evil.example"})).status_code == 400
        assert (await client.get("/readyz")).status_code == 503


@pytest.mark.anyio
async def test_http_restart_keeps_durable_threads_not_listener_keys(tmp_path: Path, account_home: None) -> None:
    settings = _settings(tmp_path / "state")
    configuration = _write_configuration(tmp_path)
    thread_id = None
    for key in ("before-restart", "after-restart"):
        server = create_webui(
            lambda: open_harness_ui_app(settings, configuration_path=configuration, host_mode="webui"),
            api_key=key,
        )
        async with _network_server(server) as url, httpx.AsyncClient(trust_env=False, base_url=url) as client:
            client.headers["Authorization"] = f"Bearer {key}"
            if thread_id is None:
                result = await client.post("/api/threads", json={"title": "Persistent thread"})
                assert result.status_code == 200
                thread_id = result.json()["thread_id"]
            else:
                result = await client.get(f"/api/threads/{thread_id}")
                assert result.status_code == 200
                assert "Persistent thread" in result.text
                assert (
                    await client.get("/api/status", headers={"Authorization": "Bearer before-restart"})
                ).status_code == 401


@pytest.mark.anyio
async def test_configuration_lifecycle_over_real_http(tmp_path: Path, account_home: None) -> None:
    del account_home
    from .test_project_defaults import _project

    root = _write_configuration(tmp_path)

    @asynccontextmanager
    async def factory():
        async with open_harness_ui_app(
            _settings(tmp_path / "state"),
            configuration_path=root,
            host_mode="webui",
        ) as app:
            app._root_runs._executor._agents = _CompletedReconstructor()
            yield app

    async with (
        _network_server(create_webui(factory, api_key="key")) as url,
        httpx.AsyncClient(trust_env=False, base_url=url, headers={"Authorization": "Bearer key"}) as client,
    ):
        endpoint = "/api/configuration/sources/projects/main.yaml"
        blocked = await client.put(endpoint, json={"content": "invalid"}, headers={"Authorization": "Bearer wrong"})
        assert blocked.status_code == 401
        assert (await client.get("/api/configuration/sources")).json()["generation_digest"]
        saved = (await client.get(endpoint)).json()
        assert saved["writable"] and saved["content_available"]
        content = _project(tmp_path / "workspace", {"environment_profile": "environment-native", "mcp_servers": []})
        checked = await client.post(
            "/api/configuration/validate", params={"path": "projects/main.yaml"}, json={"content": content}
        )
        assert checked.status_code == 200, checked.text
        assert (await client.get(endpoint)).json()["source_digest"] == saved["source_digest"]
        written = await client.put(endpoint, json={"content": content})
        assert written.status_code == 200, written.text
        projects = (await client.get("/api/projects")).json()
        assert projects[0]["defaults"] == {"environment_profile": "environment-native", "mcp_servers": []}
        created_preview = (await client.post("/api/threads/preview", json={})).json()
        created = await client.post("/api/threads", json={})
        assert created.status_code == 200, created.text
        thread = created.json()
        thread_id = thread["thread_id"]
        assert thread["configuration"] == created_preview
        changed_content = _project(tmp_path / "workspace", {"environment_profile": "environment-sandbox"})
        assert (await client.put(endpoint, json={"content": changed_content})).status_code == 200
        assert (await client.get(f"/api/threads/{thread_id}")).json()["thread"]["configuration"][
            "environment_profile_id"
        ] == "environment-native"
        preview = (await client.get(f"/api/threads/{thread_id}/project-defaults")).json()
        request = {"expected_version": preview["expected_version"], "defaults_digest": preview["defaults_digest"]}
        applied = await client.post(f"/api/threads/{thread_id}/project-defaults", json=request)
        assert applied.status_code == 200, applied.text
        assert applied.json()["configuration"]["environment_profile_id"] == "environment-sandbox"
        assert (await client.post(f"/api/threads/{thread_id}/project-defaults", json=request)).status_code == 409
        patched = await client.patch(
            f"/api/threads/{thread_id}/configuration",
            json={"expected_version": 2, "patch": {"project_id": None, "environment_profile_id": "environment-native"}},
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["configuration"]["project_id"] is None
        stale = await client.patch(
            f"/api/threads/{thread_id}/configuration",
            json={"expected_version": 2, "patch": {"project_id": "project-main"}},
        )
        assert stale.status_code == 409, stale.text
        assert stale.json()["error"]["code"] == "thread_configuration_conflict"
        assert (await client.get(f"/api/threads/{thread_id}")).json()["thread"]["configuration"] == patched.json()[
            "configuration"
        ]
        duplicate = await client.patch(
            f"/api/threads/{thread_id}/configuration",
            json={"expected_version": 3, "patch": {"mcp_server_ids": ["mcp-one", "mcp-one"]}},
        )
        assert duplicate.status_code == 400
        assert (
            await client.patch(
                f"/api/threads/{thread_id}/configuration", json={"expected_version": 3, "patch": {"agent_id": None}}
            )
        ).status_code == 400
        submitted = await client.post(f"/api/threads/{thread_id}/submit", json={"prompt": "Use the configured thread."})
        assert submitted.status_code == 200, submitted.text
        receipt = submitted.json()["receipt_id"]
        with fail_after(5):
            while (await client.get(f"/api/operations/{receipt}")).json()["status"] in ("preparing", "running"):
                await sleep(0.01)
        # Reads show accepted content; a broken external source can be repaired by replacement.
        (tmp_path / "projects/main.yaml").write_text("broken: [")
        repaired = await client.put(endpoint, json={"content": content})
        assert repaired.status_code == 200, repaired.text
        assert (
            await client.post(
                "/api/configuration/validate", params={"path": "../outside.yaml"}, json={"content": content}
            )
        ).status_code == 400
        assert (await client.delete("/api/configuration/sources/a13n-harness-ui.yaml")).status_code == 400
        unused = "/api/configuration/sources/agents/unused.yaml"
        assert (
            await client.put(
                unused, json={"content": 'schema_version: "1"\nkind: agent\nid: agent-unused\nname: Unused\n'}
            )
        ).status_code == 200
        assert (await client.delete(unused)).status_code == 200
        assert (await client.delete(unused)).status_code == 200
        assert (await client.get(unused)).status_code == 404


@pytest.mark.anyio
async def test_configuration_source_views_omit_mcp_literals(tmp_path: Path, account_home: None) -> None:
    del account_home
    root = _write_configuration(tmp_path)
    target = tmp_path / "mcp/private.json"
    target.parent.mkdir()
    target.write_text(
        json.dumps({"mcpServers": {"private": {"command": "unused", "env": {"TOKEN": "private-literal"}}}})
    )

    @asynccontextmanager
    async def factory():
        async with open_harness_ui_app(
            _settings(tmp_path / "state"), configuration_path=root, host_mode="webui"
        ) as app:
            yield app

    async with (
        _network_server(create_webui(factory, api_key="key")) as url,
        httpx.AsyncClient(trust_env=False, base_url=url, headers={"Authorization": "Bearer key"}) as client,
    ):
        catalog = await client.get("/api/configuration/sources")
        source = await client.get("/api/configuration/sources/mcp/private.json")
        assert source.status_code == 200, source.text
        assert source.json()["content"] is None
        assert source.json()["content_available"] is False
        assert source.json()["writable"] is True
        assert "private-literal" not in catalog.text + source.text
        unknown = await client.get("/api/configuration/sources/auth.json")
        assert unknown.status_code == 404


def test_configuration_command_and_preview_schemas_are_distinct() -> None:
    def forbidden():
        raise AssertionError("Schema export opened the App")

    document = openapi_document(create_webui(forbidden, api_key="key"))
    schemas = document["components"]["schemas"]
    preview_patch = schemas["ProjectDefaultsPreview"]["properties"]["patch"]["$ref"].rsplit("/", 1)[1]
    command_patch = schemas["ThreadConfigurationMutationInput"]["properties"]["patch"]["$ref"].rsplit("/", 1)[1]
    assert preview_patch != command_patch
    assert "agent_source" in schemas[preview_patch]["properties"]
    assert "agent_id" in schemas[command_patch]["properties"]


@pytest.mark.anyio
async def test_observer_sse_bootstrap_then_saved_history_and_restart(tmp_path: Path, account_home: None) -> None:
    del account_home
    from .test_app import _reconstructed

    progressed, release = Event(), Event()
    opened: list[HarnessUiApp] = []
    configuration = _write_configuration(tmp_path)
    settings = _settings(tmp_path / "state")

    class Reconstructor:
        def reconstruct(self, composition, *, subagent_operator, root_capabilities=(), **kwargs):
            async def model(messages, info):
                for index in range(300):
                    yield f"chunk-{index};"
                progressed.set()
                await release.wait()
                yield "finished"

            return _reconstructed(model, root_capabilities)

    @asynccontextmanager
    async def factory():
        async with open_harness_ui_app(settings, configuration_path=configuration, host_mode="webui") as app:
            app._root_runs._executor._agents = Reconstructor()
            opened.append(app)
            yield app

    server = create_webui(factory, api_key=None)
    async with _network_server(server) as url, httpx.AsyncClient(trust_env=False, base_url=url) as client:
        thread = (await client.post("/api/threads", json={"title": "Reconnect"})).json()
        thread_id = thread["thread_id"]
        submitted = await client.post(f"/api/threads/{thread_id}/submit", json={"prompt": "long reply"})
        assert submitted.status_code == 200, submitted.text
        receipt = submitted.json()
        with fail_after(5):
            await progressed.wait()
        app = opened[0]
        with fail_after(5):
            while not any(
                event.payload and "chunk-299" in str(event.payload.get("delta", ""))
                for event in await app._live_hub.snapshot(root_thread_id=thread_id)
            ):
                await sleep(0.01)
        replayed: list[dict] = []
        async with client.stream("GET", f"/api/threads/{thread_id}/events") as response:
            with fail_after(5):
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    frame = json.loads(line[6:])
                    if frame["kind"] == "snapshot":
                        assert frame["resume_cursor"] is None
                        total = frame["snapshot"]["root_stream"]["event_count"]
                        assert total > len(await app._live_hub.snapshot(root_thread_id=thread_id))
                    elif frame["kind"] == "root_stream":
                        assert len(frame["events"]) <= 16
                        replayed.extend(frame["events"])
                    elif frame["kind"] == "ready":
                        cursor = frame["resume_cursor"]
                        break
        assert [item["index"] for item in replayed] == list(range(total))
        assistant_ids = {
            item["payload"]["message_id"]
            for item in replayed
            if item["event_type"] == "TEXT_MESSAGE_START"
            and item["payload"]
            and item["payload"].get("role") == "assistant"
        }
        text = "".join(
            item["payload"].get("delta", "")
            for item in replayed
            if item["event_type"] == "TEXT_MESSAGE_CONTENT"
            and item["payload"]
            and item["payload"].get("message_id") in assistant_ids
        )
        assert text == "".join(f"chunk-{index};" for index in range(300))
        assert (await app.active_root_operation(thread_id)).receipt.receipt_id == receipt["receipt_id"]
        release.set()
        await app.wait_root_operation(receipt["receipt_id"])
        assert thread_id not in app._live_hub._root_streams
        history = (await client.get(f"/api/threads/{thread_id}/transcript")).json()
        assert "chunk-0;" in json.dumps(history) and "finished" in json.dumps(history)
        async with client.stream("GET", f"/api/threads/{thread_id}/events") as response:
            frame = await _first_frame(response)
            assert frame["snapshot"]["root_stream"] is None
            assert frame["resume_cursor"]
    # A new App restores saved history, not old connections, observers, or receipts.
    async with _network_server(server) as url, httpx.AsyncClient(trust_env=False, base_url=url) as client:
        assert "finished" in (await client.get(f"/api/threads/{thread_id}/transcript")).text
        async with client.stream("GET", f"/api/threads/{thread_id}/events", params={"after": cursor}) as response:
            assert (await _first_frame(response))["kind"] == "reset"
        assert (await client.get(f"/api/operations/{receipt['receipt_id']}")).status_code != 200


@pytest.mark.anyio
async def test_conversation_query_adapters_and_strict_steering(tmp_path: Path, account_home: None) -> None:
    del account_home
    server = create_webui(
        lambda: open_harness_ui_app(
            _settings(tmp_path / "state"), configuration_path=_write_configuration(tmp_path), host_mode="webui"
        ),
        api_key=None,
    )
    async with (
        server.router.lifespan_context(server),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=server), base_url="http://127.0.0.1") as client,
    ):
        created = (await client.post("/api/threads", json={"title": "Activity"})).json()
        thread_id = created["thread_id"]
        activity = await client.get("/api/threads/activity")
        assert activity.status_code == 200, activity.text
        assert thread_id in activity.text
        assert (await client.get(f"/api/threads/{thread_id}/tasks")).status_code == 200
        assert (
            await client.get(f"/api/threads/{thread_id}/tasks", params={"expected_continuation_id": "stale"})
        ).status_code == 409
        assert (await client.get(f"/api/threads/{thread_id}/children")).json()["total"] == 0
        wait = await client.get(f"/api/threads/{thread_id}/children/wait", params={"timeout_seconds": 0})
        assert wait.status_code == 200, wait.text
        decisions = await client.get(
            f"/api/threads/{thread_id}/decisions", params={"expected_continuation_id": "stale"}
        )
        assert decisions.status_code == 409, decisions.text
        for endpoint in (
            "/api/operations/receipt-missing/steer",
            f"/api/threads/{thread_id}/children/execution-missing/steer",
        ):
            response = await client.post(endpoint, json={"prompt": "hello", "attachment_ids": ["attachment-1"]})
            assert response.status_code == 400, response.text
        schema = (await client.get("/api/openapi.json")).json()
        assert "FocusReplayFrame" in schema["components"]["schemas"]
        assert "SteerRequest" in schema["components"]["schemas"]


@pytest.mark.anyio
async def test_http_decision_query_response_and_saved_history(tmp_path: Path, account_home: None) -> None:
    del account_home
    from .test_app import _DeferredReconstructor

    opened: list[HarnessUiApp] = []

    @asynccontextmanager
    async def factory():
        async with open_harness_ui_app(
            _settings(tmp_path / "state"), configuration_path=_write_configuration(tmp_path), host_mode="webui"
        ) as app:
            app._root_runs._executor._agents = _DeferredReconstructor()
            opened.append(app)
            yield app

    server = create_webui(factory, api_key=None)
    async with (
        server.router.lifespan_context(server),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=server), base_url="http://127.0.0.1") as client,
    ):
        thread_id = (await client.post("/api/threads", json={})).json()["thread_id"]
        path = f"/api/threads/{thread_id}"
        receipt = (await client.post(f"{path}/submit", json={"prompt": "defer"})).json()
        app = opened[0]
        assert (await app.wait_root_operation(receipt["receipt_id"])).status.value == "suspended"
        detail = (await client.get(path)).json()
        expected = detail["continuation_id"]
        decisions = await client.get(f"{path}/decisions", params={"expected_continuation_id": expected})
        assert decisions.status_code == 200, decisions.text
        request = decisions.json()["requests"][0]
        assert request["kind"] == "external"
        assert "dynamic_action" in decisions.text
        response = await client.post(
            f"{path}/decisions",
            json={
                "expected_continuation_id": expected,
                "responses": [{"kind": "external", "request_id": request["request_id"], "result": "external result"}],
            },
        )
        assert response.status_code == 200, response.text
        completed = await app.wait_root_operation(response.json()["receipt_id"])
        assert completed.status.value == "completed"
        assert (await client.get(f"{path}/decisions")).json() is None
        stale = await client.get(f"{path}/decisions", params={"expected_continuation_id": expected})
        assert stale.status_code == 409, stale.text
        history = await client.get(f"{path}/transcript")
        assert "external result" in history.text


@pytest.mark.anyio
@pytest.mark.parametrize("cancel", [False, True])
async def test_http_child_scope_inspection_and_controls(
    tmp_path: Path, account_home: None, monkeypatch, cancel
) -> None:
    del account_home
    from a13n_harness_ui.model_runtime import HarnessUiModelResolver
    from pydantic_ai.models.function import DeltaToolCall, FunctionModel

    configuration = _write_configuration(tmp_path)
    configuration.write_text(configuration.read_text() + "subagents:\n  include: [explorer]\n")
    started, release = Event(), Event()
    calls = 0
    opened: list[HarnessUiApp] = []

    async def build(self, recipe, authentication):
        async def stream(messages, info):
            nonlocal calls
            if "delegate" not in {tool.name for tool in info.function_tools}:
                yield "Child work in progress."
                started.set()
                await release.wait()
                yield "Child completed."
                return
            calls += 1
            if calls == 1:
                yield {
                    0: DeltaToolCall(
                        name="delegate",
                        json_args=json.dumps({"subagent_name": "explorer", "prompt": "Bounded task"}),
                        tool_call_id="delegate-1",
                    )
                }
            else:
                yield "Parent completed."

        return FunctionModel(stream_function=stream)

    monkeypatch.setattr(HarnessUiModelResolver, "_api_key_model", build)

    @asynccontextmanager
    async def factory():
        async with open_harness_ui_app(
            _settings(tmp_path / "state"), configuration_path=configuration, host_mode="webui"
        ) as app:
            opened.append(app)
            yield app

    server = create_webui(factory, api_key=None)
    async with (
        server.router.lifespan_context(server),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=server), base_url="http://127.0.0.1") as client,
    ):
        thread_id = (await client.post("/api/threads", json={})).json()["thread_id"]
        other_id = (await client.post("/api/threads", json={})).json()["thread_id"]
        receipt = (await client.post(f"/api/threads/{thread_id}/submit", json={"prompt": "Delegate"})).json()
        with fail_after(5):
            await started.wait()
        path = f"/api/threads/{thread_id}/children"
        listed = await client.get(path)
        assert listed.status_code == 200, listed.text
        execution = listed.json()["executions"][0]
        execution_id = execution["execution_id"]
        assert execution["persisted_status"] == "running"
        assert execution["parent_thread_id"] == thread_id
        poll = await client.get(f"{path}/wait", params={"execution_id": execution_id, "timeout_seconds": 0})
        assert poll.status_code == 200, poll.text
        assert poll.json()["executions"][0]["execution_id"] == execution_id
        # Child presentation only publishes closed activities, not partial text.
        assert "Child work in progress." not in listed.text
        for endpoint in ("", "/wait"):
            wrong = await client.get(
                f"/api/threads/{other_id}/children{endpoint}", params={"execution_id": execution_id}
            )
            assert wrong.status_code == 400, wrong.text
            assert wrong.json()["error"]["code"] == "subagent_execution_unavailable"
        wrong_review = await client.get(f"/api/threads/{other_id}/children/{execution_id}/review")
        assert wrong_review.status_code == 400, wrong_review.text
        assert wrong_review.json()["error"]["code"] == "subagent_execution_unavailable"
        for action in ("steer", "cancel"):
            wrong = await client.post(
                f"/api/threads/{other_id}/children/{execution_id}/{action}",
                json={"prompt": "Wrong parent"} if action == "steer" else None,
            )
            assert wrong.status_code == 400, wrong.text
            assert wrong.json()["error"]["code"] == "subagent_execution_unavailable"
        if cancel:
            steering = await client.post(f"{path}/{execution_id}/steer", json={"prompt": "Keep it brief"})
            assert steering.status_code == 200, steering.text
            assert steering.json()["accepted"] is True
            cancellation = await client.post(f"{path}/{execution_id}/cancel")
            assert cancellation.status_code == 200, cancellation.text
            assert cancellation.json()["accepted"] is True
        else:
            release.set()
        terminal = await client.get(f"{path}/wait", params={"execution_id": execution_id, "timeout_seconds": 5})
        assert terminal.status_code == 200, terminal.text
        assert terminal.json()["executions"][0]["persisted_status"] == ("cancelled" if cancel else "succeeded")
        review = await client.get(f"{path}/{execution_id}/review")
        assert review.status_code == 200, review.text
        if not cancel:
            assert "Child completed." in review.text
        root = await opened[0].wait_root_operation(receipt["receipt_id"])
        assert root.status.value == "completed"
