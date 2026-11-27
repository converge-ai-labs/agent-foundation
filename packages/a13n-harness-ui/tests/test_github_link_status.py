from __future__ import annotations

import asyncio
import json
import subprocess

import httpx2
import pytest
from a13n_harness_ui import github_link_status as github

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def clear_token_env(monkeypatch):
    for key in github._TOKEN_ENV:
        monkeypatch.delenv(key, raising=False)


@pytest.mark.parametrize("failure", [403, 404, 429, "network"])
async def test_public_rest_reuses_status_and_keeps_stale_on_failure(monkeypatch, failure):
    now = 0.0
    responses = [200, failure]
    monkeypatch.setattr(github, "monotonic", lambda: now)

    def handle(request):
        assert str(request.url) == "https://api.github.com/repos/org/repo/pulls/4"
        assert "authorization" not in request.headers
        code = responses.pop(0)
        if code == "network":
            raise httpx2.ConnectError("offline", request=request)
        return httpx2.Response(code, json={"title": "A pull request", "state": "open", "draft": True})

    status = github.GitHubLinkStatus(share_computer=False)
    url = "https://github.com/org/repo/pull/4/files#diff-1"
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
        first = await status.status(client, url)
        assert first is not None and first.status == "draft" and first.title == "A pull request"
        assert await status.status(client, url) == first
        assert responses == [failure]
        now = 61.0
        assert await status.status(client, url) == first
        assert await status.status(client, url) == first
        assert responses == []


async def test_public_static_and_non_github_fallback(monkeypatch):
    calls = []

    async def gh(_host):
        pytest.fail("No Host CLI without computer sharing")

    monkeypatch.setattr(github, "_host_token", gh)

    def handle(request):
        calls.append(request.url.path)
        return httpx2.Response(403)

    statuses = github.GitHubLinkStatus(share_computer=False)
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
        assert await statuses.status(client, "https://gitlab.example/org/repo/issues/1") is None
        discussion = await statuses.status(client, "https://github.com/org/repo/discussions/2")
        issue = await statuses.status(client, "https://github.com/org/repo/issues/3")
    assert discussion is not None and discussion.number == 2 and discussion.status is None
    assert issue is not None and issue.number == 3 and issue.status is None
    assert calls == ["/repos/org/repo/issues/3"]


async def test_uses_host_scoped_gh_token_for_enterprise_graphql_and_expires_on_401(monkeypatch):
    calls = []
    monkeypatch.setenv("GH_TOKEN", "fixture-public-token")
    monkeypatch.setenv("GH_ENTERPRISE_TOKEN", "fixture-unscoped-token")
    monkeypatch.setattr(github.shutil, "which", lambda _: "gh")

    async def run(command, *, env, stderr, check):
        calls.append(command)
        assert not check and stderr == subprocess.DEVNULL
        assert github._TOKEN_ENV.isdisjoint(env)
        return subprocess.CompletedProcess(command, 0, b"fixture-host-token\n")

    monkeypatch.setattr(github, "run_process", run)

    def handle(request):
        if request.url.host == "api.github.com":
            assert request.headers["Authorization"] == "Bearer fixture-public-token"
            return httpx2.Response(200, json={"title": "Public", "state": "closed"})
        assert request.url.host == "git.corp.example"
        assert request.headers["Authorization"] == "Bearer fixture-host-token"
        if request.method == "GET":
            assert request.url.path == "/api/v3/repos/team/repo/pulls/7"
            return httpx2.Response(401)
        assert request.url.path == "/api/graphql"
        assert json.loads(request.content)["variables"] == {"owner": "team", "repo": "repo", "number": 6}
        return httpx2.Response(
            200,
            json={
                "data": {
                    "repository": {
                        "discussion": {
                            "title": "Question",
                            "closed": False,
                            "answerChosenAt": "2026-11-01T02:00:00Z",
                        }
                    }
                }
            },
        )

    statuses = github.GitHubLinkStatus(share_computer=True)
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
        public = await statuses.status(client, "https://github.com/team/repo/issues/1")
        discussion = await statuses.status(client, "https://git.corp.example/team/repo/discussions/6")
        assert public is not None and public.status == "closed"
        assert discussion is not None and discussion.status == "answered"
        pr = await statuses.status(client, "https://git.corp.example/team/repo/pull/7")
        assert pr is not None and pr.status is None
        await statuses.status(client, "https://git.corp.example/team/repo/discussions/6?other=1")
    assert calls == [["gh", "auth", "token", "--hostname", "git.corp.example"]]
    assert "git.corp.example" not in statuses._tokens


@pytest.mark.parametrize("host", ["api.github.com", "x.github.localhost", "api.org.ghe.com"])
async def test_github_cli_aliases_never_receive_credentials(monkeypatch, host):
    async def token(_host):
        pytest.fail("Alias must not be resolved through gh")

    def handle(_request):
        pytest.fail("Alias must not receive a request")

    monkeypatch.setattr(github, "_host_token", token)
    statuses = github.GitHubLinkStatus(share_computer=True)
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
        assert await statuses.status(client, f"https://{host}/org/repo/issues/3") is None


async def test_late_failure_preserves_concurrent_status():
    started = asyncio.Event()
    release = asyncio.Event()
    requests = 0

    async def handle(_request):
        nonlocal requests
        requests += 1
        if requests == 1:
            started.set()
            await release.wait()
            return httpx2.Response(429)
        return httpx2.Response(200, json={"title": "Merged now", "state": "closed", "merged": True})

    statuses = github.GitHubLinkStatus(share_computer=False)
    url = "https://github.com/org/repo/pull/3"
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
        slow = asyncio.create_task(statuses.status(client, url))
        try:
            await asyncio.wait_for(started.wait(), timeout=5)
            fresh = await statuses.status(client, url)
            assert fresh is not None and fresh.status == "merged"
        finally:
            release.set()
            late = await slow
        assert late == fresh
        assert await statuses.status(client, url) == fresh
        assert requests == 2
