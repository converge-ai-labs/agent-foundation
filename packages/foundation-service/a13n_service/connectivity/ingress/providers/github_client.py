"""Bounded GitHub REST client for current-context native actions."""

from __future__ import annotations

from typing import Literal
from urllib.parse import quote, urlsplit

import httpx2
from pydantic import JsonValue, ValidationError

from a13n_service.connectivity.ingress.domain import JsonObject

from .github_actions import (
    GitHubActionBinding,
    GitHubAddCommentArguments,
    GitHubAddCommentOutcome,
    GitHubAddCommentOutcomeUnknown,
    GitHubAddCommentSucceeded,
    GitHubComment,
    GitHubCommentPage,
    GitHubCommentReceipt,
    GitHubListPrFilesArguments,
    GitHubPrFile,
    GitHubPrFilePage,
    GitHubReadCommentsArguments,
    GitHubReadTargetArguments,
    GitHubTarget,
)
from .github_api import GitHubApiError, read_github_response
from .github_token import GITHUB_API_VERSION, GitHubInstallationTokenProvider
from .native_http import EndpointValidator
from .origins import provider_url_origin

_RESPONSE_MAX_BYTES = 2 * 1024 * 1024
_PATCH_MAX_BYTES = 16 * 1024
_PATCH_TOTAL_MAX_BYTES = 64 * 1024


