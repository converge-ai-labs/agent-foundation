"""Update notice sources, shared cache, and owner read boundary."""

import asyncio
import json
import time
from collections.abc import Callable
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import httpx2
import pytest
from a13n_harness_ui import update_notices, updater, webui
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.cli import cli
from a13n_harness_ui.interactive.updates import AvailableUpdate, check_update
from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings
from click.testing import CliRunner

pytestmark = pytest.mark.anyio

REPOSITORY_PATH = "/repos/converge-ai-labs/agent-foundation"
RELEASE_URL = "https://github.com/converge-ai-labs/agent-foundation/releases/tag/release/a13n-harness-ui-v1.10.0"
ADVISORY_ID = "GHSA-cfff-gggg-hhhh"
ADVISORY_URL = f"https://github.com/converge-ai-labs/agent-foundation/security/advisories/{ADVISORY_ID}"
AUTH = {"Authorization": "Bearer owner-test"}


@pytest.fixture
def upstream(monkeypatch: pytest.MonkeyPatch):
    client = httpx2.AsyncClient

    def install(handler: Callable):
        def mock_client(**kwargs):
            return client(transport=httpx2.MockTransport(handler), **kwargs)

        monkeypatch.setattr(update_notices, "AsyncClient", mock_client)

    return install


def assert_public_request(request: httpx2.Request) -> None:
    assert request.method == "GET"
    assert request.headers["user-agent"] == "a13n-harness-ui"
    assert not {"authorization", "cookie", "traceparent", "baggage"}.intersection(request.headers.keys())
    assert "1.9.0" not in str(request.url)


def release_response(request: httpx2.Request) -> httpx2.Response:
    assert_public_request(request)
    if request.url.host == "pypi.org":
        assert request.url.path == "/simple/a13n-harness-ui/"
        assert request.headers["accept"] == "application/vnd.pypi.simple.v1+json"
        return httpx2.Response(
            200,
            json={
                "files": [
                    {"filename": "a13n_harness_ui-1.8.0-py3-none-any.whl", "yanked": False},
                    {"filename": "a13n_harness_ui-1.10.0-py3-none-any.whl", "yanked": False},
                    {"filename": "a13n_harness_ui-1.10.1.tar.gz", "yanked": False},
                    {"filename": "a13n_harness_ui-1.10.2.tar.gz", "yanked": False},
                    {"filename": "a13n_harness_ui-1.11.0.tar.gz", "yanked": "Replaced"},
                    {"filename": "a13n_harness_ui-2.0.0rc1.tar.gz", "yanked": False},
                    {"filename": "a13n_harness_ui-2.0.0.dev1.tar.gz", "yanked": False},
                    {"filename": "a13n_service-2.0.0.tar.gz", "yanked": False},
                ]
            },
        )
    assert request.url.host == "api.github.com"
    tag = request.url.path.removeprefix(f"{REPOSITORY_PATH}/releases/tags/")
    if tag == "release/a13n-harness-ui-v1.10.2":
        return httpx2.Response(404)
    if tag == "release/a13n-harness-ui-v1.10.1":
        return httpx2.Response(200, json={"tag_name": tag, "draft": True, "prerelease": False})
    assert tag == "release/a13n-harness-ui-v1.10.0"
    return httpx2.Response(200, json={"tag_name": tag, "draft": False, "prerelease": False})


def advisory(bounds=" >= 1.0.0, < 1.10.0 ", *, package="a13n_harness_ui", ecosystem="pip", **fields):
    return {
        "ghsa_id": ADVISORY_ID,
        "state": "published",
        "summary": "Security update <strong>available</strong>",
        "severity": "high",
        "updated_at": "2026-10-05T12:00:00Z",
        "html_url": "https://example.invalid/advisory",
        "vulnerabilities": [{"package": {"name": package, "ecosystem": ecosystem}, "vulnerable_version_range": bounds}],
        **fields,
    }


@pytest.mark.parametrize(("current", "is_newer"), [("1.9.0", True), ("1.10.0", False), ("1.11.0", False)])
async def test_only_published_stable_releases_are_candidates(tmp_path, upstream, current, is_newer):
    upstream(release_response)
    release = await update_notices.UpdateChecker(tmp_path, current).check_release()
    assert release == update_notices.StableRelease(version="1.10.0", url=RELEASE_URL, is_newer=is_newer)


