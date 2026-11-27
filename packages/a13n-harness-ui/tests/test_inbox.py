from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from a13n_harness.model_context import (
    ModelContextHistory,
    ModelContextProjection,
    ModelContextProjectionRequest,
    ModelContextRequestKind,
)
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.bot_models import BOT_THREAD_ID
from a13n_harness_ui.configuration.models import HarnessUiDocument
from a13n_harness_ui.errors import HarnessUiError
from a13n_harness_ui.inbox_models import InboxItemMutation
from a13n_harness_ui.live import InboxNotice, RootOperationNotice
from a13n_harness_ui.surfaces import NewThreadDefaults
from a13n_harness_ui.thread_capability import ThreadCollaborationCapability, ThreadToolController
from a13n_harness_ui.web_push import in_scope, inbox_path, notice_payload
from anyio import fail_after

from .test_app import _settings, _write_configuration
from .test_coordinators import coordinator_configuration, create_coordinator

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("migrated_databases")]


async def test_items_are_posted_once_per_call_paged_and_track_read_and_done(tmp_path: Path) -> None:
    root = _write_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        inbox = app.inbox()
        thread = await app.create_thread(title="Release")
        first = await inbox.post(
            thread_id=thread.thread_id, tool_call_id="call-1", kind="result", title="Shipped", message="Done."
        )
        replay = await inbox.post(
            thread_id=thread.thread_id, tool_call_id="call-1", kind="milestone", title="Other", message="Other."
        )
        assert replay == first and first.thread_title == "Release"
        for index, kind in ((2, "needs_human"), (3, "milestone")):
            await inbox.post(
                thread_id=thread.thread_id, tool_call_id=f"call-{index}", kind=kind, title=f"T{index}", message="M"
            )
        page = await inbox.page(state="open", before=None, limit=2)
        assert [item.title for item in page.items] == ["T3", "T2"] and page.unread_count == 3
        rest = await inbox.page(state="open", before=page.next_cursor, limit=2)
        assert [item.item_id for item in rest.items] == [first.item_id] and rest.next_cursor is None

        read = await inbox.update(first.item_id, InboxItemMutation(read=True))
        assert read.read_at is not None and read.done_at is None
        done = await inbox.update(first.item_id, InboxItemMutation(done=True))
        assert done.done_at is not None and done.read_at == read.read_at
        assert [item.item_id for item in (await inbox.page(state="done", before=None, limit=10)).items] == [
            first.item_id
        ]
        assert (await inbox.page(state="open", before=None, limit=10)).unread_count == 2
        reopened = await inbox.update(first.item_id, InboxItemMutation(done=False, read=False))
        assert reopened.done_at is None and reopened.read_at is None
        assert (await inbox.page(state="open", before=None, limit=10)).unread_count == 3

        # No transcript holds the call, so the item has no turn to open.
        location = await inbox.location(first.item_id)
        assert location.thread_id == thread.thread_id and location.turn_id is None
        with pytest.raises(HarnessUiError) as missing:
            await inbox.location("inbox_missing")
        assert missing.value.code == "inbox_item_missing"

        await app.delete_thread(thread_id=thread.thread_id, expected_version=thread.metadata_version)
        assert not (await inbox.page(state="open", before=None, limit=10)).items


async def test_post_tool_belongs_to_roots_that_report_to_the_human(tmp_path: Path) -> None:
    root = coordinator_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        lead = await create_coordinator(app, "project-main")
        ordinary = await app.create_thread(defaults=NewThreadDefaults(project_id="project-main"))
        worker = await app.create_thread(
            defaults=NewThreadDefaults(project_id="project-main"), coordinator_thread_id=lead.thread_id
        )
        controller = ThreadToolController(
            threads=app._store.threads,
            projections=app._projections,
            root_runs=app._root_runs,
            create_thread=app.create_thread,
            configurations=app._configurations,
            inbox=app.inbox(),
        )

        async def capability(thread_id: str) -> ThreadCollaborationCapability:
            captured = await app._root_runs._executor.capture(thread_id=thread_id, prompt="Work")
            return ThreadCollaborationCapability(
                controller=controller, source_thread_id=thread_id, composition=captured.published.value
            )

        for reporter in (ordinary, lead):
            tools = await capability(reporter.thread_id)
            assert "post_to_inbox" in tools.get_toolset().tools
            assert "post_to_inbox" in tools.get_instructions()
        managed = await capability(worker.thread_id)
        assert "post_to_inbox" not in managed.get_toolset().tools
        assert "post_to_inbox" not in managed.get_instructions()

        tools = await capability(ordinary.thread_id)
        context: Any = SimpleNamespace(deps=SimpleNamespace(thread_id=ordinary.thread_id), tool_call_id="call-ask")
        posted = await tools.post_to_inbox(context, "needs_human", "Approve the plan?", "Reply **yes** to proceed.")
        assert posted["ok"] is True
        item = (await app.inbox().page(state="open", before=None, limit=10)).items[0]
        assert (item.item_id, item.thread_id, item.kind) == (posted["item_id"], ordinary.thread_id, "needs_human")


