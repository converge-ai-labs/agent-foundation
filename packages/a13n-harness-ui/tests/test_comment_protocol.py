"""Saved-output collaboration across real HTTP clients, Runs and App restart."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from a13n_harness_ui.output_comments import OutputComments
from a13n_harness_ui.storage.comments import OutputCommentRepository
from anyio import Event, fail_after
from pydantic_ai.models.function import FunctionModel

from .test_app import _write_configuration
from .test_configuration_protocol import HEADERS, settled
from .test_interactive_protocol import listener

pytestmark = pytest.mark.anyio


def publication(target, *, body="Please explain this."):
    return {
        "comment_id": "comment-" + uuid4().hex,
        "target": target,
        "author": {"display_name": "Reader", "participant_id": "participant-original"},
        "body": body,
    }


def targets(history):
    return [
        part["comment_target"]
        for entry in history["entries"]
        for part in entry["parts"]
        if part["comment_target"] is not None
    ]


async def test_saved_comments_concurrent_publish_reconciliation_original_output_and_restart(tmp_path, monkeypatch):
    configuration = _write_configuration(tmp_path)
    started, release = Event(), Event()
    calls = 0

    async def stream(messages, info):
        nonlocal calls
        calls += 1
        if calls == 2:
            started.set()
            await release.wait()
        yield "A\U0001f600e\u0301 source " + str(calls)

    async def resolve(self, context, model_id):
        return FunctionModel(stream_function=stream)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    async with listener(tmp_path, configuration_path=configuration) as (http, _):
        async with httpx.AsyncClient(base_url=http, headers=HEADERS, trust_env=False) as api:
            thread_id = (await api.post("/api/threads", json={})).json()["thread_id"]
            prefix = f"/api/threads/{thread_id}"
            assert (await api.get(prefix + "/comments")).json() == {"comments": [], "next_cursor": None}
            receipt = (await api.post(prefix + "/submit", json={"parts": ["First"]})).json()["receipt_id"]
            assert (await settled(api, receipt))["status"] == "completed"
            before = (await api.get(prefix)).json()
            history = (await api.get(prefix + "/transcript")).json()
            target = targets(history)[0]
            body = publication(target)
            body["selection"] = {"start": 1, "end": 4, "quote": "\U0001f600e\u0301"}
            invalid = {**body, "selection": {"start": 1, "end": 4, "quote": "bad"}}
            assert (await api.post(prefix + "/comments", json=invalid)).status_code == 400
            first = await api.post(prefix + "/comments", json=body)
            assert first.status_code == 200, first.text
            committed = first.json()
            assert (await api.get(prefix)).json() == before
            assert (await api.get(prefix + "/transcript")).json() == history
            assert (await api.post(prefix + "/comments", json=body)).json() == committed
            conflict = await api.post(prefix + "/comments", json={**body, "body": "Different"})
            assert conflict.status_code == 409
            # Comments on the already-saved source do not contend with root admission.
            second = (await api.post(prefix + "/submit", json={"parts": ["Second"]})).json()["receipt_id"]
            with fail_after(10):
                await started.wait()
            async with httpx.AsyncClient(base_url=http, headers=HEADERS, trust_env=False) as other:
                posts = await asyncio.gather(
                    api.post(prefix + "/comments", json=publication(target)),
                    other.post(prefix + "/comments", json=publication(target)),
                )
            assert all(response.status_code == 200 for response in posts)
            release.set()
            assert (await settled(api, second))["status"] == "completed"
            new_history = (await api.get(prefix + "/transcript")).json()
            assert targets(new_history)[0] != target  # same text is not the same source identity
            assert (await api.post(prefix + "/comments", json=body)).json() == committed
            original = (await api.post(prefix + "/saved-output", json=target, params={"offset": 1, "limit": 3})).json()
            assert original["text"] == "\U0001f600e\u0301" and original["next_offset"] == 4
            another = await api.post(prefix + "/comments", json=publication(target))
            assert another.status_code == 200  # original block is retained, even after source movement
            first_page = (await api.get(prefix + "/comments", params={"limit": 2})).json()
            second_page = (
                await api.get(prefix + "/comments", params={"limit": 2, "cursor": first_page["next_cursor"]})
            ).json()
            ids = [item["comment_id"] for page in (first_page, second_page) for item in page["comments"]]
            assert len(ids) == len(set(ids)) == 4 and second_page["next_cursor"] is None
            newest = (await api.get(prefix + "/comments", params={"limit": 2, "newest_first": True})).json()
            older = (
                await api.get(
                    prefix + "/comments", params={"limit": 2, "newest_first": True, "cursor": newest["next_cursor"]}
                )
            ).json()
            assert [item["comment_id"] for page in (newest, older) for item in page["comments"]] == ids[::-1]
            assert (await api.get(prefix + "/comments", params={"cursor": newest["next_cursor"]})).status_code == 400
            filtered = await api.get(prefix + "/comments", params={"target": json.dumps(target)})
            assert len(filtered.json()["comments"]) == 4
            assert (await api.get(prefix + "/comments", params={"target": "{"})).status_code == 400
            other_id = (await api.post("/api/threads", json={})).json()["thread_id"]
            foreign = f"/api/threads/{other_id}"
            assert (await api.get(foreign + "/comments/" + body["comment_id"])).status_code == 400
            assert (await api.post(foreign + "/comments", json=publication(target))).status_code == 400
            assert (await api.post(foreign + "/saved-output", json=target)).status_code == 400
            assert (
                await api.get(foreign + "/comments", params={"cursor": first_page["next_cursor"]})
            ).status_code == 400
            async with httpx.AsyncClient(base_url=http, trust_env=False) as anonymous:
                assert (await anonymous.get(prefix + "/comments")).status_code == 401
    async with listener(tmp_path, configuration_path=configuration) as (http, _):
        async with httpx.AsyncClient(base_url=http, headers=HEADERS, trust_env=False) as api:
            assert (await api.post(prefix + "/comments", json=body)).json() == committed
            assert (await api.get(prefix + "/comments/" + body["comment_id"])).json() == committed
            assert (await api.post(prefix + "/saved-output", json=target)).json()[
                "text"
            ] == "A\U0001f600e\u0301 source 1"


async def test_first_comment_checks_selection_at_commit_and_uncertain_outcome(tmp_path: Path, monkeypatch):
    configuration = _write_configuration(tmp_path)

    async def stream(messages, info):
        yield "Saved answer"

    async def resolve(self, context, model_id):
        return FunctionModel(stream_function=stream)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    async with listener(tmp_path, configuration_path=configuration) as (http, _):
        async with httpx.AsyncClient(base_url=http, headers=HEADERS, trust_env=False) as api:
            thread = (await api.post("/api/threads", json={})).json()["thread_id"]
            prefix = f"/api/threads/{thread}"
            receipt = (await api.post(prefix + "/submit", json={"parts": ["First"]})).json()["receipt_id"]
            await settled(api, receipt)
            target = targets((await api.get(prefix + "/transcript")).json())[0]
            body = publication(target)
            validated, release = Event(), Event()
            read_text = OutputComments._text

            async def pause(self, target, source):
                text = await read_text(self, target, source)
                validated.set()
                await release.wait()
                return text

            with monkeypatch.context() as patch:
                patch.setattr(OutputComments, "_text", pause)
                pending = asyncio.create_task(api.post(prefix + "/comments", json=body))
                with fail_after(10):
                    await validated.wait()
                receipt = (await api.post(prefix + "/submit", json={"parts": ["Second"]})).json()["receipt_id"]
                await settled(api, receipt)
                release.set()
                stale = await pending
            assert stale.status_code == 409 and stale.json()["error"]["code"] == "comment_target_stale"
            assert (await api.get(prefix + "/comments")).json()["comments"] == []
            target = targets((await api.get(prefix + "/transcript")).json())[0]
            body = publication(target)
            publish = OutputCommentRepository.publish

            async def lose_response(self, root, publication, source):
                await publish(self, root, publication, source)
                raise RuntimeError("Simulated response loss after commit")

            with monkeypatch.context() as patch:
                patch.setattr(OutputCommentRepository, "publish", lose_response)
                assert (await api.post(prefix + "/comments", json=body)).status_code == 500
            reconciled = await api.post(prefix + "/comments", json=body)
            assert reconciled.status_code == 200
            assert len((await api.get(prefix + "/comments")).json()["comments"]) == 1


async def test_comment_commit_failure_rolls_back_without_hint_and_unsaved_output_is_not_commentable(
    tmp_path, monkeypatch
):
    from a13n_harness_ui.live import HarnessUiSummaryHub
    from sqlalchemy.ext.asyncio import AsyncSession

    configuration = _write_configuration(tmp_path)

    async def stream(messages, info):
        yield "Saved answer"

    async def resolve(self, context, model_id):
        return FunctionModel(stream_function=stream)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    async with listener(tmp_path, configuration_path=configuration) as (http, _):
        async with httpx.AsyncClient(base_url=http, headers=HEADERS, trust_env=False) as api:
            thread = (await api.post("/api/threads", json={})).json()["thread_id"]
            prefix = f"/api/threads/{thread}"
            fake = {
                "producing_thread_id": thread,
                "source_id": "0" * 64,
                "location": {"kind": "root_text", "message": 0, "part": 0},
            }
            assert (await api.post(prefix + "/comments", json=publication(fake))).status_code == 409
            receipt = (await api.post(prefix + "/submit", json={"parts": ["First"]})).json()["receipt_id"]
            await settled(api, receipt)
            history = (await api.get(prefix + "/transcript")).json()
            target = targets(history)[0]
            body = publication(target)
            hints = []
            original_publish = HarnessUiSummaryHub.publish

            async def hint(self, **kwargs):
                hints.append(kwargs)
                await original_publish(self, **kwargs)

            async def fail_commit(self):
                raise RuntimeError("Simulated SQLite commit failure")

            with monkeypatch.context() as patch:
                patch.setattr(AsyncSession, "commit", fail_commit)
                patch.setattr(HarnessUiSummaryHub, "publish", hint)
                assert (await api.post(prefix + "/comments", json=body)).status_code == 500
            assert not hints
            assert (await api.get(prefix + "/comments")).json()["comments"] == []
            first = await api.post(prefix + "/comments", json=body)
            assert first.status_code == 200
            # A visible user input and arbitrary message/part are not assistant targets.
            for location in (
                {"kind": "root_text", "message": 0, "part": 0},
                {"kind": "root_text", "message": 999, "part": 0},
            ):
                invalid = publication({**target, "location": location})
                assert (await api.post(prefix + "/comments", json=invalid)).status_code == 400
            # Same identity races produce one durable record, not a second publication.
            raced = publication(target)
            responses = await asyncio.gather(*(api.post(prefix + "/comments", json=raced) for _ in range(3)))
            assert all(response.json() == responses[0].json() for response in responses)
            assert len((await api.get(prefix + "/comments")).json()["comments"]) == 2
            # Broken immutable source does not erase comments or prevent reconciliation.
            objects = list((tmp_path / "data" / "objects").rglob("*" + target["source_id"] + "*"))
            assert len(objects) == 1
            objects[0].write_bytes(b"broken test object")
            assert (await api.post(prefix + "/comments", json=body)).json() == first.json()
            assert (await api.get(prefix + "/comments/" + body["comment_id"])).json() == first.json()
            unavailable = await api.post(prefix + "/saved-output", json=target)
            assert unavailable.status_code != 200


async def test_comment_edit_delete_conflicts_captures_and_restart(tmp_path, monkeypatch):
    configuration = _write_configuration(tmp_path)

    async def stream(messages, info):
        yield "Original saved answer"

    async def resolve(self, context, model_id):
        return FunctionModel(stream_function=stream)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    async with listener(tmp_path, configuration_path=configuration) as (http, _):
        async with httpx.AsyncClient(base_url=http, headers=HEADERS, trust_env=False) as api:
            thread = (await api.post("/api/threads", json={})).json()["thread_id"]
            prefix = f"/api/threads/{thread}"
            receipt = (await api.post(prefix + "/submit", json={"parts": ["First"]})).json()["receipt_id"]
            await settled(api, receipt)
            history = (await api.get(prefix + "/transcript")).json()
            request = publication(targets(history)[0], body="Original comment")
            first = (await api.post(prefix + "/comments", json=request)).json()
            path = prefix + "/comments/" + first["comment_id"]
            captured = (await api.post(path + "/capture", params={"expected_version": 1})).json()
            download = prefix + "/attachments/" + captured["attachment_id"]
            frozen = (await api.get(download)).text
            changes = await asyncio.gather(
                api.patch(path, json={"expected_version": 1, "body": "Edit one"}),
                api.patch(path, json={"expected_version": 1, "body": "Edit two"}),
            )
            assert sorted(item.status_code for item in changes) == [200, 409]
            edited = next(item.json() for item in changes if item.status_code == 200)
            assert edited["version"] == 2 and edited["updated_at"] is not None
            assert edited["target"] == first["target"] and edited["author"] == first["author"]
            assert edited["created_at"] == first["created_at"]
            # Lost edit acknowledgements reconcile without applying the mutation twice.
            retry = await api.patch(path, json={"expected_version": 1, "body": edited["body"]})
            assert retry.json() == edited
            assert (await api.post(prefix + "/comments", json=request)).status_code == 409
            assert (await api.post(path + "/capture", params={"expected_version": 1})).status_code == 409
            assert (await api.delete(path, params={"expected_version": 1})).status_code == 409
            assert (await api.patch(path, json={"expected_version": 2, "body": " "})).status_code == 400
            assert (await api.patch(path, json={"expected_version": 2, "body": "x", "author": {}})).status_code == 400
            other = (await api.post("/api/threads", json={})).json()["thread_id"]
            foreign = f"/api/threads/{other}/comments/{first['comment_id']}"
            assert (await api.patch(foreign, json={"expected_version": 2, "body": "x"})).status_code == 400
            assert (await api.delete(foreign, params={"expected_version": 2})).status_code == 400
            async with httpx.AsyncClient(base_url=http, trust_env=False) as anonymous:
                assert (await anonymous.delete(path, params={"expected_version": 2})).status_code == 401
            assert (await api.delete(path, params={"expected_version": 2})).status_code == 204
            assert (await api.delete(path, params={"expected_version": 2})).status_code == 204
            assert (await api.get(path)).status_code == 400
            assert (await api.get(prefix + "/comments")).json()["comments"] == []
            assert (await api.post(path + "/capture")).status_code == 400
            assert (await api.patch(path, json={"expected_version": 2, "body": "Restore"})).status_code == 400
            assert (await api.post(prefix + "/comments", json=request)).status_code == 409
            assert (await api.get(download)).text == frozen
            assert (await api.get(prefix + "/transcript")).json() == history
            receipt = (
                await api.post(
                    prefix + "/submit",
                    json={"parts": ["Use the captured version", {"attachment_id": captured["attachment_id"]}]},
                )
            ).json()["receipt_id"]
            assert (await settled(api, receipt))["status"] == "completed"
    async with listener(tmp_path, configuration_path=configuration) as (http, _):
        async with httpx.AsyncClient(base_url=http, headers=HEADERS, trust_env=False) as api:
            assert (await api.get(prefix + "/comments")).json()["comments"] == []
            assert (await api.post(prefix + "/comments", json=request)).status_code == 409
            assert (await api.get(download)).text == frozen
