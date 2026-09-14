"""Real per-tab presence transport independent of draft, history and native sharing."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import httpx
import pytest
from a13n_harness_ui import webui
from a13n_harness_ui.errors import HarnessUiError
from a13n_harness_ui.page_presence import MAX_PARTICIPANTS, PagePresence, PresenceReport
from a13n_harness_ui.shared_drafts import composer_document
from anyio import fail_after, sleep
from pycrdt import Text
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from .test_app import _write_configuration
from .test_configuration_protocol import HEADERS
from .test_interactive_protocol import frame_until, listener

pytestmark = pytest.mark.anyio


async def join(connection):
    await connection.send('{"api_key":"test-only-key"}')
    return await frame_until(connection, lambda frame: frame.get("kind") == "presence")


def report(target, *, foreground=True, root_thread_id=None):
    return {
        "display_name": "Shared name",
        "color": "#112233",
        "foreground": foreground,
        "focus": {"target": target, "root_thread_id": root_thread_id},
    }


async def test_two_tabs_page_membership_focus_availability_and_draft_independence(tmp_path: Path):
    configuration = _write_configuration(tmp_path)
    native = tmp_path / "view.txt"
    native.write_text("Native view")
    async with listener(tmp_path, configuration_path=configuration) as (http, ws):
        async with httpx.AsyncClient(base_url=http, headers=HEADERS, trust_env=False) as api:
            thread = (await api.post("/api/threads", json={})).json()["thread_id"]
            prefix = f"/api/threads/{thread}"
            before = (await api.get(prefix + "/transcript")).json()
            assert (await api.get("/api/presence")).json()["participants"] == []
            async with connect(ws + "/api/presence/connect", proxy=None, origin=http) as first:
                a = (await join(first))["participant_id"]
                async with connect(ws + "/api/presence/connect", proxy=None, origin=http) as second:
                    b = (await join(second))["participant_id"]
                    assert a != b
                    conversation = {"kind": "conversation", "thread_id": thread}
                    await first.send(json.dumps(report(conversation)))
                    await second.send(json.dumps(report(conversation, foreground=False)))
                    shared = await frame_until(first, lambda frame: b in frame.get("same_page_participant_ids", []))
                    participants = {item["participant_id"]: item for item in shared["participants"]}
                    assert participants[a]["display_name"] == participants[b]["display_name"]
                    assert participants[a]["foreground"] and not participants[b]["foreground"]
                    assert participants[b]["availability"] == "available"
                    async with connect(ws + prefix + "/draft/connect", proxy=None, origin=http) as draft:
                        await draft.send('{"api_key":"test-only-key"}')
                        initial = await frame_until(draft, lambda frame: frame.get("kind") == "draft")
                        replica = composer_document()
                        replica.apply_update(base64.b64decode(initial["update_base64"]))
                        replica.get("text", type=Text).insert(0, "Keep this composer")
                        await draft.send(
                            json.dumps(
                                {
                                    "kind": "sync",
                                    "draft_id": initial["draft_id"],
                                    "update_base64": base64.b64encode(replica.get_update()).decode(),
                                }
                            )
                        )
                        edited = await frame_until(
                            draft, lambda frame: frame.get("update_base64") != initial["update_base64"]
                        )
                        await first.send(
                            json.dumps(report({"kind": "file", "path": str(native)}, root_thread_id=thread))
                        )
                        moved = await frame_until(
                            second,
                            lambda frame: any(
                                item["participant_id"] == a and item["focus"]["target"]["kind"] == "file"
                                for item in frame.get("participants", [])
                                if item["focus"] is not None
                            ),
                        )
                        assert moved["same_page_participant_ids"] == []
                        assert (await api.get(prefix + "/transcript")).json() == before
                        assert (await api.get(prefix + "/comments")).json()["comments"] == []
                        async with connect(ws + prefix + "/draft/connect", proxy=None, origin=http) as observer:
                            await observer.send('{"api_key":"test-only-key"}')
                            preserved = await frame_until(observer, lambda frame: frame.get("kind") == "draft")
                            assert preserved["draft_id"] == edited["draft_id"]
                            assert preserved["update_base64"] == edited["update_base64"]
                        native.unlink()
                        directory = (await api.get("/api/presence")).json()
                        missing = next(item for item in directory["participants"] if item["participant_id"] == a)
                        assert missing["focus"]["target"]["path"] == str(native)
                        assert missing["availability"] == "unavailable"
                        await second.send(json.dumps(report({"kind": "workbench", "section": "settings"})))
                        await frame_until(
                            first,
                            lambda frame: any(
                                item["participant_id"] == b and item["focus"]["target"]["kind"] == "workbench"
                                for item in frame.get("participants", [])
                                if item["focus"] is not None
                            ),
                        )
                remaining = await frame_until(first, lambda frame: len(frame.get("participants", [])) == 1)
                assert remaining["participants"][0]["participant_id"] == a
                async with connect(ws + "/api/presence/connect", proxy=None, origin=http) as rejoined:
                    fresh = await join(rejoined)
                    assert fresh["participant_id"] not in {a, b}
                    own = next(
                        item for item in fresh["participants"] if item["participant_id"] == fresh["participant_id"]
                    )
                    assert own["focus"] is None and own["availability"] == "unknown"
            with fail_after(2):
                while (await api.get("/api/presence")).json()["participants"]:
                    await sleep(0.01)
    async with listener(tmp_path, configuration_path=configuration) as (http, _):
        async with httpx.AsyncClient(base_url=http, headers=HEADERS, trust_env=False) as api:
            assert (await api.get("/api/presence")).json()["participants"] == []
            assert (await api.get(prefix + "/transcript")).json() == before


async def test_presence_auth_no_native_requirement_invalid_report_and_bounded_loss(tmp_path, monkeypatch):
    configuration = _write_configuration(tmp_path)
    async with listener(tmp_path, configuration_path=configuration, sharing=False) as (http, ws):
        async with connect(ws + "/api/presence/connect", proxy=None, origin=http) as connection:
            await connection.send('{"api_key":"wrong"}')
            with pytest.raises(ConnectionClosed) as failure:
                await connection.recv()
            assert failure.value.rcvd.code == 4401
        with pytest.raises(InvalidStatus):
            async with connect(ws + "/api/presence/connect", proxy=None, origin="http://foreign.invalid"):
                pass
        async with httpx.AsyncClient(base_url=http, headers=HEADERS, trust_env=False) as api:
            async with connect(ws + "/api/presence/connect", proxy=None, origin=http) as connection:
                identity = (await join(connection))["participant_id"]
                for target, expected in (
                    ({"kind": "project", "project_id": "project-main"}, "available"),
                    ({"kind": "resource", "resource_kind": "model", "resource_id": "model-primary"}, "available"),
                    ({"kind": "resource", "resource_kind": "model", "resource_id": "model-missing"}, "unavailable"),
                    ({"kind": "file", "path": str(tmp_path)}, "unavailable"),
                    ({"kind": "changes", "repository_root": str(tmp_path)}, "unavailable"),
                    ({"kind": "terminal", "terminal_id": "terminal-missing"}, "unavailable"),
                ):
                    await connection.send(json.dumps(report(target)))
                    frame = await frame_until(
                        connection,
                        lambda f, target=target, expected=expected: any(
                            item["focus"] is not None
                            and all(item["focus"]["target"].get(key) == value for key, value in target.items())
                            and item["availability"] == expected
                            for item in f.get("participants", [])
                        ),
                    )
                    assert frame["participant_id"] == identity
                await connection.send(json.dumps({**report({"kind": "workbench"}), "participant_id": "spoofed"}))
                error = await frame_until(connection, lambda frame: "error" in frame)
                assert error["error"]["code"] == "presence_invalid"
                await connection.send(b"binary-report")
                error = await frame_until(connection, lambda frame: "error" in frame)
                assert error["error"]["code"] == "presence_invalid"
            monkeypatch.setattr(webui, "PRESENCE_TIMEOUT_SECONDS", 0.1)
            async with connect(ws + "/api/presence/connect", proxy=None, origin=http) as idle:
                await join(idle)
                with pytest.raises(ConnectionClosed) as failure:
                    while True:
                        await idle.recv()
                assert failure.value.rcvd.code == 4408
            assert (await api.get("/api/presence")).json()["participants"] == []


async def test_presence_bounds_close_and_independent_directories():
    first, second = PagePresence(), PagePresence()
    ids = [first.attach() for _ in range(MAX_PARTICIPANTS)]
    with pytest.raises(HarnessUiError, match="full"):
        first.attach()
    first.report(ids[0], PresenceReport(display_name="Reader"))
    assert second.participants == {}
    changed = first.changed
    first.close()
    assert changed.is_set() and first.participants == {} and first.closed
    with pytest.raises(HarnessUiError, match="ended"):
        first.report(ids[0], PresenceReport())
