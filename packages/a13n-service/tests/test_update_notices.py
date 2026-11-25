import asyncio
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from importlib.metadata import version
from types import SimpleNamespace

import httpx2
import pytest
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from a13n_service import update_notices
from a13n_service.settings import load_settings

from .test_tenancy import join, new_client

pytestmark = pytest.mark.anyio

RELEASES = {
    "files": [
        {"filename": "a13n_service-1.9.0-py3-none-any.whl", "yanked": False},
        {"filename": "a13n_service-1.10.0-py3-none-any.whl", "yanked": "replaced by a corrected wheel"},
        {"filename": "a13n_service-1.10.0.tar.gz", "yanked": False},
        {"filename": "a13n_service-1.11.0.tar.gz", "yanked": True},
        {"filename": "a13n_service-1.10.1.tar.gz", "yanked": False},
        {"filename": "a13n_service-2.0.0rc1-py3-none-any.whl", "yanked": False},
        {"filename": "a13n_service-2.0.0.dev1.tar.gz", "yanked": False},
        {"filename": "a13n_service-0.0.0.tar.gz", "yanked": False},
    ],
}


async def refresh(checker: update_notices.UpdateChecker) -> tuple[bool, bool]:
    policy = EndpointPolicy()
    return (
        await checker.refresh(policy, "latest_stable", update_notices._release),
        await checker.refresh(policy, "advisories", update_notices._advisories),
    )


def release_response(request: httpx2.Request) -> httpx2.Response | None:
    if request.url.host == "pypi.org":
        assert request.url.path == "/simple/a13n-service/"
        assert request.headers["accept"] == "application/vnd.pypi.simple.v1+json"
        return httpx2.Response(200, json=RELEASES)
    tag = request.url.path.removeprefix("/repos/converge-ai-labs/agent-foundation/releases/tags/")
    if tag == request.url.path:
        return None
    if tag == "release/a13n-service-v1.10.1":
        return httpx2.Response(404)
    assert tag == "release/a13n-service-v1.10.0"
    return httpx2.Response(200, json={"tag_name": tag, "draft": False, "prerelease": False})


def notice(bounds=" >= 1.0.0, < 1.10.0 ", *, package="a13n_service", ecosystem="pip", **fields):
    return {
        "ghsa_id": "GHSA-cfff-gggg-hhhh",
        "state": "published",
        "summary": "Security update <strong>available</strong>",
        "severity": "high",
        "html_url": "https://example.invalid/advisory",
        "vulnerabilities": [{"package": {"name": package, "ecosystem": ecosystem}, "vulnerable_version_range": bounds}],
        **fields,
    }


@pytest.fixture
def upstream(monkeypatch: pytest.MonkeyPatch):
    def install(handler):
        @asynccontextmanager
        async def open_http(_policy, **_kwargs) -> AsyncIterator[httpx2.AsyncClient]:
            async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
                yield client

        monkeypatch.setattr(update_notices, "open_http", open_http)

    return install


@pytest.mark.parametrize(("installed", "newer"), [("1.9.0", True), ("1.10.0", False), ("1.11.0", False)])
async def test_release_orders_published_stable_versions(upstream, installed, newer):
    upstream(lambda request: release_response(request) or httpx2.Response(200, json=[]))
    checker = update_notices.UpdateChecker(installed)
    await refresh(checker)
    release = checker.snapshot.latest_stable
    assert release is not None
    assert release.version == "1.10.0" and release.is_newer is newer
    assert (
        release.url == "https://github.com/converge-ai-labs/agent-foundation/releases/tag/release/a13n-service-v1.10.0"
    )