def _ordinary_tools(app) -> ThreadToolController:
    return ThreadToolController(
        threads=app._store.threads,
        projections=app._projections,
        root_runs=app._root_runs,
        create_thread=app.create_thread,
        configurations=app._configurations,
        inbox=app.inbox(),
    )


async def _no_context(request: ModelContextProjectionRequest) -> ModelContextProjection:
    return ModelContextProjection()


async def test_reminder_follows_inputs_lost_context_and_long_unposted_runs(tmp_path: Path) -> None:
    root = _write_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        thread = await app.create_thread()
        captured = await app._root_runs._executor.capture(thread_id=thread.thread_id, prompt="Work")
        tools = ThreadCollaborationCapability(
            controller=_ordinary_tools(app), source_thread_id=thread.thread_id, composition=captured.published.value
        )
        deps = SimpleNamespace(thread_id=thread.thread_id, elapsed_seconds=5.0)
        context: Any = SimpleNamespace(deps=deps, tool_call_id="call-done")
        inputs, results = ModelContextRequestKind.INPUT, ModelContextRequestKind.TOOL_RESULTS

        async def project(kind: ModelContextRequestKind, retained: tuple = ()) -> tuple:
            request = ModelContextProjectionRequest(kind=kind, history=ModelContextHistory.from_blocks(retained))
            return (await tools.wrap_model_context(context, request, _no_context)).blocks

        first = await project(inputs)
        assert len(first) == 1 and "post_to_inbox" in first[0].content
        # Every input repeats it; tool rounds add nothing while it is retained.
        assert await project(inputs, first) == first
        assert await project(results, first) == ()
        # Compaction or handoff replaced the history that held it.
        assert await project(results) == first

        deps.elapsed_seconds = 601.0
        long_run = await project(results, first)
        assert len(long_run) == 1 and "10 minutes" in long_run[0].content
        assert await project(results, (*first, *long_run)) == ()
        posted = await tools.post_to_inbox(context, "result", "Done", "Finished.")
        assert posted["ok"] is True
        assert await project(results, first) == ()


async def test_disabled_setting_removes_tool_guidance_and_reminders(tmp_path: Path) -> None:
    root = _write_configuration(tmp_path)
    document = yaml.safe_load(root.read_text())
    document["inbox"] = {"enabled": False}
    root.write_text(yaml.safe_dump(document))
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        thread = await app.create_thread()
        captured = await app._root_runs._executor.capture(thread_id=thread.thread_id, prompt="Work")
        assert captured.published.value.inbox_enabled is False
        tools = ThreadCollaborationCapability(
            controller=_ordinary_tools(app), source_thread_id=thread.thread_id, composition=captured.published.value
        )
        assert "post_to_inbox" not in tools.get_toolset().tools
        assert "post_to_inbox" not in tools.get_instructions()
        context: Any = SimpleNamespace(deps=SimpleNamespace(thread_id=thread.thread_id, elapsed_seconds=900.0))
        request = ModelContextProjectionRequest(kind=ModelContextRequestKind.INPUT)
        assert (await tools.wrap_model_context(context, request, _no_context)).blocks == ()


def test_auto_mark_read_serializes_independently_of_posting() -> None:
    default = HarnessUiDocument()
    assert default.inbox.auto_mark_read is True
    assert "inbox" not in default.model_dump(mode="json")
    for posting in (False, True):
        document = HarnessUiDocument.model_validate({"inbox": {"enabled": posting, "auto_mark_read": False}})
        dumped = yaml.safe_dump(document.model_dump(mode="json"))
        assert yaml.safe_load(dumped)["inbox"] == {"enabled": posting, "auto_mark_read": False}
        assert HarnessUiDocument.model_validate(yaml.safe_load(dumped)).inbox == document.inbox


