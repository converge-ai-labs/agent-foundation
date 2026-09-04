from __future__ import annotations

import base64
import json
from collections.abc import Callable
from datetime import datetime, timedelta

import anyio
import httpx2
import pytest
from a13n_service.connectivity.providers.github.actions import (
    GitHubActionBinding,
    GitHubAddCommentArguments,
    GitHubAddCommentOutcomeUnknown,
    GitHubAddCommentSucceeded,
    GitHubListPrFilesArguments,
    GitHubReadCommentsArguments,
    GitHubReadTargetArguments,
)
from a13n_service.connectivity.providers.github.api import GitHubApiError
from a13n_service.connectivity.providers.github.client import GitHubNativeClient
from a13n_service.connectivity.providers.github.token import (
    GITHUB_API_VERSION,
    GitHubInstallationTokenProvider,
    load_github_private_key,
)
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from pydantic import ValidationError

from .conftest import NOW


class _AllowEndpoint:
    def __init__(self) -> None:
        self.calls = 0

    async def validate(self, endpoint: str, *, resolve_dns: bool = True) -> str:
        assert resolve_dns is True
        self.calls += 1
        return endpoint.rstrip("/")


def _binding(*, target_kind: str = "pull_request") -> GitHubActionBinding:
    return GitHubActionBinding.model_validate(
        {
            "repository_id": 42,
            "owner": "acme",
            "repository": "repo",
            "number": 7,
            "target_kind": target_kind,
        }
    )


