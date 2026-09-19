"""Bounded authenticated HTTP transport shared by built-in ConnectorProvider drivers."""

from __future__ import annotations

import json
from typing import Literal

import httpx2
from anyio import fail_after
from pydantic import JsonValue, TypeAdapter, ValidationError

from a13n_harness.providers.connector.contracts import JsonObject

from ...http import (
    EndpointValidator,
    ProviderHttpError,
    bounded_response_body,
    retry_after_seconds,
)
from .contracts import ConnectorProviderError

_JSON_VALUE = TypeAdapter(JsonValue)


class ConnectorHttpClient:
    def __init__(
        self,
        http_client: httpx2.AsyncClient,
        endpoint_validator: EndpointValidator,
        *,
        response_max_bytes: int,
        timeout_seconds: float = 30,
    ) -> None:
        if response_max_bytes <= 0:
            raise ValueError("ConnectorProvider response byte limit is invalid")
        self._http_client = http_client
        self._endpoint_validator = endpoint_validator
        self._response_max_bytes = response_max_bytes
        self._timeout_seconds = timeout_seconds

    async def request(
        self,
        method: Literal["DELETE", "GET", "PATCH", "POST"],
        *,
        endpoint: str,
        path: str,
        api_key: str | None = None,
        authentication: Literal["api_key", "bearer"] = "api_key",
        json_body: JsonObject | None = None,
        params: dict[str, str] | None = None,
        write: bool = False,
        extra_headers: dict[str, str] | None = None,
    ) -> JsonValue:
        if api_key is not None and not 1 <= len(api_key) <= 4096:
            raise ConnectorProviderError("credential_unavailable")
        dispatched = False
        try:
            with fail_after(self._timeout_seconds):
                try:
                    if not path.startswith("/") or path.startswith("//") or "\\" in path:
                        raise ValueError("Connector path must be origin-relative")
                    if any(ord(character) < 32 for character in endpoint + path):
                        raise ValueError("Connector URL contains control characters")
                    base = httpx2.URL(endpoint)
                    if base.userinfo or base.query or base.fragment:
                        raise ValueError("Connector endpoint must not contain credentials, query or fragment")
                    target = httpx2.URL(f"{str(base).rstrip('/')}{path}")
                    if target.fragment:
                        raise ValueError("Connector path must not contain a fragment")
                    if params is not None:
                        target = target.copy_merge_params(params)
                    destination = await self._endpoint_validator.validate(str(target), resolve_dns=True)
                except (ValueError, httpx2.InvalidURL) as error:
                    raise ConnectorProviderError("endpoint_denied") from error
                headers = {"accept": "application/json", "content-type": "application/json"}
                if api_key is not None:
                    if authentication == "bearer":
                        headers["authorization"] = f"Bearer {api_key}"
                    else:
                        headers["x-api-key"] = api_key
                if extra_headers is not None:
                    headers.update(extra_headers)
                dispatched = True
                async with self._http_client.stream(
                    method,
                    destination,
                    headers=headers,
                    json=json_body,
                    follow_redirects=False,
                ) as response:
                    return await _read_response(response, max_bytes=self._response_max_bytes)
        except ConnectorProviderError as error:
            if (
                write
                and dispatched
                and error.code
                in {
                    "invalid_provider_response",
                    "provider_unavailable",
                    "response_too_large",
                }
            ):
                raise ConnectorProviderError(
                    error.code,
                    retryable=error.retryable,
                    outcome_unknown=True,
                    retry_after_seconds=error.retry_after_seconds,
                    http_status=error.http_status,
                ) from error
            raise
        except (ProviderHttpError, httpx2.HTTPError, TimeoutError) as error:
            code = error.code if isinstance(error, ProviderHttpError) else "provider_unavailable"
            raise ConnectorProviderError(
                code,
                retryable=True,
                outcome_unknown=write and dispatched,
                retry_after_seconds=getattr(error, "retry_after_seconds", None),
            ) from error


async def _read_response(response: httpx2.Response, *, max_bytes: int) -> JsonValue:
    retry_after = retry_after_seconds(response.headers.get("retry-after"))
    body = await bounded_response_body(response, max_bytes=max_bytes)
    if response.status_code == 429:
        raise ConnectorProviderError(
            "rate_limited",
            retryable=True,
            retry_after_seconds=retry_after,
            http_status=response.status_code,
        )
    if response.status_code >= 500:
        raise ConnectorProviderError("provider_unavailable", retryable=True, http_status=response.status_code)
    if response.status_code < 200 or response.status_code >= 300:
        raise ConnectorProviderError("provider_rejected", http_status=response.status_code)
    if not body:
        return None
    try:
        return _JSON_VALUE.validate_python(json.loads(body))
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError, RecursionError) as error:
        raise ConnectorProviderError("invalid_provider_response") from error
