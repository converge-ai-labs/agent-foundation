"""Bounded primitives shared by Lark token and native-action clients."""

from __future__ import annotations

import json

import httpx2
from pydantic import TypeAdapter, ValidationError

from a13n_service.connectivity.domain import JsonObject
from a13n_service.connectivity.http import ConnectivityHttpError, bounded_response_body, retry_after_seconds

_JSON_OBJECT = TypeAdapter(JsonObject)


LarkApiError = ConnectivityHttpError


async def read_lark_response(response: httpx2.Response, *, max_bytes: int) -> JsonObject:
    retry_after = retry_after_seconds(response.headers.get("retry-after"))
    body = await bounded_response_body(response, max_bytes=max_bytes)
    if response.status_code == 429:
        raise LarkApiError("rate_limited", retry_after_seconds=retry_after)
    if response.status_code >= 500:
        raise LarkApiError("provider_unavailable")
    if response.status_code < 200 or response.status_code >= 300:
        raise LarkApiError("provider_rejected")
    try:
        value = _JSON_OBJECT.validate_python(json.loads(body))
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as error:
        raise LarkApiError("invalid_provider_response") from error
    code = value.get("code")
    if type(code) is not int:
        raise LarkApiError("invalid_provider_response")
    if code != 0:
        if code in {99991400, 99991401}:
            raise LarkApiError("rate_limited", retry_after_seconds=retry_after)
        raise LarkApiError("provider_rejected")
    return value