def _token_response(*, now: datetime = NOW) -> httpx2.Response:
    return httpx2.Response(
        201,
        json={
            "token": "ghs_1234567890_new_format",
            "expires_at": (now + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
        },
    )


def _clients(
    http_client: httpx2.AsyncClient,
    endpoint: _AllowEndpoint,
    private_key: str,
    *,
    clock: Callable[[], datetime] = lambda: NOW,
    response_max_bytes: int = 2 * 1024 * 1024,
) -> tuple[GitHubNativeClient, GitHubInstallationTokenProvider]:
    tokens = GitHubInstallationTokenProvider(
        http_client,
        endpoint,
        api_origin="https://api.github.com",
        app_id=123,
        installation_id=456,
        private_key_pem=private_key,
        permissions={"issues": "write", "pull_requests": "write"},
        clock=clock,
    )
    return (
        GitHubNativeClient(
            http_client,
            endpoint,
            tokens,
            api_origin="https://api.github.com",
            web_origin="https://github.com",
            response_max_bytes=response_max_bytes,
        ),
        tokens,
    )


@pytest.mark.anyio
async def test_github_app_jwt_and_repository_token_are_single_flight(
    github_private_key_pem: str,
) -> None:
    requests: list[httpx2.Request] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return _token_response()

    endpoint = _AllowEndpoint()
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http_client:
        _native, provider = _clients(http_client, endpoint, github_private_key_pem)
        tokens: list[str] = []

        async def retrieve() -> None:
            tokens.append(await provider.token(repository_id=42))

        async with anyio.create_task_group() as tasks:
            for _ in range(8):
                tasks.start_soon(retrieve)

    assert tokens == ["ghs_1234567890_new_format"] * 8
    assert len(requests) == 1
    request = requests[0]
    assert request.url.path == "/app/installations/456/access_tokens"
    assert request.headers["x-github-api-version"] == GITHUB_API_VERSION
    assert json.loads(request.content) == {
        "repository_ids": [42],
        "permissions": {"issues": "write", "pull_requests": "write"},
    }
    jwt = request.headers["authorization"].removeprefix("Bearer ")
    header, payload, signature = jwt.split(".")
    assert json.loads(_decode_base64url(header)) == {"alg": "RS256", "typ": "JWT"}
    claims = json.loads(_decode_base64url(payload))
    assert claims["iss"] == "123"
    assert claims["exp"] - claims["iat"] == 600
    load_github_private_key(github_private_key_pem).public_key().verify(
        _decode_base64url(signature),
        f"{header}.{payload}".encode(),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    assert "ghs_1234567890_new_format" not in repr(provider)
    assert github_private_key_pem not in repr(provider)


@pytest.mark.anyio
async def test_github_token_refresh_failure_keeps_still_valid_token(
    github_private_key_pem: str,
) -> None:
    now = [NOW]
    responses = iter(
        (
            _token_response(),
            httpx2.Response(503, json={"message": "unavailable"}),
            httpx2.Response(503, json={"message": "unavailable"}),
        )
    )
    endpoint = _AllowEndpoint()
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(lambda _request: next(responses))) as http_client:
        _native, provider = _clients(
            http_client,
            endpoint,
            github_private_key_pem,
            clock=lambda: now[0],
        )
        assert await provider.token(repository_id=42) == "ghs_1234567890_new_format"
        now[0] = NOW + timedelta(minutes=59, seconds=30)
        assert await provider.token(repository_id=42) == "ghs_1234567890_new_format"
        now[0] = NOW + timedelta(hours=1, seconds=1)
        with pytest.raises(GitHubApiError, match="provider_unavailable"):
            await provider.token(repository_id=42)


@pytest.mark.anyio
async def test_github_repository_token_cache_is_bounded(github_private_key_pem: str) -> None:
    requests: list[httpx2.Request] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return _token_response()

    endpoint = _AllowEndpoint()
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http_client:
        provider = GitHubInstallationTokenProvider(
            http_client,
            endpoint,
            api_origin="https://api.github.com",
            app_id=123,
            installation_id=456,
            private_key_pem=github_private_key_pem,
            permissions={"issues": "write"},
            max_cached_repositories=1,
            clock=lambda: NOW,
        )
        await provider.token(repository_id=1)
        await provider.token(repository_id=2)
        await provider.token(repository_id=1)

    assert [json.loads(request.content)["repository_ids"] for request in requests] == [[1], [2], [1]]


@pytest.mark.anyio
async def test_github_add_comment_uses_issue_endpoint_for_pull_request(
    github_private_key_pem: str,
) -> None:
    requests: list[httpx2.Request] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if request.url.path.endswith("access_tokens"):
            return _token_response()
        return httpx2.Response(
            201,
            json={
                "id": 1001,
                "node_id": "IC_1",
                "html_url": "https://github.com/acme/repo/pull/7#issuecomment-1001",
            },
        )

    endpoint = _AllowEndpoint()
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http_client:
        client, _tokens = _clients(http_client, endpoint, github_private_key_pem)
        outcome = await client.add_comment(
            _binding(),
            GitHubAddCommentArguments(body="hello"),
            request_id="req-1",
        )

    assert isinstance(outcome, GitHubAddCommentSucceeded)
    assert requests[1].url.path == "/repos/acme/repo/issues/7/comments"
    assert json.loads(requests[1].content) == {"body": "hello"}
    assert requests[1].headers["authorization"] == "Bearer ghs_1234567890_new_format"


def test_github_model_arguments_cannot_select_current_target() -> None:
    schemas = json.dumps(
        {
            "add": GitHubAddCommentArguments.model_json_schema(),
            "comments": GitHubReadCommentsArguments.model_json_schema(),
            "target": GitHubReadTargetArguments.model_json_schema(),
            "files": GitHubListPrFilesArguments.model_json_schema(),
        },
        sort_keys=True,
    )

    for forbidden in (
        "installation",
        "owner",
        "repository_id",
        "repository",
        "issue",
        "pull_request",
        "token",
        "endpoint",
        "ingress",
    ):
        assert forbidden not in schemas
    with pytest.raises(ValidationError):
        GitHubAddCommentArguments.model_validate({"body": "hello", "owner": "other"})
    binding_repr = repr(_binding())
    for protected in ("42", "acme", "repo", "7"):
        assert protected not in binding_repr


@pytest.mark.anyio
@pytest.mark.parametrize("response", [httpx2.Response(503), httpx2.Response(201, content=b"bad-json")])
async def test_github_add_comment_reports_unknown_after_ambiguous_response(
    response: httpx2.Response,
    github_private_key_pem: str,
) -> None:
    responses = iter((_token_response(), response))
    endpoint = _AllowEndpoint()
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(lambda _request: next(responses))) as http_client:
        client, _tokens = _clients(http_client, endpoint, github_private_key_pem)
        outcome = await client.add_comment(
            _binding(),
            GitHubAddCommentArguments(body="hello"),
            request_id="req-unknown",
        )

    assert outcome == GitHubAddCommentOutcomeUnknown(request_id="req-unknown")


@pytest.mark.anyio
async def test_github_token_failure_before_dispatch_is_not_unknown(
    github_private_key_pem: str,
) -> None:
    endpoint = _AllowEndpoint()
    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda _request: httpx2.Response(503, json={"message": "no"}))
    ) as http_client:
        client, _tokens = _clients(http_client, endpoint, github_private_key_pem)
        with pytest.raises(GitHubApiError, match="provider_unavailable"):
            await client.add_comment(
                _binding(),
                GitHubAddCommentArguments(body="hello"),
                request_id="req-pre-dispatch",
            )


