"""Durable navigation order and complete active groups across real App/HTTP boundaries."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from a13n_harness_ui.errors import StoreIntegrityError
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from a13n_harness_ui.settings import StorageSettings
from a13n_harness_ui.storage import ObjectKind, ObjectRef, open_local_store
from anyio import Event, fail_after
from pydantic_ai.models.function import FunctionModel

from .test_app import _write_configuration
from .test_configuration_protocol import HEADERS, settled
from .test_interactive_protocol import listener
from .test_thread_repository import _configuration, _initial

pytestmark = pytest.mark.anyio


async def test_touch_is_monotonic_and_independent_from_checkpoints_metadata_and_pagination(tmp_path):
    start = datetime(2026, 1, 1, tzinfo=UTC)
    settings = StorageSettings(data_root=tmp_path)
    async with open_local_store(settings) as store:
        for i in range(3):
            await store.threads.create(
                thread_id=f"thread-{i}", configuration=_configuration(), initial_state=_initial(), created_at=start
            )
        page, total = await store.threads.list(sort="touched", limit=2)
        assert total == 3
        assert [t.thread_id for t in page] == ["thread-2", "thread-1"]
        await store.threads.select_continuation(
            thread_id="thread-0",
            expected=None,
            replacement=ObjectRef(
                object_kind=ObjectKind.continuation, object_schema_version="1", logical_digest="2" * 64
            ),
            updated_at=start + timedelta(seconds=10),
        )
        await store.threads.update_metadata(
            thread_id="thread-1",
            expected_version=1,
            title="Renamed",
            archived=False,
            updated_at=start + timedelta(seconds=20),
        )
        tail, _ = await store.threads.list(sort="touched", before=(page[-1].touched_at, page[-1].thread_id))
        assert [t.thread_id for t in tail] == ["thread-0"]
        before = await store.threads.get("thread-0")
        later = start + timedelta(seconds=30)
        await store.threads.touch("thread-0", touched_at=later)
        await store.threads.touch("thread-0", touched_at=start)
        after = await store.threads.get("thread-0")
        assert after.touched_at == later
        assert after.model_copy(update={"touched_at": before.touched_at}) == before
        with pytest.raises(StoreIntegrityError, match="does not exist"):
            await store.threads.touch("thread-missing")
    async with open_local_store(settings) as reopened:
        page, _ = await reopened.threads.list(sort="touched")
        assert [t.thread_id for t in page] == ["thread-0", "thread-2", "thread-1"]


async def test_http_shows_six_active_roots_plus_five_recent_without_progress_reordering(tmp_path, monkeypatch):
    configuration = _write_configuration(tmp_path)
    started = [Event() for _ in range(6)]
    release = [Event() for _ in range(6)]
    calls = 0

    async def stream(messages, info):
        nonlocal calls
        index = calls
        calls += 1
        started[index].set()
        await release[index].wait()
        yield f"Completed {index}"

    async def resolve(self, context, model_id):
        return FunctionModel(stream_function=stream)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    async with listener(tmp_path, configuration_path=configuration) as (http, _):
        async with httpx.AsyncClient(base_url=http, headers=HEADERS, trust_env=False) as api:
            idle = [(await api.post("/api/threads", json={})).json()["thread_id"] for _ in range(7)]
            running = []
            receipts = []
            for i in range(6):
                thread_id = (await api.post("/api/threads", json={})).json()["thread_id"]
                running.append(thread_id)
                receipt = (await api.post(f"/api/threads/{thread_id}/submit", json={"prompt": f"Work {i}"})).json()
                receipts.append(receipt["receipt_id"])
                with fail_after(10):
                    await started[i].wait()
            params = {"include_active": "true", "limit": 5}
            first = (await api.get("/api/threads/activity", params=params)).json()
            assert [r["thread"]["thread_id"] for r in first["active_rows"]] == running[::-1]
            assert [r["thread"]["thread_id"] for r in first["rows"]] == idle[::-1][:5]
            assert first["total"] == 13
            assert first["next_cursor"]
            tail = (await api.get("/api/threads/activity", params={**params, "cursor": first["next_cursor"]})).json()
            assert len(tail["active_rows"]) == 6
            assert [r["thread"]["thread_id"] for r in tail["rows"]] == idle[::-1][5:]
            assert tail["next_cursor"] is None
            # A cursor for the separated view cannot be consumed by the ordinary list.
            mismatch = await api.get("/api/threads/activity", params={"cursor": first["next_cursor"]})
            assert mismatch.status_code >= 400
            ordinary = (await api.get("/api/threads/activity", params={"limit": 5})).json()
            assert ordinary["active_rows"] == []
            assert len(ordinary["rows"]) == 5
            # Completing the oldest active root also advances its durable navigation recency.
            saved_touch = first["active_rows"][-1]["thread"]["touched_at"]
            release[0].set()
            assert (await settled(api, receipts[0]))["status"] == "completed"
            after = (await api.get("/api/threads/activity", params=params)).json()
            assert [r["thread"]["thread_id"] for r in after["active_rows"]] == running[:0:-1]
            assert after["rows"][0]["thread"]["thread_id"] == running[0]
            assert after["rows"][0]["thread"]["touched_at"] > saved_touch
            # Explicit touch is persistent but does not submit work or mutate history.
            touched = await api.post(f"/api/threads/{idle[0]}/touch")
            assert touched.status_code == 200
            assert touched.json()["continuation_state"] == "initial"
            assert touched.json()["metadata_version"] == 1
            refreshed = (await api.get("/api/threads/activity", params=params)).json()
            assert refreshed["rows"][0]["thread"]["thread_id"] == idle[0]
            assert [r["thread"]["thread_id"] for r in refreshed["active_rows"]] == running[:0:-1]
            assert (await api.post("/api/threads/missing/touch")).status_code >= 400
            for index in range(1, len(receipts)):
                release[index].set()
                assert (await settled(api, receipts[index]))["status"] == "completed"
            finished = (await api.get("/api/threads/activity", params={"include_active": "true", "limit": 20})).json()
            expected = [*running[:0:-1], idle[0], running[0]]
            assert [r["thread"]["thread_id"] for r in finished["rows"]][:7] == expected
    async with listener(tmp_path, configuration_path=configuration) as (http, _):
        async with httpx.AsyncClient(base_url=http, headers=HEADERS, trust_env=False) as api:
            refreshed = (await api.get("/api/threads/activity", params={"include_active": "true", "limit": 20})).json()
            assert refreshed["active_rows"] == []
            assert [r["thread"]["thread_id"] for r in refreshed["rows"]][:7] == expected


async def test_create_returns_durable_thread_identity_when_navigation_write_fails(tmp_path, monkeypatch):
    import sqlite3

    from a13n_harness_ui.app import open_harness_ui_app
    from a13n_harness_ui.thread_capability import ThreadToolController
    from sqlalchemy.exc import OperationalError

    from .test_app import _settings

    async def fail_touch(thread_id):
        raise OperationalError("UPDATE thread", {}, sqlite3.OperationalError("database is locked"))

    configuration = _write_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=configuration) as app:
        source = await app.create_thread()
        monkeypatch.setattr(app._root_runs, "_touch_thread", fail_touch)
        controller = ThreadToolController(
            projections=app._projections,
            root_runs=app._root_runs,
            create_thread=app.create_thread,
            configurations=app._configurations,
        )
        result = await controller.create_thread(
            source_thread_id=source.thread_id,
            prompt="Work",
            title="Created before failed admission",
            agent_id=None,
        )
        assert result["ok"] is False
        assert result["error"]["code"] == "thread_touch_failed"
        created = await app.get_thread(result["thread_id"])
        assert created.thread.title == "Created before failed admission"
        assert created.thread.continuation_state == "initial"
        assert await app.active_root_operation(created.thread.thread_id) is None
        assert (await app.list_threads()).total == 2
