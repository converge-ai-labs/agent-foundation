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
from .test_interactive_protocol import frame_until, listener
from .test_shared_drafts import replica

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
            assert current["draft_id"] != draft_id
            assert composer_values(document(current)) == ("", ())
            await send(restarted, draft_id, composer_document())
            assert (await frame_until(restarted, lambda f: "error" in f))["error"]["code"] == "draft_instance_conflict"
        async with httpx.AsyncClient(base_url=http, headers=headers, trust_env=False) as api:
            assert "reviewed bytes" in (await api.get(prefix + "/transcript")).text
            missing = await api.get(f"/api/operations/{receipt}")
            assert missing.status_code == 400 and missing.json()["error"]["code"] == "root_receipt_missing"