@pytest.mark.anyio
async def test_github_reads_surface_rate_limits_and_response_bounds(
    github_private_key_pem: str,
) -> None:
    responses = iter(
        (
            _token_response(),
            httpx2.Response(429, headers={"retry-after": "15"}, json={"message": "slow down"}),
        )
    )
    endpoint = _AllowEndpoint()
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(lambda _request: next(responses))) as http_client:
        client, _tokens = _clients(http_client, endpoint, github_private_key_pem)
        with pytest.raises(GitHubApiError) as raised:
            await client.read_comments(_binding(), GitHubReadCommentsArguments())
    assert raised.value.code == "rate_limited"
    assert raised.value.retry_after_seconds == 15

    responses = iter((_token_response(), httpx2.Response(200, content=b"x" * 65)))
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(lambda _request: next(responses))) as http_client:
        client, _tokens = _clients(
            http_client,
            endpoint,
            github_private_key_pem,
            response_max_bytes=64,
        )
        with pytest.raises(GitHubApiError, match="response_too_large"):
            await client.read_comments(_binding(), GitHubReadCommentsArguments())


@pytest.mark.anyio
async def test_github_reads_project_safe_fields_and_bound_patch_bytes(
    github_private_key_pem: str,
) -> None:
    requests: list[httpx2.Request] = []
    large_patch = "世" * 10_000
    file_items = [
        {
            "filename": f"file-{index}.py",
            "status": "modified",
            "additions": 1,
            "deletions": 1,
            "changes": 2,
            "patch": large_patch,
        }
        for index in range(5)
    ]
    responses = iter(
        (
            _token_response(),
            httpx2.Response(
                200,
                json=[
                    {
                        "id": 1001,
                        "node_id": "IC_1",
                        "user": {"id": 111, "login": "human", "email": "drop@example.com"},
                        "body": "comment",
                        "html_url": "https://github.com/acme/repo/issues/7#issuecomment-1001",
                        "created_at": "2026-09-03T08:00:00Z",
                        "updated_at": "2026-09-03T08:00:01Z",
                    }
                ],
            ),
            httpx2.Response(
                200,
                json={
                    "number": 7,
                    "title": "Title",
                    "body": "Body",
                    "state": "open",
                    "labels": [{"name": "ready"}],
                    "html_url": "https://github.com/acme/repo/pull/7",
                    "draft": False,
                    "merged": False,
                },
            ),
            httpx2.Response(200, json=file_items),
        )
    )

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return next(responses)

    endpoint = _AllowEndpoint()
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http_client:
        client, _tokens = _clients(http_client, endpoint, github_private_key_pem)
        comments = await client.read_comments(
            _binding(),
            GitHubReadCommentsArguments(per_page=2),
        )
        target = await client.read_issue_or_pr(_binding(), GitHubReadTargetArguments())
        files = await client.list_pr_files(
            _binding(),
            GitHubListPrFilesArguments(per_page=5),
        )

    assert comments.items[0].author_login == "human"
    assert "email" not in comments.model_dump_json()
    assert target.labels == ("ready",)
    assert files.patches_truncated is True
    assert sum(len(item.patch.encode()) for item in files.items if item.patch is not None) <= 64 * 1024
    assert all(item.patch_truncated for item in files.items)
    assert requests[1].url.path == "/repos/acme/repo/issues/7/comments"
    assert requests[3].url.path == "/repos/acme/repo/pulls/7/files"


@pytest.mark.anyio
async def test_github_rejects_wrong_receipt_origin_and_issue_file_action(
    github_private_key_pem: str,
) -> None:
    responses = iter(
        (
            _token_response(),
            httpx2.Response(
                201,
                json={"id": 1, "node_id": "IC_1", "html_url": "https://evil.example/comment"},
            ),
        )
    )
    endpoint = _AllowEndpoint()
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(lambda _request: next(responses))) as http_client:
        client, _tokens = _clients(http_client, endpoint, github_private_key_pem)
        outcome = await client.add_comment(
            _binding(),
            GitHubAddCommentArguments(body="hello"),
            request_id="req-origin",
        )
        with pytest.raises(GitHubApiError, match="action_not_available"):
            await client.list_pr_files(
                _binding(target_kind="issue"),
                GitHubListPrFilesArguments(),
            )

    assert outcome == GitHubAddCommentOutcomeUnknown(request_id="req-origin")


def _decode_base64url(value: str) -> bytes:
    padding_bytes = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding_bytes)
