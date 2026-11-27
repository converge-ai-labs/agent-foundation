"""Real App/HTTP guest admission is independent of instance administrative access."""

from contextlib import asynccontextmanager
from uuid import uuid4

import httpx
import pytest
import yaml
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.surfaces import NewThreadDefaults
from a13n_harness_ui.webui import create_webui

from .test_app import _settings, _write_configuration
from .test_coordinators import coordinator_configuration
from .test_sharing import owned_child

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("migrated_databases")]


@asynccontextmanager
async def shared_server(tmp_path, *, skip_auth=False, share_computer=False, configuration=None):
    configuration = configuration or _write_configuration(tmp_path)
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


async def test_webui_send_shortcut_uses_accepted_root_configuration(tmp_path):
    root = tmp_path / "a13n-harness-ui.yaml"
    source_path = f"/api/configuration/sources/{root.name}"
    owner = {"Authorization": "Bearer owner-test-key"}
    async with shared_server(tmp_path) as (_, app, client):
        thread = await app.create_thread(title="Shared composer")
        prefix, guest = await create_link(client, "thread", thread.thread_id, permission="prompt")

        async def shortcuts():
            status = await client.get("/api/status", headers=owner)
            access = await client.get(prefix + "/access", headers=guest)
            assert status.status_code == access.status_code == 200
            return status.json()["app"]["send_shortcut"], access.json()["send_shortcut"]

        assert await shortcuts() == ("enter", "enter")
        original = root.read_text()
        body = {"content": original + "webui: {send_shortcut: mod-enter}\n"}
        denied = await client.put(source_path, headers=guest, json=body)
        assert denied.status_code == 401
        saved = await client.put(source_path, headers=owner, json=body)
        assert saved.status_code == 200, saved.text
        assert yaml.safe_load(root.read_text())["webui"]["send_shortcut"] == "mod-enter"
        assert await shortcuts() == ("mod-enter", "mod-enter")

        rejected = await client.put(
            source_path, headers=owner, json={"content": original + "webui: {send_shortcut: shift-enter}\n"}
        )
        assert rejected.status_code == 400
        assert rejected.json()["error"]["code"] == "settings_invalid"
        assert yaml.safe_load(root.read_text())["webui"]["send_shortcut"] == "mod-enter"
        assert await shortcuts() == ("mod-enter", "mod-enter")


