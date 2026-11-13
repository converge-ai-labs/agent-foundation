"""Small real protocol clients prove shared editing before browser implementation."""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Any

import httpx
import pytest
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from a13n_harness_ui.shared_drafts import composer_document, composer_values
from anyio import Event, fail_after, sleep
from pycrdt import Doc, Map, Text
from pydantic_ai.models.function import FunctionModel
from websockets.asyncio.client import ClientConnection, connect

from .test_app import _write_configuration
from .test_host_git import git, repository
from .test_interactive_protocol import frame_until, listener, listener_with_app
from .test_shared_drafts import replica, sync

pytestmark = pytest.mark.anyio


async def join(connection: ClientConnection) -> dict[str, Any]:
    await connection.send('{"api_key":"test-only-key"}')
    return await frame_until(connection, lambda frame: frame.get("kind") == "draft")


def document(frame: dict[str, Any]) -> Doc:
    result = composer_document()
    result.apply_update(base64.b64decode(frame["update_base64"]))
    return result


async def send(connection: ClientConnection, draft_id: str, doc: Doc) -> None:
    await connection.send(
        json.dumps({"kind": "sync", "draft_id": draft_id, "update_base64": base64.b64encode(doc.get_update()).decode()})
    )


async def containing(connection: ClientConnection, text: str) -> dict[str, Any]:
    return await frame_until(connection, lambda f: f.get("kind") == "draft" and text in str(document(f)["text"]))


async def test_bot_draft_authentication_precedes_lazy_room_and_rejects_scoped_guests(tmp_path: Path) -> None:
    from a13n_harness_ui.share_models import ShareCreate
    from websockets.exceptions import ConnectionClosed

    async with listener_with_app(tmp_path, configuration_path=_write_configuration(tmp_path)) as (http, ws, app):
        async with connect(ws + "/api/threads/thread_bot/draft/connect", proxy=None, origin=http) as connection:
            await connection.send('{"api_key":"wrong"}')
            with pytest.raises(ConnectionClosed) as failure:
                await connection.recv()
            assert failure.value.rcvd.code == 4401
        assert "thread_bot" not in app._shared_drafts
        thread = await app.create_thread()
        created = await app.sharing.create(ShareCreate(scope="thread", target_id=thread.thread_id, permission="prompt"))
        path = f"/api/shared/{created.grant.share_id}/threads/thread_bot/draft/connect"
        async with connect(ws + path, proxy=None, origin=http) as connection:
            await connection.send(json.dumps({"api_key": created.key}))
            denied = await frame_until(connection, lambda frame: "error" in frame)
            assert denied["error"]["code"] == "share_unavailable"
        assert "thread_bot" not in app._shared_drafts


