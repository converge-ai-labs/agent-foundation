"""The browser shell is served only for implemented route families."""

from pathlib import Path

import httpx
import pytest
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings
from a13n_harness_ui.webui import create_webui


@pytest.mark.anyio
async def test_workbench_deep_links_and_api_not_found_remain_distinct(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<!doctype html><title>Workbench test</title>")
    settings = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"), pricing_auto_update=False)
    server = create_webui(lambda: open_harness_ui_app(settings), api_key="test-only-key", static_root=tmp_path)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server), base_url="http://127.0.0.1") as client:
        for path in (
            "/",
            "/setup",
            "/settings",
            "/settings/resources",
            "/settings/source?path=models/test.yaml",
            "/settings/accounts",
            "/settings/catalog",
            "/settings/models",
            "/settings/notifications",
            "/settings/agents",
            "/settings/capabilities",
            "/settings/environments",
            "/settings/connections",
            "/archived",
            "/new",
            "/projects",
            "/projects/project-demo",
            "/threads/thread-demo",
        ):
            response = await client.get(path)
            assert response.status_code == 200, (path, response.text)
            assert "Workbench test" in response.text
            assert response.headers["cache-control"] == "no-cache"
            assert "script-src 'self'" in response.headers["content-security-policy"]
            assert "img-src 'self' data: blob:;" in response.headers["content-security-policy"]
            assert "media-src 'self' blob:;" in response.headers["content-security-policy"]
        for path in (
            "/new/draft-demo",
            "/unknown",
            "/settings/unknown",
            "/projects/project-demo/unknown",
            "/assets/missing.js",
        ):
            response = await client.get(path)
            assert response.status_code == 404
            assert response.json()["error"]["code"] == "not_found"
        assert (await client.get("/api/unknown")).status_code == 401
        response = await client.get("/api/unknown", headers={"Authorization": "Bearer test-only-key"})
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"


def test_obsolete_http_wrappers_are_not_part_of_bundled_frontend_contract(tmp_path: Path):
    settings = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"), pricing_auto_update=False)
    server = create_webui(lambda: open_harness_ui_app(settings), api_key="test-only-key")
    paths = server.openapi()["paths"]
    for path in (
        "/api/threads/preview",
        "/api/operations/{receipt_id}/configuration",
        "/api/threads/{thread_id}/notes",
        "/api/threads/{thread_id}/tasks",
        "/api/threads/{thread_id}/children/wait",
        "/api/threads/{thread_id}/children/{execution_id}/review",
        "/api/threads/{thread_id}/touch",
    ):
        assert path not in paths
