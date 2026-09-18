"""Bounded primitives shared by Lark token and native-action clients."""

from __future__ import annotations

import json

import httpx2
from a13n_harness.providers.http import ProviderHttpError, bounded_response_body, retry_after_seconds
from pydantic import TypeAdapter, ValidationError

from a13n_service.connectivity.domain import JsonObject

_JSON_OBJECT = TypeAdapter(JsonObject)


async def read_lark_response(response: httpx2.Response, *, max_bytes: int) -> JsonObject:
    retry_after = retry_after_seconds(response.headers.get("retry-after"))
    body = await bounded_response_body(response, max_bytes=max_bytes)
    if response.status_code == 429:
        raise ProviderHttpError("rate_limited", retry_after_seconds=retry_after)
    if response.status_code >= 500:
        raise ProviderHttpError("provider_unavailable")
    if response.status_code < 200 or response.status_code >= 300:
        raise ProviderHttpError("provider_rejected")
    try:
        value = _JSON_OBJECT.validate_python(json.loads(body))
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as error:
        raise ProviderHttpError("invalid_provider_response") from error
    code = value.get("code")
    if type(code) is not int:
        raise ProviderHttpError("invalid_provider_response")
    if code != 0:
        if code in {99991400, 99991401}:
            raise ProviderHttpError("rate_limited", retry_after_seconds=retry_after)
        raise ProviderHttpError("provider_rejected")
    return value