async def test_workbench_shortcuts_follow_accepted_owner_configuration_and_stay_private(tmp_path):
    root = tmp_path / "a13n-harness-ui.yaml"
    source_path = f"/api/configuration/sources/{root.name}"
    owner = {"Authorization": "Bearer " + "owner-test-key"}
    async with shared_server(tmp_path) as (_, app, client):
        thread = await app.create_thread(title="Workbench shortcuts")
        prefix, guest = await create_link(client, "thread", thread.thread_id, permission="prompt")
        original = root.read_text()

        async def shortcuts():
            status = await client.get("/api/status", headers=owner)
            assert status.status_code == 200
            return status.json()["app"]["shortcuts"]

        assert await shortcuts() == {}
        configured = {"search_chats": "both", "terminal": "off", "new_chat": "mod-shift-o"}
        body = {
            "content": original
            + 'webui:\n  shortcuts:\n    search_chats: both\n    terminal: "off"\n    new_chat: mod-shift-o\n'
        }
        saved = await client.put(source_path, headers=owner, json=body)
        assert saved.status_code == 200, saved.text
        assert await shortcuts() == configured
        assert yaml.safe_load(root.read_text())["webui"]["shortcuts"] == configured

        shared = await client.get(prefix + "/access", headers=guest)
        assert shared.status_code == 200
        assert "shortcuts" not in shared.json()
        assert shared.json()["send_shortcut"] == "enter"

        invalid = {"content": original + "webui: {shortcuts: {search_chats: mod-shift-o}}\n"}
        rejected = await client.put(source_path, headers=owner, json=invalid)
        assert rejected.status_code == 400
        assert rejected.json()["error"]["code"] == "settings_invalid"
        assert await shortcuts() == configured


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
        prediction = await client.post(
            prefix + f"/threads/{thread.thread_id}/input-prediction",
            headers=headers,
            json={"continuation_id": "stale"},
        )
        assert prediction.status_code == 403
        assert (await client.get(prefix + f"/threads/{private.thread_id}", headers=headers)).status_code == 401
        for path in (
            "/api/status",
            "/api/update-notices",
            "/api/shares",
            "/api/configuration/sources",
            "/api/host/files",
            "/api/presence",
        ):
            assert (await client.get(path, headers=headers)).status_code == 401
        for method, path in (
            ("POST", "/api/threads/read"),
            ("POST", f"/api/threads/{thread.thread_id}/unread"),
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
        for path in ("/threads/read", f"/threads/{thread.thread_id}/unread"):
            assert (await client.post(prefix + path, headers=headers, json={})).status_code in {404, 405}
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


async def test_shared_worker_listing_and_scope_filters(tmp_path):
    configuration = coordinator_configuration(tmp_path)
    async with shared_server(tmp_path, configuration=configuration) as (_, app, client):
        main = NewThreadDefaults(project_id="project-main")
        lead = await app.create_thread(defaults=main, coordinator=True)
        other = await app.create_thread(defaults=NewThreadDefaults(project_id="project-other"), coordinator=True)
        worker = await app.create_thread(defaults=main, coordinator_thread_id=lead.thread_id, title="Alpha worker")
        sibling = await app.create_thread(defaults=main, coordinator_thread_id=lead.thread_id, title="Beta worker")
        child = await owned_child(app, worker.thread_id)
        chat, chat_headers = await create_link(client, "thread", lead.thread_id, "prompt")
        direct, direct_headers = await create_link(client, "thread", worker.thread_id)
        project, project_headers = await create_link(client, "project", "project-main")

        async def page(path, headers, **params):
            response = await client.get(path + "/threads", params=params, headers=headers)
            assert response.status_code == 200, response.text
            body = response.json()
            return {row["thread_id"] for row in body["threads"]}, body.get("next_cursor")

        assert (await page(chat, chat_headers))[0] == {lead.thread_id}
        for path, headers in ((chat, chat_headers), (project, project_headers)):
            first, cursor = await page(path, headers, coordinator_thread_id=lead.thread_id, limit=1)
            assert len(first) == 1 and cursor
            second, end = await page(path, headers, coordinator_thread_id=lead.thread_id, limit=1, cursor=cursor)
            assert first.isdisjoint(second) and first | second == {worker.thread_id, sibling.thread_id}
            assert end is None
            assert (await page(path, headers, coordinator_thread_id=lead.thread_id, query="alpha"))[0] == {
                worker.thread_id
            }
            outside = await client.get(
                path + "/threads", headers=headers, params={"coordinator_thread_id": other.thread_id}
            )
            assert outside.status_code == 401
            conflicting = await client.get(
                path + "/threads",
                headers=headers,
                params={"coordinator_thread_id": lead.thread_id, "independent_only": True},
            )
            assert conflicting.status_code == 400 and conflicting.json()["error"]["code"] == "thread_page_invalid"

        assert (await page(project, project_headers, independent_only=True))[0] == {lead.thread_id}
        assert (await page(project, project_headers, query="beta"))[0] == {sibling.thread_id}
        assert (await client.get(project + "/access", headers=project_headers)).json()["target_role"] is None
        assert (await client.get(chat + "/access", headers=chat_headers)).json()["target_role"] == "coordinator"
        assert (await client.get(chat + f"/threads/{child.thread_id}", headers=chat_headers)).status_code == 200
        detail = await client.get(direct + f"/threads/{worker.thread_id}", headers=direct_headers)
        assert detail.json()["thread"]["coordinator_thread_id"] is None
        assert (await page(direct, direct_headers, coordinator_thread_id=worker.thread_id))[0] == set()
        assert (
            await client.get(
                direct + "/threads", headers=direct_headers, params={"coordinator_thread_id": lead.thread_id}
            )
        ).status_code == 401
        for thread_id in (lead.thread_id, sibling.thread_id):
            assert (await client.get(direct + f"/threads/{thread_id}", headers=direct_headers)).status_code == 401
        assert (
            await client.post(chat + "/threads", headers=chat_headers, json={"thread_id": f"thread_{uuid4().hex}"})
        ).status_code == 403
        assert (await client.get(f"/api/threads/{worker.thread_id}", headers=chat_headers)).status_code == 401


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

    configuration = coordinator_configuration(tmp_path)
    async with listener_with_app(tmp_path, configuration_path=configuration) as (http, ws, app):
        lead = await app.create_thread(coordinator=True)
        thread = await app.create_thread(coordinator_thread_id=lead.thread_id)
        child = await owned_child(app, thread.thread_id)
        private = await app.create_thread()
        created = await app.sharing.create(ShareCreate(scope="thread", target_id=lead.thread_id))
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
            await socket.send(
                json.dumps(
                    {
                        "kind": "subscribe",
                        "channel": "lead",
                        "stream": "focus",
                        "root_thread_id": lead.thread_id,
                    }
                )
            )
            lead_frame = (await frame_until(socket, lambda f: f.get("channel") == "lead"))["frame"]
            assert lead_frame["snapshot"]["thread"]["thread"]["unarchived_worker_count"] == 1
            # Child execution output rides on its root's stream. A child itself
            # passes sharing admission but is not a root focus target.
            await socket.send(
                json.dumps(
                    {
                        "kind": "subscribe",
                        "channel": "child",
                        "stream": "focus",
                        "root_thread_id": child.thread_id,
                    }
                )
            )
            child_frame = (await frame_until(socket, lambda f: f.get("channel") == "child"))["frame"]
            assert child_frame["reason"] == "child_thread_scoped"
            assert opened["frame"]["snapshot"]["thread"]["thread"]["coordinator_thread_id"] == lead.thread_id
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

    configuration = coordinator_configuration(tmp_path)
    async with listener_with_app(tmp_path, configuration_path=configuration) as (http, ws, app):
        lead = await app.create_thread(coordinator=True)
        thread = await app.create_thread(coordinator_thread_id=lead.thread_id)
        created = await app.sharing.create(ShareCreate(scope="thread", target_id=lead.thread_id, permission="prompt"))
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


async def test_bot_cannot_be_shared_and_existing_bot_grants_cannot_read_history(tmp_path):
    from a13n_harness_ui.bot_models import BOT_THREAD_ID
    from a13n_harness_ui.errors import HarnessUiError
    from a13n_harness_ui.share_models import ShareCreate

    async with shared_server(tmp_path) as (_, app, client):
        await app._threads.bot_thread()
        command = ShareCreate(scope="thread", target_id=BOT_THREAD_ID)
        with pytest.raises(HarnessUiError) as error:
            await app.sharing.create(command)
        assert error.value.code == "share_unavailable"
        # A grant persisted by an older build must lose access too.
        created = await app.sharing._repository.create(command)
        prefix = f"/api/shared/{created.grant.share_id}"
        headers = {"Authorization": f"Bearer {created.key}"}
        for path in ("/access", f"/threads/{BOT_THREAD_ID}", f"/threads/{BOT_THREAD_ID}/transcript"):
            response = await client.get(prefix + path, headers=headers)
            assert response.status_code == 401, response.text
        project = await app.sharing.create(ShareCreate(scope="project", target_id="project-main"))
        with pytest.raises(HarnessUiError) as error:
            await app.sharing.thread(project.grant.share_id, BOT_THREAD_ID)
        assert error.value.code == "share_unavailable"


@pytest.mark.parametrize("guest", [True, False])
async def test_acp_fallback_cancels_pending_permission_only_for_owner(tmp_path, guest):
    import json

    from a13n_harness_ui.errors import HarnessUiError
    from a13n_harness_ui.mcp_runtime.inputs import McpInputResponse
    from a13n_harness_ui.share_models import ShareCreate
    from a13n_harness_ui.surfaces import RootOperationStatus
    from anyio import fail_after, sleep

    from .test_acp_execution import _ui_peer_config

    journal = tmp_path / "wire.jsonl"
    configuration = _ui_peer_config(tmp_path, "no-steering", f"steer-journal={journal}")
    async with shared_server(tmp_path, configuration=configuration) as (_, app, client):
        thread_id = (await app.create_thread()).thread_id
        share = await app.sharing.create(ShareCreate(scope="thread", target_id=thread_id, permission="prompt"))
        receipt = await app.submit_thread(thread_id=thread_id, prompt="wait")
        with fail_after(15):
            while not (requests := await app.mcp_input_requests(thread_id)):
                await sleep(0.01)
            (request,) = requests
            answer = McpInputResponse(action="accept", content={"option": "allow"})
            if guest:
                prefix = f"/api/shared/{share.grant.share_id}"
                response = await client.post(
                    f"{prefix}/operations/{receipt.receipt_id}/steer",
                    json={"parts": ["Steering follow-up"]},
                    headers={"Authorization": f"Bearer {share.key}"},
                )
                assert response.status_code == 200 and response.json()["accepted"], response.text
                assert (await app.mcp_input_requests(thread_id)) == (request,)
                await app.respond_mcp_input(thread_id, request.request_id, answer)
            else:
                offer = await app.steer_root_operation(receipt_id=receipt.receipt_id, message="Steering follow-up")
                assert offer.accepted
            outcome = await app.wait_root_operation(receipt.receipt_id)
        assert outcome.status is RootOperationStatus.completed, outcome.failure
        if not guest:
            with pytest.raises(HarnessUiError):
                await app.respond_mcp_input(thread_id, request.request_id, answer)
        frames = [json.loads(line) for line in journal.read_text().splitlines()]
        prompts = [frame for frame in frames if frame["kind"] == "session/prompt"]
        assert len(prompts) == 2 and "Steering follow-up" in str(prompts[-1])
        assert sum(frame["kind"] == "session/cancel" for frame in frames) == (0 if guest else 1)


async def test_prompt_guest_can_read_and_receive_a_prediction_only_in_the_shared_thread(tmp_path, monkeypatch):
    import asyncio
    import json

    from a13n_harness_ui.model_runtime import HarnessUiModelResolver
    from a13n_harness_ui.share_models import ShareCreate
    from a13n_harness_ui.shared_drafts import DraftFrame
    from a13n_harness_ui.surfaces import RootOperationStatus
    from pydantic_ai.messages import ModelResponse, TextPart
    from pydantic_ai.models.function import FunctionModel
    from websockets.asyncio.client import connect

    from .test_interactive_protocol import frame_until, listener_with_app

    configuration = _write_configuration(tmp_path)
    with configuration.open("a") as config:
        config.write("auto_chat_title: false\ninput_prediction: {enabled: true}\n")

    async def stream(_messages, _info):
        yield "Here is a first draft."

    async def run_model(self, context, model_id):
        return FunctionModel(stream_function=stream)

    release = asyncio.Event()

    async def prediction_model(self, model_id, *, thread_id, prediction=False, prediction_account=None):
        async def sample(_messages, _info):
            await release.wait()
            return ModelResponse(parts=[TextPart("Add an example.")])

        return FunctionModel(function=sample)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", run_model)
    monkeypatch.setattr(HarnessUiModelResolver, "resolve", prediction_model)
    async with listener_with_app(tmp_path, configuration_path=configuration) as (http, ws, app):
        thread_id = (await app.create_thread()).thread_id
        other = (await app.create_thread()).thread_id
        receipt = await app.submit_thread(thread_id=thread_id, prompt="Draft a note.")
        assert (await app.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.completed
        continuation_id = (await app.get_thread(thread_id)).continuation_id
        assert continuation_id is not None
        share = await app.sharing.create(ShareCreate(scope="thread", target_id=thread_id, permission="prompt"))
        prefix = f"/api/shared/{share.grant.share_id}"
        path = f"{prefix}/threads/{thread_id}"
        request = {"continuation_id": continuation_id}
        async with (
            httpx.AsyncClient(base_url=http, headers={"Authorization": f"Bearer {share.key}"}) as client,
            connect(ws + path + "/draft/connect", proxy=None, origin=http) as guest,
        ):
            access = await client.get(prefix + "/access")
            assert access.status_code == 200, access.text
            assert access.json()["input_prediction_enabled"] is True
            await guest.send(json.dumps({"api_key": share.key}))
            initial = await frame_until(guest, lambda frame: frame.get("kind") == "draft")
            assert DraftFrame.model_validate(initial).prediction is None
            response = await client.post(path + "/input-prediction", json=request)
            assert response.status_code == 200, response.text
            assert response.json() == {**request, "text": None}
            release.set()
            expected = {**request, "text": "Add an example."}
            frame = DraftFrame.model_validate(await frame_until(guest, lambda frame: frame.get("prediction")))
            assert frame.prediction is not None and frame.prediction.model_dump() == expected
            response = await client.post(path + "/input-prediction", json=request)
            assert response.status_code == 200 and response.json() == expected
            denied = await client.post(f"{prefix}/threads/{other}/input-prediction", json=request)
            assert denied.status_code == 401, denied.text


async def test_guest_can_read_model_display_and_usage_only_within_scope(tmp_path):
    configuration = coordinator_configuration(tmp_path)
    model_file = tmp_path / "models" / "primary.yaml"
    model_doc = yaml.safe_load(model_file.read_text(encoding="utf-8"))
    model_doc["name"] = "GPT-5.4"
    model_doc["route"] = "openai:gpt-5.4"
    model_doc["settings"] = {
        "openai_reasoning_effort": "high",
        "openai_service_tier": "priority",
    }
    model_file.write_text(yaml.safe_dump(model_doc, sort_keys=False), encoding="utf-8")
    async with shared_server(tmp_path, configuration=configuration) as (_, app, client):
        main = NewThreadDefaults(project_id="project-main")
        lead = await app.create_thread(defaults=main, coordinator=True, title="Release lead")
        worker = await app.create_thread(defaults=main, coordinator_thread_id=lead.thread_id, title="QA worker")
        private = await app.create_thread(defaults=NewThreadDefaults(project_id="project-other"), title="Private")
        prefix, headers = await create_link(client, "thread", lead.thread_id, permission="view")

        lead_detail = await client.get(f"{prefix}/threads/{lead.thread_id}", headers=headers)
        assert lead_detail.status_code == 200, lead_detail.text
        display = lead_detail.json()["model_display"]
        assert display == {
            "agent_name": "Assistant",
            "model_name": "GPT-5.4",
            "model_identity": "openai:gpt-5.4",
            "thinking_summary": "High",
            "fast": "on",
        }

        for thread_id in (lead.thread_id, worker.thread_id):
            usage = await client.get(f"{prefix}/threads/{thread_id}/usage", headers=headers)
            assert usage.status_code == 200, usage.text
            assert "combined" in usage.json() and "root" in usage.json()
            context = await client.get(f"{prefix}/threads/{thread_id}/context-usage", headers=headers)
            assert context.status_code == 200, context.text

        assert (await client.get(f"{prefix}/threads/{private.thread_id}/usage", headers=headers)).status_code == 401
        assert (
            await client.get(f"{prefix}/threads/{lead.thread_id}/configuration", headers=headers)
        ).status_code == 404
        assert (await client.get(f"{prefix}/selectors", headers=headers)).status_code == 404