async def test_advisories_match_only_this_package_and_follow_only_the_cursor(tmp_path, upstream):
    def respond(request: httpx2.Request) -> httpx2.Response:
        assert_public_request(request)
        assert request.url.host == "api.github.com"
        assert request.url.path == f"{REPOSITORY_PATH}/security-advisories"
        assert request.url.params["state"] == "published"
        assert request.url.params["per_page"] == "100"
        if request.url.params.get("after") == "second":
            return httpx2.Response(200, json=[advisory("= 1.9.0", severity="critical", ghsa_id="GHSA-2222-3333-4444")])
        return httpx2.Response(
            200,
            headers={"Link": '<https://example.invalid/other?state=draft&after=second>; rel="next"'},
            json=[
                advisory(">1.9.0"),
                advisory(package="a13n-service"),
                advisory(ecosystem="npm"),
                advisory("not a range"),
                advisory(state="draft"),
                advisory(severity="medium"),
                advisory(withdrawn_at="2026-10-09T12:00:00Z"),
                advisory(ghsa_id="GHSA-../../other"),
                advisory(),
            ],
        )

    upstream(respond)
    checker = update_notices.UpdateChecker(tmp_path, "1.9.0")
    assert await checker._refresh("advisories")
    first, second = checker.snapshot.advisories
    assert first.id == ADVISORY_ID and first.severity == "high"
    assert first.url == ADVISORY_URL
    assert first.summary == "Security update <strong>available</strong>"
    assert second.id == "GHSA-2222-3333-4444" and second.severity == "critical"


async def test_cache_survives_restart_and_rechecks_advisories_on_version_change(tmp_path, upstream):
    requests = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request.url.path)
        if request.url.path.endswith("/security-advisories"):
            return httpx2.Response(200, json=[advisory()])
        return release_response(request)

    upstream(respond)
    checker = update_notices.UpdateChecker(tmp_path, "1.9.0")
    assert await asyncio.gather(checker._refresh("latest_stable"), checker._refresh("advisories")) == [True, True]
    requests.clear()
    restarted = update_notices.UpdateChecker(tmp_path, "1.9.0")
    await restarted.load()
    assert restarted.snapshot == checker.snapshot
    assert 0 < restarted._remaining_delay("latest_stable") <= 86400
    assert 0 < restarted._remaining_delay("advisories") <= 86400
    assert await check_update(tmp_path, current="1.9.0") == AvailableUpdate("1.9.0", "1.10.0")
    assert not requests

    upgraded = update_notices.UpdateChecker(tmp_path, "1.10.0")
    await upgraded.load()
    assert upgraded.snapshot.latest_stable is not None and not upgraded.snapshot.latest_stable.is_newer
    assert not upgraded.snapshot.advisories
    assert upgraded._remaining_delay("latest_stable") > 0
    assert upgraded._remaining_delay("advisories") == 0


async def test_failed_source_retries_without_changing_successful_snapshot(tmp_path, upstream, monkeypatch):
    release_retries = 0
    successful_advisories = asyncio.Event()
    release_retried = asyncio.Event()
    original_refresh = update_notices.UpdateChecker._refresh

    async def refresh(self, source):
        nonlocal release_retries
        success = await original_refresh(self, source)
        if source == "advisories":
            assert success
            successful_advisories.set()
        else:
            assert not success
            release_retries += 1
            if release_retries == 2:
                release_retried.set()
        return success

    def respond(request):
        if request.url.host == "pypi.org":
            return httpx2.Response(503)
        return httpx2.Response(200, json=[advisory()])

    checker = update_notices.UpdateChecker(tmp_path, "1.9.0")
    write_cached_release(checker.path, checked_at=time.time() - 86401)
    await checker.load()
    monkeypatch.setattr(update_notices, "_RETRY_SECONDS", 0.01)
    monkeypatch.setattr(update_notices.UpdateChecker, "_refresh", refresh)
    upstream(respond)
    task = asyncio.create_task(checker.run())
    try:
        async with asyncio.timeout(2):
            await successful_advisories.wait()
            await release_retried.wait()
        assert checker.snapshot.latest_stable == update_notices.StableRelease(
            version="1.10.0", url=RELEASE_URL, is_newer=True
        )
        assert checker.snapshot.advisories[0].id == ADVISORY_ID
        assert checker._remaining_delay("latest_stable") == 0
        assert checker._remaining_delay("advisories") > 0
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_release_poll_adopts_shared_success_during_retry(tmp_path, upstream, monkeypatch):
    web = update_notices.UpdateChecker(tmp_path, "1.9.0")
    write_cached_release(web.path, checked_at=time.time() - 86401)
    await web.load()
    failing_check = asyncio.Event()
    terminal_finished = asyncio.Event()
    adopted_release = asyncio.Event()
    remaining_delay = web._remaining_delay

    async def refresh(source):
        assert source == "latest_stable"
        assert not failing_check.is_set(), "A fresh terminal result should prevent another request"
        failing_check.set()
        await terminal_finished.wait()
        return False

    def delay(source):
        remaining = remaining_delay(source)
        if remaining > 0:
            adopted_release.set()
        return remaining

    monkeypatch.setattr(web, "_refresh", refresh)
    monkeypatch.setattr(web, "_remaining_delay", delay)
    monkeypatch.setattr(update_notices, "_RETRY_SECONDS", 0.01)
    upstream(release_response)
    task = asyncio.create_task(web._poll("latest_stable"))
    try:
        async with asyncio.timeout(2):
            await failing_check.wait()
            assert await check_update(tmp_path, current="1.9.0") == AvailableUpdate("1.9.0", "1.10.0")
            terminal_finished.set()
            await adopted_release.wait()
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_terminal_keeps_the_last_verified_release_on_fetch_failure(tmp_path, upstream):
    checker = update_notices.UpdateChecker(tmp_path, "1.9.0")
    write_cached_release(checker.path, checked_at=time.time() - 86401)
    upstream(lambda _request: httpx2.Response(503))
    assert await check_update(tmp_path, current="1.9.0") == AvailableUpdate("1.9.0", "1.10.0")