async def test_auto_mark_read_uses_live_accepted_config_without_blocking_explicit_read(tmp_path: Path) -> None:
    from a13n_harness_ui.webui import create_webui
    from httpx import ASGITransport, AsyncClient

    root = _write_configuration(tmp_path)
    document = yaml.safe_load(root.read_text())
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        thread = await app.create_thread()
        item = await app.inbox().post_from_mcp(
            thread_id=thread.thread_id, kind="result", title="Ready", message="See diff."
        )

        @asynccontextmanager
        async def factory():
            yield app

        server = create_webui(factory, api_key="test-only-key")
        path = f"/api/inbox/threads/{thread.thread_id}"
        async with (
            server.router.lifespan_context(server),
            AsyncClient(transport=ASGITransport(app=server), base_url="http://localhost") as client,
        ):
            assert (await client.get("/api/status")).status_code == 401
            client.headers["Authorization"] = "Bearer test-only-key"
            assert (await client.get("/api/status")).json()["app"]["inbox_auto_mark_read"] is True

            document["inbox"] = {"auto_mark_read": False}
            root.write_text(yaml.safe_dump(document))
            await app.reload_configuration()
            assert (await client.get("/api/status")).json()["app"]["inbox_auto_mark_read"] is False

            mark = {"item_ids": [item.item_id]}
            assert (await client.post(path + "/mark-read", json=mark)).status_code == 204
            assert (await client.get(path)).json()[0]["read_at"] is None
            explicit = await client.patch(f"/api/inbox/{item.item_id}", json={"read": True})
            assert explicit.status_code == 200 and explicit.json()["read_at"] is not None


async def test_http_routes_list_update_and_locate_items(tmp_path: Path) -> None:
    from a13n_harness_ui.webui import create_webui
    from httpx import ASGITransport, AsyncClient

    key = "test-only-key"
    root = _write_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        thread = await app.create_thread(title="Review")
        item = await app.inbox().post(
            thread_id=thread.thread_id, tool_call_id="call-1", kind="result", title="Ready", message="See diff."
        )

        @asynccontextmanager
        async def factory():
            yield app

        server = create_webui(factory, api_key=key)
        async with server.router.lifespan_context(server):
            async with AsyncClient(transport=ASGITransport(app=server), base_url="http://localhost") as client:
                assert (await client.get("/api/inbox")).status_code == 401
                client.headers["Authorization"] = f"Bearer {key}"
                page = (await client.get("/api/inbox?state=open&limit=10")).json()
                assert page["unread_count"] == 1 and page["items"][0]["title"] == "Ready"
                updated = await client.patch(f"/api/inbox/{item.item_id}", json={"done": True})
                assert updated.status_code == 200 and updated.json()["read_at"] is not None
                assert (await client.get("/api/inbox")).json() == {
                    "items": [],
                    "next_cursor": None,
                    "unread_count": 0,
                    "unread_by_group": {"needs_you": 0, "updates": 0},
                }
                location = (await client.get(f"/api/inbox/{item.item_id}/location")).json()
                assert location["thread_id"] == thread.thread_id and location["turn_id"] is None
                assert (await client.get("/api/inbox?limit=0")).status_code == 422


