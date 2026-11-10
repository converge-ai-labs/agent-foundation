"""Real App/HTTP guest admission is independent of instance administrative access."""

from contextlib import asynccontextmanager
from uuid import uuid4

import httpx
import pytest
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.webui import create_webui

from .test_app import _settings, _write_configuration

pytestmark = pytest.mark.anyio


@asynccontextmanager
async def shared_server(tmp_path, *, skip_auth=False, share_computer=False):
    configuration = _write_configuration(tmp_path)
    owner = None

    @asynccontextmanager
    async def factory():
        nonlocal owner
        async with open_harness_ui_app(
            _settings(tmp_path / "data").model_copy(update={"pricing_auto_update": False}),
            configuration_path=configuration,
            host_mode="webui",
            share_computer=share_computer,
            instrumentation=None,
        ) as app:
            owner = app
            yield app

    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "index.html").write_text("<html>Guest shell</html>")
    server = create_webui(factory, api_key=None if skip_auth else "owner-test-key", static_root=assets)
    async with server.router.lifespan_context(server):
        assert owner is not None
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server), base_url="http://localhost") as client:
            yield server, owner, client


async def create_link(client, scope, target_id, permission="view"):
    response = await client.post(
        "/api/shares",
        headers={"Authorization": "Bearer owner-test-key"},
        json={"scope": scope, "target_id": target_id, "permission": permission},
    )
    assert response.status_code == 200, response.text
    created = response.json()
    return f"/api/shared/{created['grant']['share_id']}", {"Authorization": f"Bearer {created['key']}"}


async def test_guest_cannot_read_other_threads_or_call_owner_apis(tmp_path):
    async with shared_server(tmp_path) as (_, app, client):
        thread = await app.create_thread(title="Shared design")
        private = await app.create_thread(title="Private design")
        prefix, headers = await create_link(client, "thread", thread.thread_id)
        response = await client.get(prefix + "/access", headers=headers)
        assert response.status_code == 200 and response.json()["title"] == "Shared design"
        assert (await client.get(prefix + "/access")).status_code == 401
        assert (
            await client.get(prefix + "/access", headers={"Authorization": "Bearer owner-test-key"})
        ).status_code == 401
        response = await client.get(prefix + f"/threads/{thread.thread_id}", headers=headers)
        assert response.status_code == 200
        assert "run" not in response.json()["available_actions"]
        assert (await client.get(prefix + f"/threads/{private.thread_id}", headers=headers)).status_code == 401
        for path in ("/api/status", "/api/shares", "/api/configuration/sources", "/api/host/files", "/api/presence"):
            assert (await client.get(path, headers=headers)).status_code == 401
        for method, path in (
            ("GET", "/api/browser"),
            ("POST", "/api/browser/prepare"),
            ("POST", "/api/browser/scan"),
            ("POST", "/api/browser/check"),
            ("POST", "/api/browser/macos/settings"),
            ("GET", f"/api/threads/{thread.thread_id}/browser"),
            ("POST", f"/api/threads/{thread.thread_id}/browser/access"),
            ("GET", f"/api/threads/{thread.thread_id}/browser/setup"),
            *(
                ("POST", f"/api/threads/{thread.thread_id}/browser/{action}")
                for action in ("start", "reset", "close", "take-control", "handback", "viewport")
            ),
        ):
            assert (await client.request(method, path, headers=headers)).status_code == 401
        for path in (
            "/configuration/sources",
            "/host/files",
            "/selectors",
            "/status",
            f"/threads/{thread.thread_id}/browser",
        ):
            response = await client.get(prefix + path, headers=headers)
            assert response.status_code == 404
        response = await client.post(
            prefix + f"/threads/{thread.thread_id}/submit", headers=headers, json={"parts": ["Hello"]}
        )
        assert response.status_code == 403
        assert (
            await client.get(prefix + "/access", headers={**headers, "Origin": "https://other.example"})
        ).status_code == 403


