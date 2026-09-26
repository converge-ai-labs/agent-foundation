"""Complete saved feedback capture through ordinary retained input and steering."""

from __future__ import annotations

import httpx
import pytest
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from anyio import Event, fail_after
from pydantic_ai.models.function import FunctionModel

from .test_app import _write_configuration
from .test_comment_protocol import publication, targets
from .test_configuration_protocol import HEADERS, settled
from .test_interactive_protocol import listener

pytestmark = pytest.mark.anyio


async def test_comment_capture_is_explicit_complete_scoped_and_retained_through_send_steer_restart(
    tmp_path, monkeypatch
):
    configuration = _write_configuration(tmp_path)
    started, release = Event(), Event()
    calls = 0
    original = "A\U0001f600 **complete original output**\nSecond line."

    async def stream(messages, info):
        nonlocal calls
        calls += 1
        if calls == 2:
            started.set()
            await release.wait()
        yield original if calls == 1 else "New answer"

    async def resolve(self, context, model_id):
        return FunctionModel(stream_function=stream)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    async with listener(tmp_path, configuration_path=configuration, sharing=False) as (http, _):
        async with httpx.AsyncClient(base_url=http, headers=HEADERS, trust_env=False) as api:
            thread = (await api.post("/api/threads", json={})).json()["thread_id"]
            prefix = f"/api/threads/{thread}"
            receipt = (await api.post(prefix + "/submit", json={"parts": ["First"]})).json()["receipt_id"]
            await settled(api, receipt)
            history = (await api.get(prefix + "/transcript")).json()
            target = targets(history)[0]
            request = publication(target, body="Explain every part, not just the quote.\nKeep this second paragraph.")
            request["selection"] = {"start": 1, "end": 2, "quote": "\U0001f600"}
            comment = (await api.post(prefix + "/comments", json=request)).json()
            before = (await api.get(prefix)).json()
            capture_path = prefix + f"/comments/{comment['comment_id']}/capture"
            response = await api.post(capture_path)
            assert response.status_code == 200, response.text
            attachment = response.json()
            assert attachment["source"] == {
                "kind": "comment_reference",
                "root_thread_id": thread,
                "comment_id": comment["comment_id"],
                "target": target,
            }
            assert attachment["comment"] == {
                "version": 1,
                "author": "Reader",
                "preview": request["body"],
                "quote": "\U0001f600",
            }
            download = prefix + f"/attachments/{attachment['attachment_id']}"
            captured = (await api.get(download)).text
            assert original in captured and "Keep this second paragraph." in captured
            assert comment["comment_id"] in captured and "Reader" in captured
            assert (await api.get(prefix)).json() == before
            assert (await api.get(prefix + "/transcript")).json() == history
            assert calls == 1  # capturing never invokes the model
            foreign = (await api.post("/api/threads", json={})).json()["thread_id"]
            assert (
                await api.post(f"/api/threads/{foreign}/comments/{comment['comment_id']}/capture")
            ).status_code == 400
            body = {"parts": ["Review captured feedback", {"attachment_id": attachment["attachment_id"]}]}
            receipt = (await api.post(prefix + "/submit", json=body)).json()["receipt_id"]
            with fail_after(10):
                await started.wait()
            steer = await api.post(
                f"/api/operations/{receipt}/steer",
                json={"parts": ["Also use this feedback", {"attachment_id": attachment["attachment_id"]}]},
            )
            assert steer.status_code == 200 and steer.json()["accepted"], steer.text
            release.set()
            assert (await settled(api, receipt))["status"] == "completed"
            history = (await api.get(prefix + "/transcript")).json()
            references = [
                part
                for entry in history["entries"]
                for part in entry["parts"]
                if (part.get("metadata") or {})
                .get("harness_ui", {})
                .get("attachment", {})
                .get("source", {})
                .get("kind")
                == "comment_reference"
            ]
            assert len(references) == 2  # initial input and steering retain their presentation metadata
            assert all(part["text"] == captured for part in references)
            assert (await api.get(download)).text == captured
    async with listener(tmp_path, configuration_path=configuration, sharing=False) as (http, _):
        async with httpx.AsyncClient(base_url=http, headers=HEADERS, trust_env=False) as api:
            assert (await api.get(download)).text == captured
            response = await api.post(capture_path)
            assert response.status_code == 200, response.text
            assert response.json()["source"] == attachment["source"]
            again = await api.get(prefix + f"/attachments/{response.json()['attachment_id']}")
            assert again.text == captured  # later history never substitutes for the original output


@pytest.mark.parametrize("original", ["x" * 65537, "\U0001f600" * 20000], ids=["ascii-byte-limit", "utf8-byte-limit"])
async def test_comment_capture_rejects_incomplete_or_oversized_utf8_without_truncation(tmp_path, monkeypatch, original):
    configuration = _write_configuration(tmp_path)

    async def stream(messages, info):
        yield original

    async def resolve(self, context, model_id):
        return FunctionModel(stream_function=stream)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    async with listener(tmp_path, configuration_path=configuration, sharing=False) as (http, _):
        async with httpx.AsyncClient(base_url=http, headers=HEADERS, trust_env=False) as api:
            thread = (await api.post("/api/threads", json={})).json()["thread_id"]
            prefix = f"/api/threads/{thread}"
            receipt = (await api.post(prefix + "/submit", json={"parts": ["First"]})).json()["receipt_id"]
            await settled(api, receipt)
            history = (await api.get(prefix + "/transcript")).json()
            assistant = next(
                part for entry in history["entries"] for part in entry["parts"] if part["kind"] == "assistant"
            )
            assert assistant["text_truncated"] == (len(original) > 65536)
            target = targets(history)[0]
            comment = (await api.post(prefix + "/comments", json=publication(target))).json()
            response = await api.post(prefix + f"/comments/{comment['comment_id']}/capture")
            assert response.status_code == 400, response.text
            assert response.json()["error"]["code"] == "comment_context_unsupported"
            assert not list((tmp_path / "data" / "threads" / thread / "attachments").glob("attachment-*"))
            assert (await api.get(prefix + "/comments/" + comment["comment_id"])).json() == comment