async def test_thread_mark_read_is_owner_only_scoped_and_bounded(tmp_path: Path) -> None:
    from a13n_harness_ui.webui import create_webui
    from anyio import move_on_after
    from httpx import ASGITransport, AsyncClient

    root = coordinator_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        inbox = app.inbox()
        thread = await create_coordinator(app)
        other = await app.create_thread(title="Other")
        call = await inbox.post(
            thread_id=thread.thread_id, tool_call_id="call-1", kind="result", title="One", message="Ready"
        )
        mcp = await inbox.post_from_mcp(thread_id=thread.thread_id, kind="needs_human", title="Two", message="Review")
        done = await inbox.post(
            thread_id=thread.thread_id, tool_call_id="call-done", kind="result", title="Done", message="Handled"
        )
        done = await inbox.update(done.item_id, InboxItemMutation(done=True))
        unrelated = await inbox.post_from_mcp(thread_id=other.thread_id, kind="result", title="Other", message="Other")

        @asynccontextmanager
        async def factory():
            yield app

        server = create_webui(factory, api_key="test-only-key")
        path = f"/api/inbox/threads/{thread.thread_id}"
        async with (
            server.router.lifespan_context(server),
            AsyncClient(transport=ASGITransport(app=server), base_url="http://localhost") as client,
        ):
            assert (await client.get(path)).status_code == 401
            assert (await client.post(path + "/mark-read", json={})).status_code == 401
            client.headers["Authorization"] = "Bearer test-only-key"
            snapshot = {post["item_id"]: post for post in (await client.get(path)).json()}
            assert {item_id: post["has_transcript"] for item_id, post in snapshot.items()} == {
                call.item_id: True,
                mcp.item_id: False,
                done.item_id: True,
            }
            assert snapshot[call.item_id]["tool_call_id"] == call.tool_call_id
            assert snapshot[call.item_id]["created_at"] == call.model_dump(mode="json")["created_at"]
            assert snapshot[call.item_id]["read_at"] is snapshot[call.item_id]["done_at"] is None
            assert snapshot[done.item_id]["done_at"] == done.model_dump(mode="json")["done_at"]
            late = await inbox.post_from_mcp(thread_id=thread.thread_id, kind="result", title="Later", message="New")
            mark = {"item_ids": [*snapshot, unrelated.item_id]}

            async with app.summary_events() as events:
                assert (await client.post(path + "/mark-read", json=mark)).status_code == 204
                with fail_after(2):
                    event = await events.receive()
                assert event.kind == "inbox" and event.thread_id == thread.thread_id and event.inbox is None
                assert (await client.post(path + "/mark-read", json=mark)).status_code == 204
                with move_on_after(0.05) as scope:
                    await events.receive()
                assert scope.cancelled_caught

            page = (await client.get("/api/inbox")).json()
            by_id = {item["item_id"]: item for item in page["items"]}
            assert page["unread_count"] == 2
            assert by_id[call.item_id]["read_at"] == by_id[mcp.item_id]["read_at"] is not None
            assert by_id[call.item_id]["done_at"] is by_id[mcp.item_id]["done_at"] is None
            assert by_id[late.item_id]["read_at"] is by_id[unrelated.item_id]["read_at"] is None
            assert (await inbox.page(state="done", before=None, limit=1)).items == (done,)
            snapshot = {post["item_id"]: post for post in (await client.get(path)).json()}
            assert set(snapshot) == {call.item_id, mcp.item_id, late.item_id, done.item_id}
            assert snapshot[call.item_id]["read_at"] == by_id[call.item_id]["read_at"]
            assert snapshot[done.item_id]["done_at"] == done.model_dump(mode="json")["done_at"]
            assert (await client.patch(f"/api/inbox/{call.item_id}", json={"read": False})).status_code == 200
            unread = (await client.get(path)).json()
            assert {post["item_id"] for post in unread if post["read_at"] is None} == {call.item_id, late.item_id}


async def test_mark_read_is_scoped_to_one_open_group_across_pages(tmp_path: Path) -> None:
    from a13n_harness_ui.webui import create_webui
    from httpx import ASGITransport, AsyncClient

    root = _write_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        thread = await app.create_thread()
        inbox = app.inbox()
        question, result, read, done, milestone = [
            await inbox.post(thread_id=thread.thread_id, tool_call_id=label, kind=kind, title=label, message="Message")
            for kind, label in (
                ("needs_human", "Question"),
                ("result", "Result"),
                ("result", "Read"),
                ("result", "Done"),
                ("milestone", "Milestone"),
            )
        ]
        read = await inbox.update(read.item_id, InboxItemMutation(read=True))
        # The single-item API can explicitly clear read time on Done; bulk changes must still skip it.
        done = await inbox.update(done.item_id, InboxItemMutation(done=True, read=False))

        @asynccontextmanager
        async def factory():
            yield app

        key = "test-only-key"
        server = create_webui(factory, api_key=key)
        async with (
            server.router.lifespan_context(server),
            AsyncClient(transport=ASGITransport(app=server), base_url="http://localhost") as client,
        ):
            path = "/api/inbox/mark-read"
            assert (await client.post(path, json={"group": "updates"})).status_code == 401
            client.headers["Authorization"] = f"Bearer {key}"
            invalid = await client.post(path, json={"group": "done"})
            assert invalid.json()["error"]["code"] == "request_invalid"
            first = (await client.get("/api/inbox?limit=1")).json()
            assert [item["item_id"] for item in first["items"]] == [milestone.item_id]
            assert first["unread_count"] == 3
            assert first["unread_by_group"] == {"needs_you": 1, "updates": 2}

            async with app.summary_events() as events:
                assert (await client.post(path, json={"group": "updates"})).status_code == 204
                with fail_after(2):
                    event = await events.receive()
                assert event.kind == "inbox" and event.inbox is None

            page = await inbox.page(state="open", before=None, limit=10)
            assert page.unread_count == 1
            assert page.unread_by_group.model_dump() == {"needs_you": 1, "updates": 0}
            by_id = {item.item_id: item for item in page.items}
            assert by_id[question.item_id] == question
            assert by_id[read.item_id] == read
            assert by_id[result.item_id].read_at == by_id[milestone.item_id].read_at is not None
            assert by_id[result.item_id].done_at is None
            assert (await inbox.page(state="done", before=None, limit=10)).items == (done,)

            assert (await client.post(path, json={"group": "needs_you"})).status_code == 204
            assert (await client.post(path, json={"group": "updates"})).status_code == 204
            page = await inbox.page(state="open", before=None, limit=10)
            assert page.unread_count == 0 and len(page.items) == 4
            assert {item.item_id: item for item in page.items}[result.item_id] == by_id[result.item_id]

            await inbox.post(
                thread_id=thread.thread_id, tool_call_id="new", kind="result", title="New update", message="Later"
            )
            assert (await inbox.page(state="open", before=None, limit=1)).unread_by_group.updates == 1