async def test_bot_shared_composer_capture_steer_rejection_and_peer_edits(tmp_path: Path, monkeypatch) -> None:
    from a13n_harness_ui.surfaces import RootControlResult

    from .test_app import _SlowReconstructor
    from .test_bot import _write_configuration as bot_configuration
    from .test_bot import ready

    async with listener_with_app(tmp_path, configuration_path=bot_configuration(tmp_path)) as (http, ws, app):
        await ready(app)
        started = Event()
        app._root_runs._executor._agents = _SlowReconstructor(started)
        path = ws + "/api/threads/thread_bot/draft/connect"
        async with (
            httpx.AsyncClient(base_url=http, headers={"Authorization": "Bearer test-only-key"}) as api,
            connect(path, proxy=None, origin=http) as first,
            connect(path, proxy=None, origin=http) as second,
        ):
            a, b = await join(first), await join(second)
            assert a["draft_id"] == b["draft_id"]
            assert (await app.get_thread("thread_bot")).thread.last_execution is None
            authored = document(a)
            authored.get("text", type=Text).insert(0, "Keep working")
            await send(first, a["draft_id"], authored)
            captured = await containing(second, "Keep working")
            peer = document(captured)
            peer.get("text", type=Text).insert(0, "NEXT ")
            await send(second, a["draft_id"], peer)
            await containing(first, "NEXT ")
            command = {
                "input_id": "shared-first",
                "text": "Keep working",
                "draft": {
                    "draft_id": a["draft_id"],
                    "update_base64": captured["update_base64"],
                },
            }
            response = await api.post("/api/bot/inputs", json=command)
            assert response.status_code == 200, response.text
            receipt = response.json()["receipt_id"]
            with fail_after(5):
                await started.wait()
            cleared = await frame_until(
                first, lambda f: f.get("kind") == "draft" and str(document(f)["text"]) == "NEXT "
            )
            # Exact retry consumes the same old identities, never the peer's next input.
            assert (await api.post("/api/bot/inputs", json=command)).status_code == 200
            steered = await api.post(
                "/api/bot/inputs",
                json={
                    "input_id": "shared-steer",
                    "text": "NEXT ",
                    "draft": {"draft_id": a["draft_id"], "update_base64": cleared["update_base64"]},
                },
            )
            assert steered.status_code == 200, steered.text
            assert steered.json()["receipt_id"] == receipt
            empty = await frame_until(second, lambda f: f.get("kind") == "draft" and not str(document(f)["text"]))
            rejected_doc = document(empty)
            rejected_doc.get("text", type=Text).insert(0, "Retain rejected input")
            await send(second, a["draft_id"], rejected_doc)
            retained = await containing(first, "Retain rejected input")

            async def reject(**kwargs):
                return RootControlResult(receipt_id=receipt, accepted=False)

            monkeypatch.setattr(app._root_runs, "steer", reject)
            rejected = await api.post(
                "/api/bot/inputs",
                json={
                    "input_id": "shared-rejected",
                    "text": "Retain rejected input",
                    "draft": {"draft_id": a["draft_id"], "update_base64": retained["update_base64"]},
                },
            )
            assert rejected.status_code == 400, rejected.text
            assert rejected.json()["error"]["code"] == "bot_input_rejected"
            assert str(app._shared_drafts["thread_bot"].document["text"]) == "Retain rejected input"
            await app.cancel_root_operation(receipt)
        async with connect(path, proxy=None, origin=http) as rejoined:
            restored = await join(rejoined)
            assert restored["draft_id"] == a["draft_id"]
            assert str(document(restored)["text"]) == "Retain rejected input"


async def test_corrupt_saved_composer_reports_a_sanitized_protocol_error(tmp_path: Path) -> None:
    from dataclasses import replace

    async with listener_with_app(tmp_path, configuration_path=_write_configuration(tmp_path)) as (_, ws, app):
        thread = await app.create_thread()
        async with app.shared_draft_editor(thread.thread_id) as (draft, participant):
            authored = composer_document()
            authored.get("text", type=Text).insert(0, "saved input")
            await app.edit_shared_draft(thread.thread_id, participant, sync(draft, authored))
        stored = await app._store.drafts.get(thread.thread_id)
        assert stored is not None
        broken = replace(stored, document_update=b"bad")
        await app._store.drafts.save(broken)
        app._shared_drafts.pop(thread.thread_id)
        async with connect(ws + f"/api/threads/{thread.thread_id}/draft/connect") as connection:
            await connection.send('{"api_key":"test-only-key"}')
            error = await frame_until(connection, lambda frame: "error" in frame)
            assert error["error"]["code"] == "draft_restore_failed"
            assert "stored input was preserved" in error["error"]["message"]
            assert "Traceback" not in json.dumps(error)
            await connection.wait_closed()
            assert connection.close_code == 4404
        assert await app._store.drafts.get(thread.thread_id) == broken