class GitHubNativeClient:
    def __init__(
        self,
        http_client: httpx2.AsyncClient,
        endpoint_validator: EndpointValidator,
        token_provider: GitHubInstallationTokenProvider,
        *,
        api_origin: str,
        web_origin: str,
        response_max_bytes: int = _RESPONSE_MAX_BYTES,
    ) -> None:
        if not 1 <= response_max_bytes <= _RESPONSE_MAX_BYTES:
            raise ValueError("GitHub response byte limit is invalid")
        self._http_client = http_client
        self._endpoint_validator = endpoint_validator
        self._token_provider = token_provider
        self._api_origin = api_origin.rstrip("/")
        self._web_origin = web_origin.rstrip("/")
        self._response_max_bytes = response_max_bytes

    async def add_comment(
        self,
        binding: GitHubActionBinding,
        arguments: GitHubAddCommentArguments,
        *,
        request_id: str,
    ) -> GitHubAddCommentOutcome:
        origin, token = await self._authorize(binding)
        url = f"{origin}{_issue_comments_path(binding)}"
        try:
            value = await self._send("POST", url, token=token, json_body={"body": arguments.body})
        except GitHubApiError as error:
            if error.code not in {
                "invalid_provider_response",
                "provider_unavailable",
                "response_too_large",
            }:
                raise
            return GitHubAddCommentOutcomeUnknown(request_id=request_id)
        if not isinstance(value, dict):
            return GitHubAddCommentOutcomeUnknown(request_id=request_id)
        try:
            receipt = GitHubCommentReceipt(
                comment_id=_required_int(value, "id"),
                node_id=_required_string(value, "node_id", max_length=512),
                html_url=self._safe_html_url(value.get("html_url")),
                request_id=request_id,
            )
        except (ValueError, ValidationError):
            return GitHubAddCommentOutcomeUnknown(request_id=request_id)
        return GitHubAddCommentSucceeded(receipt=receipt)

    async def read_comments(
        self,
        binding: GitHubActionBinding,
        arguments: GitHubReadCommentsArguments,
    ) -> GitHubCommentPage:
        value = await self._request(
            "GET",
            _issue_comments_path(binding),
            binding=binding,
            params={"page": str(arguments.page), "per_page": str(arguments.per_page)},
        )
        if not isinstance(value, list) or len(value) > arguments.per_page:
            raise GitHubApiError("invalid_provider_response")
        try:
            items = tuple(self._comment(item) for item in value)
        except (ValueError, ValidationError) as error:
            raise GitHubApiError("invalid_provider_response") from error
        return GitHubCommentPage(
            items=items,
            page=arguments.page,
            has_more=len(items) == arguments.per_page,
        )

    async def read_issue_or_pr(
        self,
        binding: GitHubActionBinding,
        arguments: GitHubReadTargetArguments,
    ) -> GitHubTarget:
        value = await self._request("GET", _target_path(binding), binding=binding)
        if not isinstance(value, dict):
            raise GitHubApiError("invalid_provider_response")
        try:
            title = _required_string(value, "title", max_length=40_000)
            state = _required_string(value, "state", max_length=128)
            labels = _labels(value.get("labels")) if arguments.include_labels else ()
            body = _optional_string(value.get("body"), max_length=262_144) if arguments.include_body else None
            return GitHubTarget(
                number=_bound_number(value, binding),
                kind=binding.target_kind,
                title=title,
                body=body,
                state=state,
                labels=labels,
                html_url=self._safe_html_url(value.get("html_url")),
                draft=_optional_bool(value.get("draft")),
                merged=_optional_bool(value.get("merged")),
            )
        except (ValueError, ValidationError) as error:
            raise GitHubApiError("invalid_provider_response") from error

    async def list_pr_files(
        self,
        binding: GitHubActionBinding,
        arguments: GitHubListPrFilesArguments,
    ) -> GitHubPrFilePage:
        if binding.target_kind != "pull_request":
            raise GitHubApiError("action_not_available")
        value = await self._request(
            "GET",
            f"{_repository_path(binding)}/pulls/{binding.number}/files",
            binding=binding,
            params={"page": str(arguments.page), "per_page": str(arguments.per_page)},
        )
        if not isinstance(value, list) or len(value) > arguments.per_page:
            raise GitHubApiError("invalid_provider_response")
        remaining = _PATCH_TOTAL_MAX_BYTES
        truncated_any = False
        items: list[GitHubPrFile] = []
        try:
            for item in value:
                if not isinstance(item, dict):
                    raise ValueError("file is not an object")
                patch = _optional_string(item.get("patch"), max_length=self._response_max_bytes)
                projected_patch, truncated = _truncate_utf8(patch, min(_PATCH_MAX_BYTES, remaining))
                remaining -= len(projected_patch.encode()) if projected_patch is not None else 0
                truncated_any = truncated_any or truncated
                items.append(
                    GitHubPrFile(
                        filename=_required_string(item, "filename", max_length=4096),
                        status=_required_string(item, "status", max_length=128),
                        additions=_required_nonnegative_int(item, "additions"),
                        deletions=_required_nonnegative_int(item, "deletions"),
                        changes=_required_nonnegative_int(item, "changes"),
                        patch=projected_patch,
                        patch_truncated=truncated,
                    )
                )
        except (ValueError, ValidationError) as error:
            raise GitHubApiError("invalid_provider_response") from error
        return GitHubPrFilePage(
            items=tuple(items),
            page=arguments.page,
            has_more=len(items) == arguments.per_page,
            patches_truncated=truncated_any,
        )

    async def _request(
        self,
        method: Literal["GET", "POST"],
        path: str,
        *,
        binding: GitHubActionBinding,
        params: dict[str, str] | None = None,
    ) -> JsonValue:
        origin, token = await self._authorize(binding)
        return await self._send(method, f"{origin}{path}", token=token, params=params)

    async def _authorize(self, binding: GitHubActionBinding) -> tuple[str, str]:
        try:
            origin = await self._endpoint_validator.validate(self._api_origin, resolve_dns=True)
        except ValueError as error:
            raise GitHubApiError("endpoint_denied") from error
        token = await self._token_provider.token(repository_id=binding.repository_id)
        return origin, token

    async def _send(
        self,
        method: Literal["GET", "POST"],
        url: str,
        *,
        token: str,
        json_body: JsonObject | None = None,
        params: dict[str, str] | None = None,
    ) -> JsonValue:
        try:
            async with self._http_client.stream(
                method,
                url,
                headers={
                    "accept": "application/vnd.github+json",
                    "authorization": f"Bearer {token}",
                    "x-github-api-version": GITHUB_API_VERSION,
                    "user-agent": "agent-foundation-service",
                },
                json=json_body,
                params=params,
                follow_redirects=False,
            ) as response:
                return await read_github_response(response, max_bytes=self._response_max_bytes)
        except GitHubApiError:
            raise
        except httpx2.HTTPError as error:
            raise GitHubApiError("provider_unavailable") from error

    def _comment(self, value: JsonValue) -> GitHubComment:
        if not isinstance(value, dict):
            raise ValueError("comment is not an object")
        user = value.get("user")
        author = user if isinstance(user, dict) else {}
        return GitHubComment(
            comment_id=_required_int(value, "id"),
            node_id=_required_string(value, "node_id", max_length=512),
            author_login=_optional_string(author.get("login"), max_length=256),
            author_id=_optional_positive_int(author.get("id")),
            body=_optional_string(value.get("body"), max_length=65_536),
            html_url=self._safe_html_url(value.get("html_url")),
            created_at=_optional_string(value.get("created_at"), max_length=64),
            updated_at=_optional_string(value.get("updated_at"), max_length=64),
        )

    def _safe_html_url(self, value: JsonValue | None) -> str:
        if not isinstance(value, str) or not 1 <= len(value) <= 2048:
            raise ValueError("GitHub URL is invalid")
        parsed = urlsplit(value)
        if parsed.username is not None or parsed.password is not None or parsed.query:
            raise ValueError("GitHub URL is invalid")
        if provider_url_origin(value) != self._web_origin:
            raise ValueError("GitHub URL has an unexpected origin")
        return value