async def test_matches_only_published_high_or_critical_service_ranges(upstream):
    def respond(request: httpx2.Request) -> httpx2.Response:
        assert request.method == "GET"
        assert not {"authorization", "cookie", "traceparent", "baggage"}.intersection(request.headers.keys())
        assert "1.9.0" not in str(request.url)
        if (release := release_response(request)) is not None:
            return release
        assert request.url.host == "api.github.com"
        if request.url.params.get("after") == "page-two":
            return httpx2.Response(200, json=[notice("= 1.9.0", severity="critical", ghsa_id="GHSA-2222-3333-4444")])
        return httpx2.Response(
            200,
            headers={"Link": '<https://api.github.com/test?after=page-two>; rel="next"'},
            json=[
                notice("> 1.9.0"),
                notice(package="a13n-harness"),
                notice(ecosystem="npm"),
                notice("not a range"),
                notice(None),
                notice(state="draft"),
                notice(severity="medium"),
                notice(withdrawn_at="2026-01-01T00:00:00Z"),
                notice(ghsa_id="GHSA-../../evil"),
                notice(),
            ],
        )

    upstream(respond)
    checker = update_notices.UpdateChecker("1.9.0")
    await refresh(checker)
    assert [(item.id, item.severity) for item in checker.snapshot.advisories] == [
        ("GHSA-cfff-gggg-hhhh", "high"),
        ("GHSA-2222-3333-4444", "critical"),
    ]
    item = checker.snapshot.advisories[0]
    assert item.summary == "Security update <strong>available</strong>"
    assert item.url == "https://github.com/converge-ai-labs/agent-foundation/security/advisories/GHSA-cfff-gggg-hhhh"


@pytest.mark.parametrize("failed_host", ["pypi.org", "api.github.com"])
@pytest.mark.parametrize("failure", ["rate-limit", "offline", "timeout", "invalid-json"])
async def test_upstream_failures_are_independent(upstream, monkeypatch, failed_host, failure):
    monkeypatch.setattr(update_notices, "_FETCH_SECONDS", 0.1)

    fail = True

    async def respond(request: httpx2.Request) -> httpx2.Response:
        if fail and request.url.host == failed_host and "/releases/tags/" not in request.url.path:
            if failure == "offline":
                raise httpx2.ConnectError("offline")
            if failure == "timeout":
                await asyncio.sleep(10)
            if failure == "invalid-json":
                return httpx2.Response(200, content=b"bad JSON")
            return httpx2.Response(429)
        return release_response(request) or httpx2.Response(200, json=[notice()])

    upstream(respond)
    checker = update_notices.UpdateChecker("1.9.0")
    assert await refresh(checker) == (failed_host != "pypi.org", failed_host != "api.github.com")
    assert (checker.snapshot.latest_stable is None) == (failed_host == "pypi.org")
    assert bool(checker.snapshot.advisories) == (failed_host != "api.github.com")

    fail = False
    assert await refresh(checker) == (True, True)
    confirmed = checker.snapshot
    assert confirmed.latest_stable and confirmed.advisories
    fail = True
    assert await refresh(checker) == (failed_host != "pypi.org", failed_host != "api.github.com")
    assert checker.snapshot == confirmed

    # A successful empty response clears the source's old result.
    upstream(lambda request: httpx2.Response(200, json={"files": []} if request.url.host == "pypi.org" else []))
    assert await refresh(checker) == (True, True)
    assert checker.snapshot.latest_stable is None and not checker.snapshot.advisories


@pytest.mark.parametrize("installed", ["0.0.0", "1.9.0rc1", "1.9.0.dev1"])
async def test_source_and_prerelease_builds_do_not_fetch(upstream, installed):
    def unexpected(request):
        pytest.fail(f"Unexpected request to {request.url}")

    upstream(unexpected)
    checker = update_notices.UpdateChecker(installed)
    await checker.run(EndpointPolicy())
    assert checker.snapshot.latest_stable is None and not checker.snapshot.advisories


def test_disable_with_environment(monkeypatch: pytest.MonkeyPatch, clean_environment: None) -> None:
    monkeypatch.setenv("A13N_CONTROL__UPDATE_NOTICES_ENABLED", "false")
    assert not load_settings().control.update_notices_enabled


async def test_read_is_only_for_organization_administrators(service: SimpleNamespace) -> None:
    path = f"{service.organization}/update-notices"
    reply = await service.client.get(path)
    assert reply.status_code == 200
    assert reply.headers["cache-control"] == "no-store"
    assert reply.json() == {"current_version": version("a13n-service"), "latest_stable": None, "advisories": []}
    async with AsyncExitStack() as stack:
        unauthenticated = await stack.enter_async_context(new_client(service))
        workspace_admin = await join(service, stack, "workspace@example.com", "admin")
        member = await join(service, stack, "member@example.com", "viewer", scope=service.organization)
        assert (await unauthenticated.get(path)).status_code == 401
        assert (await workspace_admin.get(path)).status_code == 403
        assert (await member.get(path)).status_code == 403