async def test_real_shared_composer_submit_capture_steer_rejoin_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _write_configuration(tmp_path)
    started, release = Event(), Event()
    inputs = []

    async def model(messages, info):
        inputs.extend(messages)
        started.set()
        await release.wait()
        yield "completed protocol input"

    async def resolve(self, context, model_id):
        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    source = tmp_path / "context.txt"
    source.write_text("reviewed bytes")
    checkout = repository(tmp_path / "repo")
    (checkout / "selected").write_text("reviewed patch bytes\n")
    git(checkout, "add", "selected")
    headers = {"Authorization": "Bearer test-only-key"}
    async with listener(tmp_path, configuration_path=root) as (http, ws):
        async with httpx.AsyncClient(base_url=http, headers=headers, trust_env=False) as api:
            created = await api.post("/api/threads", json={})
            assert created.status_code == 200, created.text
            thread_id = created.json()["thread_id"]
            prefix = f"/api/threads/{thread_id}"
            other = (await api.post("/api/threads", json={})).json()["thread_id"]
            metadata = (await api.get("/api/host/files/metadata", params={"path": str(source)})).json()
            capture = await api.post(
                prefix + "/host-file-captures", json={"path": str(source), "expected_revision": metadata["revision"]}
            )
            assert capture.status_code == 200, capture.text
            attachment = capture.json()["attachment"]["attachment_id"]
            inspected = await api.get(prefix + f"/attachments/{attachment}/metadata")
            assert inspected.status_code == 200, inspected.text
            assert inspected.json() == capture.json()["attachment"]
            assert inspected.json()["source"]["revision"] == metadata["revision"]
            foreign_metadata = await api.get(f"/api/threads/{other}/attachments/{attachment}/metadata")
            assert foreign_metadata.status_code == 400
            source.write_text("changed after capture")
            diff_request = {"repository_path": str(checkout), "path": "selected", "comparison": "staged"}
            diff = (await api.get("/api/host/git/diff", params=diff_request)).json()
            patch = await api.post(
                prefix + "/host-git-captures", json={**diff_request, "expected_revision": diff["revision"]}
            )
            assert patch.status_code == 200, patch.text
            patch_attachment = patch.json()["attachment"]["attachment_id"]
            (checkout / "selected").write_text("changed patch after capture\n")
            git(checkout, "add", "selected")
            path = ws + prefix + "/draft/connect"
            async with (
                connect(path, proxy=None, origin=http) as first,
                connect(path, proxy=None, origin=http) as second,
            ):
                first_frame, second_frame = await join(first), await join(second)
                draft_id = first_frame["draft_id"]
                assert draft_id == second_frame["draft_id"]
                a, b = document(first_frame), document(second_frame)
                a.get("text", type=Text).insert(0, "review ")
                context_key, diff_key = "inline-" + "1" * 36, "inline-" + "2" * 36
                a.get("attachments", type=Map[str])[context_key] = attachment
                a.get("attachments", type=Map[str])[diff_key] = patch_attachment
                a.get("text", type=Text).insert(7, f"\ufffc{context_key}\ufffc\ufffc{diff_key}\ufffc")
                b.get("text", type=Text).insert(0, "please ")
                await send(first, draft_id, a)
                await send(second, draft_id, b)
                merged = await frame_until(
                    first,
                    lambda f: (
                        f.get("kind") == "draft" and all(s in str(document(f)["text"]) for s in ("please", "review"))
                    ),
                )
                captured = document(merged)
                prompt, ids = composer_values(captured)
                response = await api.post(
                    prefix + "/submit",
                    json={
                        "parts": [
                            re.sub(r"\ufffcinline-[0-9a-f-]{36}\ufffc", "", prompt),
                            *({"attachment_id": id} for id in ids),
                        ]
                    },
                )
                assert response.status_code == 200, response.text
                receipt = response.json()["receipt_id"]
                with fail_after(10):
                    await started.wait()
                # Execution is immutable while another participant prepares the next input.
                next_input = replica(captured)
                next_input.get("text", type=Text).insert(0, "NEXT")
                await send(second, draft_id, next_input)
                await containing(first, "NEXT")
                rejected = await api.post(prefix + "/submit", json={"parts": ["busy"]})
                assert rejected.status_code == 409, rejected.text
                # Steering translates captured context, not current Host bytes.
                steer = await api.post(
                    f"/api/operations/{receipt}/steer",
                    json={"parts": ["use captured", *({"attachment_id": id} for id in ids)]},
                )
                assert steer.status_code == 200, steer.text
                upload = (
                    await api.post(prefix + "/attachments", params={"name": "binary.bin"}, content=b"\x00\xff")
                ).json()
                binary_steer = await api.post(
                    f"/api/operations/{receipt}/steer",
                    json={"parts": ["keep", {"attachment_id": upload["attachment_id"]}]},
                )
                assert binary_steer.status_code == 200, binary_steer.text
                assert binary_steer.json()["accepted"]
                # Positive submit ack permits clearing only captured item identities.
                with captured.transaction():
                    del captured.get("text", type=Text)[:]
                await send(first, draft_id, captured)
                cleared = await frame_until(
                    first, lambda f: f.get("kind") == "draft" and composer_values(document(f)) == ("NEXT", ())
                )
                assert composer_values(document(cleared)) == ("NEXT", ())
                await first.send(
                    json.dumps(
                        {"kind": "presence", "draft_id": draft_id, "presence": {"name": "Alice", "color": "#112233"}}
                    )
                )
                await frame_until(
                    second, lambda f: any(p["name"] == "Alice" for p in f.get("participants", {}).values())
                )
            async with connect(path, proxy=None, origin=http) as rejoined:
                current = await join(rejoined)
                assert current["draft_id"] == draft_id and composer_values(document(current)) == ("NEXT", ())
                assert len(current["participants"]) == 1
                # Existing Thread-scoped attachment identities cannot cross drafts.
                foreign = (
                    await api.post(f"/api/threads/{other}/attachments", params={"name": "other.txt"}, content=b"other")
                ).json()
                invalid = document(current)
                invalid.get("text", type=Text).insert(0, "reject whole edit")
                foreign_key = "inline-" + "f" * 36
                invalid.get("attachments", type=Map[str])[foreign_key] = foreign["attachment_id"]
                invalid.get("text", type=Text).insert(0, f"\ufffc{foreign_key}\ufffc")
                await send(rejoined, draft_id, invalid)
                assert (await frame_until(rejoined, lambda f: "error" in f))["error"]["code"] == "draft_invalid"
            async with connect(ws + f"/api/threads/{other}/draft/connect", proxy=None, origin=http) as isolated:
                assert composer_values(document(await join(isolated))) == ("", ())
            release.set()
            with fail_after(10):
                while (outcome := (await api.get(f"/api/operations/{receipt}")).json())["status"] in {
                    "preparing",
                    "running",
                }:
                    await sleep(0.02)
            assert outcome["status"] == "completed", outcome
            transcript = await api.get(prefix + "/transcript")
            assert "reviewed bytes" in transcript.text and "changed after capture" not in transcript.text
            assert "reviewed patch bytes" in transcript.text and "changed patch after capture" not in transcript.text
    async with listener(tmp_path, configuration_path=root) as (http, ws):
        async with connect(ws + prefix + "/draft/connect", proxy=None, origin=http) as restarted:
            current = await join(restarted)
            assert current["draft_id"] == draft_id
            assert composer_values(document(current)) == ("NEXT", ())
            await send(restarted, "draft-expired", composer_document())
            assert (await frame_until(restarted, lambda f: "error" in f))["error"]["code"] == "draft_instance_conflict"
        async with httpx.AsyncClient(base_url=http, headers=headers, trust_env=False) as api:
            assert "reviewed bytes" in (await api.get(prefix + "/transcript")).text
            missing = await api.get(f"/api/operations/{receipt}")
            assert missing.status_code == 400 and missing.json()["error"]["code"] == "root_receipt_missing"


