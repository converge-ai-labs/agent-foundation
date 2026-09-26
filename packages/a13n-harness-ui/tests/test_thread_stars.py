"""Shared stars remain metadata, independent of navigation time and execution."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from a13n_harness_ui.errors import StoreConflictError
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from a13n_harness_ui.settings import StorageSettings
from a13n_harness_ui.storage import ObjectKind, ObjectRef, open_local_store
from a13n_harness_ui.storage.contracts import ThreadReadModel
from anyio import Event, fail_after
from pydantic_ai.models.function import FunctionModel

from .test_app import _write_configuration
from .test_configuration_protocol import HEADERS, settled
from .test_interactive_protocol import listener
from .test_thread_repository import _configuration, _initial

pytestmark = pytest.mark.anyio


async def test_star_metadata_preserves_recency_checkpoint_and_version_boundaries(tmp_path):
    settings = StorageSettings(data_root=tmp_path)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    async with open_local_store(settings) as store:
        original = await store.threads.create(
            thread_id="thread-star", configuration=_configuration(), initial_state=_initial(), created_at=start
        )
        assert original.starred is False
        starred = await store.threads.update_metadata(
            thread_id=original.thread_id,
            expected_version=1,
            title=None,
            archived=False,
            starred=True,
            updated_at=start + timedelta(seconds=1),
        )
        assert starred.starred and starred.metadata_version == 2
        assert starred.updated_at > original.updated_at
        assert starred.touched_at == original.touched_at
        assert starred.activity_at == original.activity_at
        unchanged = await store.threads.update_metadata(
            thread_id=original.thread_id, expected_version=2, title=None, archived=False, starred=True
        )
        assert unchanged == starred
        with pytest.raises(StoreConflictError):
            await store.threads.update_metadata(
                thread_id=original.thread_id, expected_version=1, title=None, archived=False, starred=False
            )
        # Unrelated metadata writers retain stars; checkpoints never write the metadata head back.
        await store.threads.update_metadata(
            thread_id=original.thread_id, expected_version=2, title="Renamed", archived=False
        )
        await store.threads.select_continuation(
            thread_id=original.thread_id,
            expected=None,
            replacement=ObjectRef(
                object_kind=ObjectKind.continuation, object_schema_version="1", logical_digest="2" * 64
            ),
            read_model=ThreadReadModel(),
        )
    async with open_local_store(settings) as store:
        saved = await store.threads.get(original.thread_id)
        assert saved.starred and saved.metadata_version == 3
        assert saved.title == "Renamed" and saved.touched_at == original.touched_at
        assert (await store.threads.list(starred=True))[1] == 1
        assert (await store.threads.list(starred=False))[1] == 0


async def test_http_stars_are_shared_scoped_and_outside_recent_pagination(tmp_path):
    configuration = _write_configuration(tmp_path)
    params = {"project_id": "project-main", "include_active": "true", "include_starred": "true", "limit": 5}
    async with listener(tmp_path, configuration_path=configuration) as (http, _):
        async with httpx.AsyncClient(base_url=http, headers=HEADERS, trust_env=False) as api:
            stars = [(await api.post("/api/threads", json={"title": f"Reference {i}"})).json() for i in range(7)]
            recent = [(await api.post("/api/threads", json={})).json()["thread_id"] for _ in range(7)]
            for thread in stars:
                response = await api.patch(
                    f"/api/threads/{thread['thread_id']}/metadata",
                    json={"expected_version": 1, "patch": {"starred": True}},
                )
                assert response.status_code == 200
                assert response.json()["touched_at"] == thread["touched_at"]
            first = (await api.get("/api/threads/activity", params=params)).json()
            assert [row["thread"]["thread_id"] for row in first["starred_rows"]] == [
                t["thread_id"] for t in stars[::-1]
            ]
            assert [row["thread"]["thread_id"] for row in first["rows"]] == recent[::-1][:5]
            assert first["total"] == 14
            tail = (await api.get("/api/threads/activity", params={**params, "cursor": first["next_cursor"]})).json()
            assert tail["starred_rows"] == [] and tail["total"] == 14
            assert [row["thread"]["thread_id"] for row in tail["rows"]] == recent[::-1][5:]
            mismatch = await api.get(
                "/api/threads/activity", params={**params, "include_starred": "false", "cursor": first["next_cursor"]}
            )
            assert mismatch.status_code >= 400
            other = (await api.get("/api/threads/activity", params={**params, "project_id": "other"})).json()
            assert other["starred_rows"] == [] and other["total"] == 0
            # Search only returns matches; its ordinary page is not implicitly star-prioritized.
            search = (await api.get("/api/threads/activity", params={"query": "Reference 0"})).json()
            assert len(search["rows"]) == 1 and search["rows"][0]["thread"]["starred"]
            assert search["starred_rows"] == []
            endpoint = f"/api/threads/{stars[0]['thread_id']}/metadata"
            assert (
                await api.patch(endpoint, json={"expected_version": 2, "patch": {"starred": None}})
            ).status_code >= 400
            assert (
                await api.patch(endpoint, json={"expected_version": 1, "patch": {"starred": False}})
            ).status_code == 409
            archived = (await api.patch(endpoint, json={"expected_version": 2, "patch": {"archived": True}})).json()
            assert archived["starred"]
            assert len((await api.get("/api/threads/activity", params=params)).json()["starred_rows"]) == 6
            assert (
                await api.patch(endpoint, json={"expected_version": 3, "patch": {"archived": False}})
            ).status_code == 200
            assert len((await api.get("/api/threads/activity", params=params)).json()["starred_rows"]) == 7
            unstarred = (await api.patch(endpoint, json={"expected_version": 4, "patch": {"starred": False}})).json()
            assert unstarred["starred"] is False
            assert unstarred["touched_at"] == stars[0]["touched_at"]
            refreshed = (await api.get("/api/threads/activity", params=params)).json()
            assert len(refreshed["starred_rows"]) == 6
            assert [row["thread"]["thread_id"] for row in refreshed["rows"]] == recent[::-1][:5]
    # A different client after restart sees the same shared choice.
    async with listener(tmp_path, configuration_path=configuration) as (http, _):
        async with httpx.AsyncClient(base_url=http, headers=HEADERS, trust_env=False) as api:
            page = (await api.get("/api/threads/activity", params=params)).json()
            assert [row["thread"]["thread_id"] for row in page["starred_rows"]] == [
                t["thread_id"] for t in stars[:0:-1]
            ]


async def test_star_during_run_survives_completion_and_is_not_duplicated(tmp_path, monkeypatch):
    configuration = _write_configuration(tmp_path)
    started, release = Event(), Event()

    async def stream(messages, info):
        started.set()
        await release.wait()
        yield "Finished"

    async def resolve(self, context, model_id):
        return FunctionModel(stream_function=stream)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    async with listener(tmp_path, configuration_path=configuration) as (http, _):
        async with httpx.AsyncClient(base_url=http, headers=HEADERS, trust_env=False) as api:
            thread = (await api.post("/api/threads", json={})).json()
            thread_id = thread["thread_id"]
            receipt = (await api.post(f"/api/threads/{thread_id}/submit", json={"parts": ["Work"]})).json()
            with fail_after(10):
                await started.wait()
            updated = await api.patch(
                f"/api/threads/{thread_id}/metadata", json={"expected_version": 1, "patch": {"starred": True}}
            )
            assert updated.status_code == 200 and updated.json()["root_activity"]["state"] == "running"
            params = {"include_active": "true", "include_starred": "true", "limit": 5}
            page = (await api.get("/api/threads/activity", params=params)).json()
            assert len(page["active_rows"]) == 1 and page["active_rows"][0]["thread"]["starred"]
            assert page["starred_rows"] == page["rows"] == [] and page["total"] == 1
            release.set()
            assert (await settled(api, receipt["receipt_id"]))["status"] == "completed"
            page = (await api.get("/api/threads/activity", params=params)).json()
            assert page["active_rows"] == page["rows"] == []
            assert len(page["starred_rows"]) == 1 and page["starred_rows"][0]["thread"]["starred"]