async def test_project_guest_creation_scope_and_permission_changes(tmp_path):
    async with shared_server(tmp_path) as (_, app, client):
        prefix, headers = await create_link(client, "project", "project-main", "prompt")
        response = await client.post(
            prefix + "/threads", headers=headers, json={"thread_id": f"thread_{uuid4().hex}", "title": "Guest idea"}
        )
        assert response.status_code == 200, response.text
        thread_id = response.json()["thread_id"]
        assert response.json()["configuration"]["project_id"] == "project-main"
        future = await app.create_thread(title="Later owner idea")
        response = await client.get(prefix + "/threads?project_id=project-other", headers=headers)
        assert {item["thread_id"] for item in response.json()["threads"]} == {thread_id, future.thread_id}
        response = await client.post(
            prefix + "/threads",
            headers=headers,
            json={"thread_id": f"thread_{uuid4().hex}", "defaults": {"project_id": None}},
        )
        assert response.status_code in {400, 422}
        response = await client.post(
            prefix + f"/threads/{thread_id}/submit",
            headers=headers,
            json={"parts": ["Hello"], "model_id": "model-other"},
        )
        assert response.status_code in {400, 422}
        share_id = prefix.rsplit("/", 1)[1]
        response = await client.patch(
            f"/api/shares/{share_id}", headers={"Authorization": "Bearer owner-test-key"}, json={"permission": "view"}
        )
        assert response.status_code == 200
        assert (
            await client.post(prefix + "/threads", headers=headers, json={"thread_id": f"thread_{uuid4().hex}"})
        ).status_code == 403
        assert (
            await client.delete(f"/api/shares/{share_id}", headers={"Authorization": "Bearer owner-test-key"})
        ).status_code == 204
        assert (await client.get(prefix + "/access", headers=headers)).status_code == 401


async def test_dangerous_owner_bypass_does_not_bypass_guest_authentication(tmp_path):
    async with shared_server(tmp_path, skip_auth=True) as (_, app, client):
        thread = await app.create_thread()
        prefix, headers = await create_link(client, "thread", thread.thread_id)
        assert (await client.get(prefix + "/access")).status_code == 401
        assert (await client.get(prefix + "/access", headers=headers)).status_code == 200


async def test_guest_realtime_scope_and_revocation_disconnect(tmp_path):
    import json

    from a13n_harness_ui.share_models import ShareCreate
    from anyio import fail_after
    from websockets.asyncio.client import connect
    from websockets.exceptions import ConnectionClosed

    from .test_interactive_protocol import frame_until, listener_with_app

    configuration = _write_configuration(tmp_path)
    async with listener_with_app(tmp_path, configuration_path=configuration) as (http, ws, app):
        thread = await app.create_thread()
        private = await app.create_thread()
        created = await app.sharing.create(ShareCreate(scope="thread", target_id=thread.thread_id))
        path = f"/api/shared/{created.grant.share_id}"
        async with connect(ws + path + "/realtime/connect", proxy=None, origin=http) as socket:
            await socket.send(json.dumps({"api_key": created.key}))
            await socket.send(json.dumps({"kind": "subscribe", "channel": "global"}))
            denied = await frame_until(socket, lambda f: f.get("channel") == "global")
            assert denied["frame"]["reason"] == "share_forbidden"
            await socket.send(
                json.dumps(
                    {"kind": "subscribe", "channel": "private", "stream": "focus", "root_thread_id": private.thread_id}
                )
            )
            denied = await frame_until(socket, lambda f: f.get("channel") == "private")
            assert denied["frame"]["reason"] == "share_unavailable"
            await socket.send(
                json.dumps(
                    {"kind": "subscribe", "channel": "shared", "stream": "focus", "root_thread_id": thread.thread_id}
                )
            )
            opened = await frame_until(socket, lambda f: f.get("channel") == "shared")
            assert "run" not in opened["frame"]["snapshot"]["thread"]["available_actions"]
            await app.sharing.revoke(created.grant.share_id)
            with fail_after(3), pytest.raises(ConnectionClosed) as failure:
                while True:
                    await socket.recv()
            assert failure.value.rcvd.code == 4401


async def test_guest_draft_permission_downgrade_and_presence_scope(tmp_path):
    import json

    from a13n_harness_ui.share_models import ShareCreate
    from anyio import fail_after
    from websockets.asyncio.client import connect
    from websockets.exceptions import ConnectionClosed

    from .test_interactive_protocol import frame_until, listener_with_app
    from .test_presence_protocol import report

    configuration = _write_configuration(tmp_path)
    async with listener_with_app(tmp_path, configuration_path=configuration) as (http, ws, app):
        thread = await app.create_thread()
        created = await app.sharing.create(ShareCreate(scope="thread", target_id=thread.thread_id, permission="prompt"))
        path = f"/api/shared/{created.grant.share_id}"
        async with (
            connect(ws + "/api/presence/connect", proxy=None, origin=http) as owner,
            connect(ws + path + "/presence/connect", proxy=None, origin=http) as guest,
        ):
            await owner.send(json.dumps({"api_key": "test-only-key"}))
            owner_id = (await frame_until(owner, lambda f: f.get("kind") == "presence"))["participant_id"]
            await owner.send(json.dumps(report({"kind": "workbench", "section": "settings"})))
            await guest.send(json.dumps({"api_key": created.key}))
            initial = await frame_until(guest, lambda f: f.get("kind") == "presence")
            assert owner_id not in {p["participant_id"] for p in initial["participants"]}
            target = {"kind": "conversation", "thread_id": thread.thread_id}
            await guest.send(json.dumps(report(target)))
            await owner.send(json.dumps(report(target)))
            same = await frame_until(guest, lambda f: owner_id in f.get("same_page_participant_ids", []))
            assert len(same["participants"]) == 2
            await guest.send(json.dumps(report({"kind": "workbench", "section": "settings"})))
            denied = await frame_until(guest, lambda f: "error" in f)
            assert denied["error"]["code"] == "share_forbidden"
        async with connect(ws + path + f"/threads/{thread.thread_id}/draft/connect", proxy=None, origin=http) as draft:
            await draft.send(json.dumps({"api_key": created.key}))
            await frame_until(draft, lambda f: f.get("kind") == "draft")
            await app.sharing.update(created.grant.share_id, "view")
            with fail_after(3), pytest.raises(ConnectionClosed) as failure:
                while True:
                    await draft.recv()
            assert failure.value.rcvd.code == 4403
        async with connect(ws + path + f"/threads/{thread.thread_id}/draft/connect", proxy=None, origin=http) as draft:
            await draft.send(json.dumps({"api_key": created.key}))
            denied = await frame_until(draft, lambda f: "error" in f)
            assert denied["error"]["code"] == "share_forbidden"