@pytest.mark.anyio
async def test_mark_done_moves_all_open_items_in_one_group(tmp_path: Path) -> None:
    from a13n_harness_ui.webui import create_webui
    from httpx import ASGITransport, AsyncClient

    root = _write_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        thread = await app.create_thread()
        inbox = app.inbox()
        question, result, milestone, done = [
            await inbox.post(thread_id=thread.thread_id, tool_call_id=title, kind=kind, title=title, message="Message")
            for kind, title in (
                ("needs_human", "Question"),
                ("result", "Result"),
                ("milestone", "Milestone"),
                ("result", "Already done"),
            )
        ]
        milestone = await inbox.update(milestone.item_id, InboxItemMutation(read=True))
        done = await inbox.update(done.item_id, InboxItemMutation(done=True))

        @asynccontextmanager
        async def factory():
            yield app

        key = "test-only-key"
        server = create_webui(factory, api_key=key)
        async with (
            server.router.lifespan_context(server),
            AsyncClient(transport=ASGITransport(app=server), base_url="http://localhost") as client,
        ):
            path = "/api/inbox/mark-done"
            assert (await client.post(path, json={"group": "updates"})).status_code == 401
            client.headers["Authorization"] = f"Bearer {key}"
            invalid = await client.post(path, json={"group": "all"})
            assert invalid.json()["error"]["code"] == "request_invalid"
            first = (await client.get("/api/inbox?limit=1")).json()
            assert [item["item_id"] for item in first["items"]] == [milestone.item_id]
            assert first["next_cursor"] == milestone.item_id

            async with app.summary_events() as events:
                assert (await client.post(path, json={"group": "updates"})).status_code == 204
                with fail_after(2):
                    event = await events.receive()
                assert event.kind == "inbox" and event.inbox is None

            page = await inbox.page(state="open", before=None, limit=10)
            assert page.items == (question,)
            assert page.unread_by_group.model_dump() == {"needs_you": 1, "updates": 0}
            completed = await inbox.page(state="done", before=None, limit=10)
            by_id = {item.item_id: item for item in completed.items}
            assert by_id[done.item_id] == done
            assert by_id[result.item_id].read_at == by_id[result.item_id].done_at is not None
            assert by_id[milestone.item_id].read_at == milestone.read_at
            assert by_id[milestone.item_id].done_at == by_id[result.item_id].done_at
            assert (await client.post(path, json={"group": "updates"})).status_code == 204
            assert (await inbox.page(state="done", before=None, limit=10)).items == completed.items


def test_inbox_notifications_follow_scope_and_open_the_item() -> None:
    inbox = InboxNotice(item_id="inbox_1", kind="result", title="Shipped", brief="All checks passed.")
    finished = RootOperationNotice(receipt_id="receipt-1", status="completed", brief="Finished.")
    assert in_scope("inbox", "thread-a", inbox) and not in_scope("inbox", "thread-a", finished)
    assert in_scope("all", "thread-a", inbox) and in_scope("all", "thread-a", finished)
    assert not in_scope("bot", "thread-a", inbox) and in_scope("bot", BOT_THREAD_ID, inbox)
    assert inbox_path("thread-a", "inbox_1") == "/threads/thread-a?inbox=inbox_1"
    assert inbox_path(BOT_THREAD_ID, "inbox_1") == "/bot?inbox=inbox_1"
    payload = notice_payload("thread-a", "Release", inbox, language="zh-CN")
    assert payload["title"] == "收件箱新消息 · Shipped"
    assert payload["path"] == "/threads/thread-a?inbox=inbox_1"
