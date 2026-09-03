"""Bounded GitHub REST response handling."""

from __future__ import annotations

import json

import httpx2
from pydantic import JsonValue, TypeAdapter, ValidationError

from .native_http import NativeActionError, bounded_response_body, retry_after_seconds

GitHubApiError = NativeActionError
_JSON_VALUE = TypeAdapter(JsonValue)


async def read_github_response(response: httpx2.Response, *, max_bytes: int) -> JsonValue:
    retry_after = retry_after_seconds(response.headers.get("retry-after"))
    body = await bounded_response_body(response, max_bytes=max_bytes)
    if response.status_code == 429 or (response.status_code == 403 and retry_after is not None):
        raise GitHubApiError("rate_limited", retry_after_seconds=retry_after)
    if response.status_code >= 500:
        raise GitHubApiError("provider_unavailable")
    if response.status_code < 200 or response.status_code >= 300:
        raise GitHubApiError("provider_rejected")
    try:
        return _JSON_VALUE.validate_python(json.loads(body))
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError, RecursionError) as error:
        raise GitHubApiError("invalid_provider_response") from error