async def test_shared_browser_routes_serve_the_shell_without_instance_auth(tmp_path):
    async with shared_server(tmp_path) as (_, _, client):
        for path in ("/share/share-example", "/share/share-example/threads/thread-example"):
            response = await client.get(path)
            assert response.status_code == 200
            assert "Guest shell" in response.text
        assert (await client.get("/share/share-example/settings")).status_code == 404


@pytest.mark.parametrize("sharing", [False, True])
async def test_browser_runtime_status_and_explicit_retry(tmp_path, monkeypatch, sharing):
    from unittest.mock import AsyncMock, Mock

    from a13n_harness.capabilities.browser.cdp import Chromium
    from a13n_harness_ui.browser import HostBrowsers

    launch = AsyncMock(side_effect=RuntimeError("Missing display"))
    monkeypatch.setattr(HostBrowsers, "launch", launch)
    async with shared_server(tmp_path, share_computer=sharing) as (_, app, client):
        headers = {"Authorization": "Bearer owner-test-key"}
        assert (await client.get("/api/browser")).status_code == 401
        assert (await client.post("/api/browser/prepare")).status_code == 401
        if sharing:
            await app.browsers().prepare()
        calls = launch.await_count
        response = await client.get("/api/browser", headers=headers)
        assert response.status_code == 200
        state = response.json()
        assert state["status"] == ("failed" if sharing else "disabled")
        assert state["available"] is sharing
        assert state["configuration"]["mode"] == "auto"
        assert launch.await_count == calls
        if not sharing:
            assert "sharing" in state["error"]
            assert (await client.post("/api/browser/prepare", headers=headers)).status_code == 403
            launch.assert_not_awaited()
            return
        assert state["error"] == "Missing display"
        runtime = Chromium(Mock())
        runtime.close = AsyncMock()

        async def recover(host):
            host.runtime = runtime
            host.runtime_failed = False

        monkeypatch.setattr(HostBrowsers, "launch", recover)
        response = await client.post("/api/browser/prepare", headers=headers)
        assert response.status_code == 200
        assert response.json()["status"] == "ready"
        assert response.json()["error"] is None
        assert not app.browsers().bindings and not runtime.pages


async def test_guest_submission_cannot_replay_owner_options_capture(tmp_path):
    import base64

    from a13n_harness_ui.shared_drafts import DraftCommand, composer_document, composer_options, options_document
    from pycrdt import Map, Text

    async with shared_server(tmp_path) as (_, app, client):
        thread = await app.create_thread()
        prefix, headers = await create_link(client, "thread", thread.thread_id, "prompt")
        draft = await app.shared_draft(thread.thread_id)
        participant = draft.attach()
        authored, options = composer_document(), options_document()
        authored.get("text", type=Text).insert(0, "Owner input")
        options.get("options", type=Map)["mode"] = "goal"
        content = base64.b64encode(authored.get_update()).decode()
        selected = base64.b64encode(options.get_update()).decode()
        await app.edit_shared_draft(
            thread.thread_id,
            participant,
            DraftCommand(
                kind="sync",
                draft_id=draft.draft_id,
                update_base64=content,
                options_update_base64=selected,
            ),
        )
        capture = {"draft_id": draft.draft_id, "update_base64": content, "options_update_base64": selected}
        response = await client.post(
            prefix + f"/threads/{thread.thread_id}/submit",
            headers=headers,
            json={"parts": ["Guest input"], "draft": capture},
        )
        assert response.status_code == 403
        assert composer_options(draft.options).mode == "goal"
        assert str(draft.document["text"]) == "Owner input"