@pytest.mark.parametrize("action", ["send", "steer"])
@pytest.mark.parametrize("submitted", ["submitted", "中" * 160000], ids=["short", "large-unicode"])
async def test_accepted_input_clears_peer_draft_without_the_submitting_editor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, action: str, submitted: str
) -> None:
    root = _write_configuration(tmp_path)
    started, release = Event(), Event()

    async def model(messages, info):
        started.set()
        await release.wait()
        yield "completed"

    async def resolve(self, context, model_id):
        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    async with listener(tmp_path, configuration_path=root, sharing=False) as (http, ws):
        async with httpx.AsyncClient(
            base_url=http, headers={"Authorization": "Bearer test-only-key"}, trust_env=False
        ) as api:
            thread_id = (await api.post("/api/threads", json={})).json()["thread_id"]
            prefix = f"/api/threads/{thread_id}"
            receipt = None
            if action == "steer":
                receipt = (await api.post(prefix + "/submit", json={"parts": ["start"]})).json()["receipt_id"]
                with fail_after(10):
                    await started.wait()
            path = ws + prefix + "/draft/connect"
            async with connect(path, proxy=None, origin=http) as peer:
                await join(peer)
                async with connect(path, proxy=None, origin=http) as author:
                    initial = await join(author)
                    authored = document(initial)
                    authored.get("text", type=Text).insert(0, submitted)
                    await send(author, initial["draft_id"], authored)
                    accepted = await containing(author, submitted)
                    capture = {"draft_id": accepted["draft_id"], "update_base64": accepted["update_base64"]}
                    next_input = document(accepted)
                    next_input.get("text", type=Text).insert(0, "NEXT ")
                    await send(peer, initial["draft_id"], next_input)
                    await containing(peer, "NEXT ")
                # Navigation/closure has detached the submitting editor. Only the
                # HTTP admission can now consume its snapshot; no client clear follows.
                endpoint = prefix + "/submit" if action == "send" else f"/api/operations/{receipt}/steer"
                body = {"parts": [submitted], "draft": capture}
                if len(submitted) > 1000:
                    # Valid UTF-8 text plus its base64 capture exceeds the ordinary
                    # one MiB API budget but fits the submission endpoint budget.
                    assert len(json.dumps(body, ensure_ascii=False).encode()) > 1024 * 1024
                oversized = await api.post(endpoint, content=b" " * (2 * 1024 * 1024 + 1))
                assert oversized.json()["error"]["code"] == "request_too_large"
                invalid = await api.post(endpoint, json={**body, "draft": {**capture, "draft_id": "stale-draft"}})
                assert invalid.status_code == 409, invalid.text
                if action == "steer":
                    busy = await api.post(prefix + "/submit", json=body)
                    assert busy.status_code == 409, busy.text
                # Rejections leave the synchronized shared input unchanged.
                async with connect(path, proxy=None, origin=http) as observer:
                    assert composer_values(document(await join(observer))) == ("NEXT " + submitted, ())
                response = await api.post(endpoint, json=body)
                assert response.status_code == 200, response.text
                if action == "steer":
                    assert response.json()["accepted"]
                else:
                    receipt = response.json()["receipt_id"]
                cleared = await frame_until(
                    peer, lambda f: f.get("kind") == "draft" and composer_values(document(f)) == ("NEXT ", ())
                )
                assert cleared["draft_id"] == initial["draft_id"]
                assert (await api.get("/api/drafts")).json()[0]["thread_id"] == thread_id
                # A stale reconnect cannot resurrect already-consumed item identities.
                await send(peer, initial["draft_id"], authored)
                async with connect(path, proxy=None, origin=http) as observer:
                    current = await join(observer)
                    assert composer_values(document(current)) == ("NEXT ", ())
                release.set()
                with fail_after(10):
                    while (await api.get(f"/api/operations/{receipt}")).json()["status"] in {"preparing", "running"}:
                        await sleep(0.02)
                rejected = await api.post(
                    f"/api/operations/{receipt}/steer",
                    json={
                        "parts": ["NEXT "],
                        "draft": {"draft_id": current["draft_id"], "update_base64": current["update_base64"]},
                    },
                )
                assert rejected.status_code == 200 and not rejected.json()["accepted"]
                async with connect(path, proxy=None, origin=http) as observer:
                    assert composer_values(document(await join(observer))) == ("NEXT ", ())