@pytest.mark.parametrize("current", ["0.0.0", "1.9.0rc1", "1.9.0.dev1"])
async def test_source_and_preview_builds_skip_network(tmp_path, upstream, current):
    upstream(lambda request: pytest.fail(f"Unexpected check for {request.url}"))
    checker = update_notices.UpdateChecker(tmp_path, current)
    assert await checker.check_release() is None
    await checker.run()
    assert checker.snapshot.current_version == current
    assert checker.snapshot.latest_stable is None and not checker.snapshot.advisories


async def test_fetch_deadline_and_response_size_are_bounded(tmp_path, upstream):
    checker = update_notices.UpdateChecker(tmp_path, "1.9.0")
    upstream(lambda _request: httpx2.Response(200, content=b" " * (2 * 1024 * 1024) + b'{"files": []}'))
    assert not await checker._refresh("latest_stable")

    async def hang(_request):
        await asyncio.Event().wait()

    upstream(hang)
    assert not await checker._refresh("latest_stable", timeout=0.01)
    assert not checker.path.exists()


def write_cached_release(path: Path, *, checked_at: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"latest_stable": {"version": "1.10.0", "checked_at": checked_at}}), encoding="utf-8")


@pytest.mark.parametrize("disabled_by", [None, "configuration", "invocation"])
async def test_webui_reads_owner_snapshot_without_waiting_for_network(tmp_path, monkeypatch, upstream, disabled_by):
    data = tmp_path / "data"
    write_cached_release(data / "cache" / "update-notices.json", checked_at=time.time())
    config = tmp_path / "a13n-harness-ui.yaml"
    config.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "process": {"update_check": disabled_by != "configuration", "terminal_update_check": False},
            }
        ),
        encoding="utf-8",
    )
    requests = []
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def wait_for_shutdown(request: httpx2.Request) -> httpx2.Response:
        requests.append(request.url.path)
        started.set()
        try:
            await asyncio.Future()
        finally:
            cancelled.set()
        raise AssertionError("The request should have been cancelled")

    upstream(wait_for_shutdown)
    monkeypatch.setattr(webui, "__version__", "1.9.0")
    monkeypatch.setattr(updater, "update_command", lambda: updater.UpdateCommand("uv", tmp_path))
    settings = HarnessUiSettings(storage=StorageSettings(data_root=data), pricing_auto_update=False)
    server = webui.create_webui(
        lambda: open_harness_ui_app(settings, configuration_path=config, host_mode="webui", instrumentation=None),
        api_key="owner-test",
        no_update_check=disabled_by == "invocation",
    )
    async with server.router.lifespan_context(server):
        if disabled_by is None:
            await asyncio.wait_for(started.wait(), 2)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server), base_url="http://localhost") as client:
            assert (await client.get("/api/update-notices")).status_code == 401
            response = await client.get("/api/update-notices", headers=AUTH)
            assert response.status_code == 200
            assert response.headers["cache-control"] == "no-store"
            assert response.json() == {
                "current_version": "1.9.0",
                "latest_stable": None if disabled_by else {"version": "1.10.0", "url": RELEASE_URL, "is_newer": True},
                "advisories": [],
                "install_method": "uv_tool",
            }
        assert requests == ([] if disabled_by else [f"{REPOSITORY_PATH}/security-advisories"])
    assert cancelled.is_set() == (disabled_by is None)


def test_cli_propagates_no_update_check_to_webui(tmp_path, monkeypatch):
    run = AsyncMock()
    monkeypatch.setattr(webui, "run", run)
    result = CliRunner().invoke(cli, ["--config", str(tmp_path / "settings.yaml"), "--no-update-check", "webui"])
    assert result.exit_code == 0, result.output
    assert run.await_args.kwargs["no_update_check"] is True
