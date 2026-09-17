"""Project navigation filters and paginates before projecting activity rows."""

from pathlib import Path

import httpx
import pytest
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings
from a13n_harness_ui.webui import create_webui

from .test_terminal_surfaces import _write_configuration

pytestmark = pytest.mark.anyio


async def test_project_pages_are_independent_and_cursors_bind_scope(tmp_path: Path) -> None:
    configuration = _write_configuration(
        tmp_path,
        projects=(("project-first", "First", tmp_path), ("project-second", "Second", tmp_path)),
    )
    server = create_webui(
        lambda: open_harness_ui_app(
            HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"), pricing_auto_update=False),
            configuration_path=configuration,
        ),
        api_key="test-navigation",
    )
    async with (
        server.router.lifespan_context(server),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server),
            base_url="http://localhost",
            headers={"Authorization": "Bearer test-navigation"},
        ) as client,
    ):
        groups: dict[str | None, list[dict]] = {"project-first": [], "project-second": [], None: []}
        for index in range(7):
            for project_id, threads in groups.items():
                response = await client.post(
                    "/api/threads", json={"title": f"Task {index}", "defaults": {"project_id": project_id}}
                )
                assert response.status_code == 200, response.text
                threads.append(response.json())

        for project_id, threads in groups.items():
            filters = {"project_id": project_id} if project_id else {"project_scope": "projectless"}
            page = (await client.get("/api/threads/activity", params={**filters, "limit": 5})).json()
            assert page["total"] == 7
            assert [row["thread"]["thread_id"] for row in page["rows"]] == [
                thread["thread_id"] for thread in reversed(threads[2:])
            ]
            older = (
                await client.get("/api/threads/activity", params={**filters, "limit": 5, "cursor": page["next_cursor"]})
            ).json()
            assert [row["thread"]["thread_id"] for row in older["rows"]] == [
                thread["thread_id"] for thread in reversed(threads[:2])
            ]
            assert older["next_cursor"] is None
            mismatch = await client.get("/api/threads/activity", params={"cursor": page["next_cursor"]})
            assert mismatch.status_code == 400
            assert mismatch.json()["error"]["code"] == "thread_cursor_mismatch"

        all_threads = (await client.get("/api/threads/activity")).json()
        assert all_threads["total"] == 21
        search = (await client.get("/api/threads/activity", params={"query": "Task 0"})).json()
        assert search["total"] == 3
        invalid = await client.get(
            "/api/threads/activity", params={"project_id": "project-first", "project_scope": "projectless"}
        )
        assert invalid.status_code == 400
        assert (await client.get("/api/threads/activity", params={"project_scope": "unknown"})).status_code == 422

        # Metadata changes retain position; only an explicit touch moves the row.
        oldest = groups["project-first"][0]
        renamed = await client.patch(
            f"/api/threads/{oldest['thread_id']}/metadata",
            json={"expected_version": oldest["metadata_version"], "patch": {"title": "Recently updated"}},
        )
        assert renamed.status_code == 200, renamed.text
        recent = (await client.get("/api/threads/activity", params={"project_id": "project-first"})).json()
        assert recent["rows"][0]["thread"]["thread_id"] == groups["project-first"][-1]["thread_id"]
        assert (await client.post(f"/api/threads/{oldest['thread_id']}/touch")).status_code == 200
        recent = (await client.get("/api/threads/activity", params={"project_id": "project-first"})).json()
        assert recent["rows"][0]["thread"]["thread_id"] == oldest["thread_id"]
        assert recent["rows"][0]["thread"]["configuration"] == oldest["configuration"]

        # Deleted Projects remain navigable and never become projectless.
        deleted = await client.delete("/api/configuration/sources/projects/project-second.yaml")
        assert deleted.status_code == 200, deleted.text
        missing = (
            await client.get("/api/threads/activity", params={"project_scope": "unavailable", "limit": 5})
        ).json()
        assert missing["total"] == 7
        assert all(row["thread"]["configuration"]["project_id"] == "project-second" for row in missing["rows"])
        missing_next = (
            await client.get(
                "/api/threads/activity",
                params={"project_scope": "unavailable", "limit": 5, "cursor": missing["next_cursor"]},
            )
        ).json()
        assert len(missing_next["rows"]) == 2
        explicit = await client.get("/api/threads/activity", params={"project_id": "project-second"})
        assert explicit.status_code == 200
        assert explicit.json()["total"] == 7
        projectless = (await client.get("/api/threads/activity", params={"project_scope": "projectless"})).json()
        assert projectless["total"] == 7
        for thread in groups["project-second"]:
            archived = await client.patch(
                f"/api/threads/{thread['thread_id']}/metadata",
                json={"expected_version": thread["metadata_version"], "patch": {"archived": True}},
            )
            assert archived.status_code == 200, archived.text
        assert (await client.get("/api/threads/activity", params={"project_scope": "unavailable"})).json()["total"] == 0
        archived_missing = (
            await client.get("/api/threads/activity", params={"project_scope": "unavailable", "include_archived": True})
        ).json()
        assert archived_missing["total"] == 7


async def test_many_project_filter_members_keep_cursors_bounded(tmp_path: Path) -> None:
    from a13n_harness_ui.errors import ThreadError
    from a13n_harness_ui.surfaces import NewThreadDefaults

    configuration = _write_configuration(tmp_path, projects=(("project-first", "First", tmp_path),))
    async with open_harness_ui_app(
        HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"), pricing_auto_update=False),
        configuration_path=configuration,
    ) as app:
        for index in range(7):
            await app.create_thread(defaults=NewThreadDefaults(project_id="project-first"), title=f"Task {index}")
        # The unavailable-project group may include arbitrarily many retained references.
        project_ids = ("project-first", *(f"project-{'x' * 80}-{index}" for index in range(100)))
        first = await app.list_threads(project_ids=project_ids, limit=5)
        assert first.next_cursor is not None
        assert len(first.next_cursor) < 2048
        second = await app.list_threads(project_ids=project_ids, limit=5, cursor=first.next_cursor)
        assert len(second.threads) == 2
        assert second.next_cursor is None
        with pytest.raises(ThreadError) as mismatch:
            await app.list_threads(project_ids=project_ids[:-1], cursor=first.next_cursor)
        assert mismatch.value.code == "thread_cursor_mismatch"
