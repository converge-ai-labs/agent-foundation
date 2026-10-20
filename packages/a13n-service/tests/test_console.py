"""API-serving roles answer browser paths from the bundled Console build and leave the API unchanged."""

from collections.abc import Callable
from pathlib import Path

import httpx2
import pytest
from a13n_service.settings import ProcessRole, Settings
from fastapi import FastAPI

pytestmark = pytest.mark.anyio

INDEX = "<!doctype html><title>a13n Console</title>"


@pytest.fixture
def console(tmp_path: Path) -> Path:
    root = tmp_path / "static"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text(INDEX)
    (root / "assets" / "index-3f2a.js").write_text("export {};")
    (tmp_path / "secret.txt").write_text("outside the build")
    return root


@pytest.fixture
def client_factory(service_app: Callable[..., FastAPI]) -> Callable[..., httpx2.AsyncClient]:
    def create(console: Path, role: ProcessRole = "all") -> httpx2.AsyncClient:
        app = service_app(role=role, settings=Settings(), console=console)
        return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://127.0.0.1:8000")

    return create


async def test_browser_paths_answer_the_uncached_index(
    console: Path, client_factory: Callable[..., httpx2.AsyncClient]
) -> None:
    async with client_factory(console) as client:
        for path in ("/", "/index.html", "/workspaces/ws_1/agents", "/login?next=%2F"):
            response = await client.get(path)
            assert response.status_code == 200, path
            assert response.text == INDEX
            assert response.headers["content-type"].startswith("text/html")
            assert response.headers["cache-control"] == "no-store"
            assert len(response.headers.get_list("x-request-id")) == 1
        head = await client.head("/workspaces")
        assert head.status_code == 200 and head.content == b""


async def test_assets_are_cached_for_good_and_confined_to_the_build(
    console: Path, client_factory: Callable[..., httpx2.AsyncClient]
) -> None:
    async with client_factory(console) as client:
        asset = await client.get("/assets/index-3f2a.js")
        assert asset.status_code == 200 and asset.text == "export {};"
        assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"
        for path in ("/assets/missing.js", "/assets/%2E%2E/%2E%2E/secret.txt", "/assets/%2E%2E/index.html"):
            missing = await client.get(path)
            assert missing.status_code == 404, path
            assert missing.json()["error"]["details"]["kind"] == "route"


async def test_api_answers_are_the_same_with_a_console(
    console: Path, tmp_path: Path, client_factory: Callable[..., httpx2.AsyncClient]
) -> None:
    async with client_factory(console) as bundled, client_factory(tmp_path / "absent") as bare:
        for method, path in (
            ("GET", "/api"),
            ("GET", "/api/v1/unknown"),
            ("POST", "/workspaces"),
            ("GET", "/api/v1/auth/login"),
            ("GET", "/healthz/"),
        ):
            with_console = await bundled.request(method, path)
            without = await bare.request(method, path)
            assert with_console.status_code == without.status_code, (method, path)
            assert with_console.headers.get("location") == without.headers.get("location")
        assert (await bundled.get("/api/v1/unknown")).json()["error"]["code"] == "not_found"
        assert (await bundled.get("/healthz/")).status_code == 307


async def test_only_api_serving_roles_with_a_build_serve_the_console(
    console: Path, tmp_path: Path, client_factory: Callable[..., httpx2.AsyncClient]
) -> None:
    async with client_factory(console, role="worker") as worker, client_factory(tmp_path / "absent") as bare:
        assert (await worker.get("/")).status_code == 404
        assert (await bare.get("/")).status_code == 404
    async with client_factory(console, role="control") as control:
        assert (await control.get("/")).text == INDEX
