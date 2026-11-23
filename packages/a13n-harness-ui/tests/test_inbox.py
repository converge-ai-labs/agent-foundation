from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.bot_models import BOT_THREAD_ID
from a13n_harness_ui.errors import HarnessUiError
from a13n_harness_ui.inbox_models import InboxItemMutation
from a13n_harness_ui.live import InboxNotice, RootOperationNotice
from a13n_harness_ui.surfaces import NewThreadDefaults
from a13n_harness_ui.thread_capability import ThreadCollaborationCapability, ThreadToolController
from a13n_harness_ui.web_push import in_scope, inbox_path, notice_payload

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
                assert (await client.get("/api/inbox")).json() == {"items": [], "next_cursor": None, "unread_count": 0}
                location = (await client.get(f"/api/inbox/{item.item_id}/location")).json()
                assert location["thread_id"] == thread.thread_id and location["turn_id"] is None
                assert (await client.get("/api/inbox?limit=0")).status_code == 422


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
