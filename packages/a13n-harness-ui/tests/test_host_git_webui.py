from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.file_context import FileContextSource, GitContextSource
from a13n_harness_ui.thread_files import ThreadAttachment
from a13n_harness_ui.webui import create_webui

from .test_app import _CompletedReconstructor, _settings, _write_configuration
from .test_host_git import commit, git, repository


@pytest.mark.anyio
@pytest.mark.parametrize("key", [None, "key"])
async def test_git_disabled_is_backend_gate(tmp_path: Path, key: str | None) -> None:
    @asynccontextmanager
    async def factory():
        async with open_harness_ui_app(_settings(tmp_path / "state"), host_mode="webui") as app:
            yield app

    server = create_webui(factory, api_key=key)
    async with (
        server.router.lifespan_context(server),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=server), base_url="http://127.0.0.1") as client,
    ):
        if key:
            client.headers["Authorization"] = f"Bearer {key}"
        assert not (await client.get("/api/status")).json()["features"]["host_git"]
        for url in ("repository", "status", "diff"):
            params = {"path": str(tmp_path)} if url != "diff" else {"repository_path": str(tmp_path), "path": "file"}
            response = await client.get(f"/api/host/git/{url}", params=params)
            assert response.status_code == 403 and response.json()["error"]["code"] == "host_git_disabled"
        response = await client.post("/api/threads/missing/host-git-captures", content="invalid body")
        assert response.status_code == 403


@pytest.mark.anyio
async def test_git_http_shared_observation_and_retained_submission(tmp_path: Path) -> None:
    opened = []

    @asynccontextmanager
    async def factory():
        async with open_harness_ui_app(
            _settings(tmp_path / "state"),
            configuration_path=_write_configuration(tmp_path),
            host_mode="webui",
            share_computer=True,
        ) as app:
            app._root_runs._executor._agents = _CompletedReconstructor()
            opened.append(app)
            yield app

    root = repository(tmp_path / "repo")
    source = root / "file"
    source.write_text("baseline\n")
    commit(root)
    source.write_text("reviewed change\n")
    server = create_webui(factory, api_key="key")
    async with (
        server.router.lifespan_context(server),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=server), base_url="http://127.0.0.1") as first,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server),
            base_url="http://127.0.0.1",
            headers={"Authorization": "Bearer key"},
        ) as second,
    ):
        assert (await first.get("/api/host/git/status", params={"path": str(root)})).status_code == 401
        first.headers["Authorization"] = "Bearer key"
        assert (await first.get("/api/status")).json()["features"]["host_git"]
        found = await first.get("/api/host/git/repository", params={"path": str(source)})
        assert found.status_code == 200 and Path(found.json()["repository"]["root"]).samefile(root)
        status = await first.get("/api/host/git/status", params={"path": str(root)})
        assert status.status_code == 200 and status.json()["entries"][0]["worktree_status"] == "M"
        query = {"repository_path": str(root), "path": "file", "comparison": "unstaged"}
        preview = await first.get("/api/host/git/diff", params=query)
        assert preview.status_code == 200, preview.text
        assert preview.headers["cache-control"] == "no-store"
        assert (await second.get("/api/host/git/diff", params=query)).json() == preview.json()
        thread = await opened[0].create_thread()
        url = f"/api/threads/{thread.thread_id}/host-git-captures"
        capture = await first.post(url, json={**query, "expected_revision": preview.json()["revision"]})
        assert capture.status_code == 200, capture.text
        value = capture.json()
        assert "Selected Host Git diff context" in value["prompt_text"]
        assert value["attachment"]["source"]["kind"] == "git_diff"
        assert value["attachment"]["source"]["head_oid"] == git(root, "rev-parse", "HEAD")
        handle = value["attachment"]["attachment_id"]
        source.write_text("external replacement\n")
        stale = await second.post(url, json={**query, "expected_revision": preview.json()["revision"]})
        assert stale.status_code == 409
        changed = await second.get("/api/host/git/diff", params=query)
        assert changed.json()["revision"] != preview.json()["revision"]
        assert "+external replacement" in changed.json()["text"]
        assert (await first.post(url, json=query)).status_code == 400
        assert (
            await first.post(url, json={**query, "expected_revision": changed.json()["revision"], "start_line": 2})
        ).status_code == 400
        assert (
            await first.post("/api/operations/missing/steer", json={"prompt": "review", "attachment_ids": [handle]})
        ).status_code == 400
        submitted = await first.post(
            f"/api/threads/{thread.thread_id}/submit", json={"prompt": "review", "attachment_ids": [handle]}
        )
        assert submitted.status_code == 200, submitted.text
        await opened[0].wait_root_operation(submitted.json()["receipt_id"])
        transcript = (await opened[0].get_thread_transcript(thread_id=thread.thread_id)).model_dump_json()
        assert "reviewed change" in transcript and "external replacement" not in transcript
        assert "git_diff" in transcript and "unstaged" in transcript
        retained, data = await opened[0].read_thread_attachment(thread_id=thread.thread_id, attachment_id=handle)
        assert isinstance(retained.source, GitContextSource)
        assert data.decode() == preview.json()["text"]
        assert (await first.get(f"/api/threads/{thread.thread_id}/attachments/{handle}")).content == data
        # Existing file provenance remains readable with no newly required discriminator.
        legacy = retained.model_dump()
        legacy["source"] = {"location": "host", "path": str(source), "resolved_path": str(source), "revision": "old"}
        assert isinstance(ThreadAttachment.model_validate(legacy).source, FileContextSource)
        # Large text diffs use ordinary retained input rather than inflating the prompt.
        source.write_text("a" * (65 * 1024))
        large = (await first.get("/api/host/git/diff", params=query)).json()
        captured = await first.post(url, json={**query, "expected_revision": large["revision"]})
        assert captured.status_code == 200 and captured.json()["prompt_text"] is None
        source.write_bytes(b"\x00binary")
        binary = (await first.get("/api/host/git/diff", params=query)).json()
        rejected = await first.post(url, json={**query, "expected_revision": binary["revision"]})
        assert rejected.status_code == 400 and rejected.json()["error"]["code"] == "host_git_selection_invalid"


@pytest.mark.anyio
async def test_missing_git_does_not_disable_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    @asynccontextmanager
    async def factory():
        async with open_harness_ui_app(_settings(tmp_path / "state"), host_mode="webui", share_computer=True) as app:
            yield app

    server = create_webui(factory, api_key="key")
    async with (
        server.router.lifespan_context(server),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server),
            base_url="http://127.0.0.1",
            headers={"Authorization": "Bearer key"},
        ) as client,
    ):
        monkeypatch.setenv("PATH", str(tmp_path / "missing"))
        features = (await client.get("/api/status")).json()["features"]
        assert features["host_files"] and not features["host_git"]
        response = await client.get("/api/host/git/repository", params={"path": str(tmp_path)})
        assert response.status_code == 503 and response.json()["error"]["code"] == "host_git_unavailable"
        response = await client.get("/api/host/files", params={"path": str(tmp_path)})
        assert response.status_code == 200
