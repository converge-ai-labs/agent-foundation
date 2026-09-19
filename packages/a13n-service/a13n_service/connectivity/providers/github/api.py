"""Bounded GitHub REST response handling."""

from __future__ import annotations

import json
import time

import httpx2
from a13n_harness.http import ProviderHttpError, bounded_response_body, retry_after_seconds
from pydantic import JsonValue, TypeAdapter, ValidationError

_JSON_VALUE = TypeAdapter(JsonValue)


async def read_github_response(response: httpx2.Response, *, max_bytes: int) -> JsonValue:
    retry_after = retry_after_seconds(response.headers.get("retry-after"))
    body = await bounded_response_body(response, max_bytes=max_bytes)
    exhausted = response.headers.get("x-ratelimit-remaining") == "0"
    if exhausted:
        try:
            reset_delay = max(1, int(response.headers["x-ratelimit-reset"]) - int(time.time()) + 1)
            retry_after = max(retry_after or 0, reset_delay)
        except (KeyError, ValueError, OverflowError):
            pass
    if response.status_code == 429 or (response.status_code == 403 and (retry_after is not None or exhausted)):
        raise ProviderHttpError("rate_limited", retry_after_seconds=retry_after)
    if response.status_code >= 500:
        raise ProviderHttpError("provider_unavailable")
    if response.status_code < 200 or response.status_code >= 300:
        raise ProviderHttpError("provider_rejected")
    try:
        return _JSON_VALUE.validate_python(json.loads(body))
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError, RecursionError) as error:
        raise ProviderHttpError("invalid_provider_response") from error