def _repository_path(binding: GitHubActionBinding) -> str:
    owner = quote(binding.owner, safe="")
    repository = quote(binding.repository, safe="")
    return f"/repos/{owner}/{repository}"


def _target_path(binding: GitHubActionBinding) -> str:
    resource = "pulls" if binding.target_kind == "pull_request" else "issues"
    return f"{_repository_path(binding)}/{resource}/{binding.number}"


def _issue_comments_path(binding: GitHubActionBinding) -> str:
    return f"{_repository_path(binding)}/issues/{binding.number}/comments"


def _labels(value: JsonValue | None) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or len(value) > 256:
        raise ValueError("labels are invalid")
    labels: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("label is invalid")
        labels.append(_required_string(item, "name", max_length=256))
    return tuple(labels)


def _truncate_utf8(value: str | None, max_bytes: int) -> tuple[str | None, bool]:
    if value is None:
        return None, False
    encoded = value.encode()
    if len(encoded) <= max_bytes:
        return value, False
    if max_bytes == 0:
        return None, True
    return encoded[:max_bytes].decode("utf-8", errors="ignore"), True


def _required_string(value: JsonObject, key: str, *, max_length: int) -> str:
    selected = value.get(key)
    if not isinstance(selected, str) or not 1 <= len(selected) <= max_length:
        raise ValueError(f"invalid {key}")
    return selected


def _optional_string(value: JsonValue | None, *, max_length: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > max_length:
        raise ValueError("invalid provider string")
    return value


def _required_int(value: JsonObject, key: str) -> int:
    selected = value.get(key)
    if type(selected) is not int or selected <= 0:
        raise ValueError(f"invalid {key}")
    return selected


def _required_nonnegative_int(value: JsonObject, key: str) -> int:
    selected = value.get(key)
    if type(selected) is not int or selected < 0:
        raise ValueError(f"invalid {key}")
    return selected


def _optional_positive_int(value: JsonValue | None) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value <= 0:
        raise ValueError("invalid provider integer")
    return value


def _optional_bool(value: JsonValue | None) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ValueError("invalid provider boolean")
    return value


def _bound_number(value: JsonObject, binding: GitHubActionBinding) -> int:
    number = _required_int(value, "number")
    if number != binding.number:
        raise ValueError("GitHub target number does not match current binding")
    return number
