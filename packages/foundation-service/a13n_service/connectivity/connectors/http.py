"""Bounded authenticated HTTP transport shared by built-in Connector drivers."""

from __future__ import annotations

import json
from typing import Literal

import httpx2
from pydantic import JsonValue, TypeAdapter, ValidationError

from a13n_service.connectivity.domain import JsonObject
from a13n_service.connectivity.http import (
    ConnectivityHttpError,
    EndpointValidator,
    bounded_response_body,
    retry_after_seconds,
)

from .adapters import ConnectorAdapterError

_JSON_VALUE = TypeAdapter(JsonValue)


class ConnectorHttpClient:
    def __init__(
        self,
        http_client: httpx2.AsyncClient,
        endpoint_validator: EndpointValidator,
        *,
        response_max_bytes: int,
    ) -> None:
        if response_max_bytes <= 0:
            raise ValueError("Connector response byte limit is invalid")
        self._http_client = http_client
        self._endpoint_validator = endpoint_validator
        self._response_max_bytes = response_max_bytes

    async def request(
        self,
        method: Literal["DELETE", "GET", "PATCH", "POST"],
        *,
        endpoint: str,
        path: str,
        api_key: str,
        json_body: JsonObject | None = None,
        params: dict[str, str] | None = None,
        write: bool = False,
        extra_headers: dict[str, str] | None = None,
    ) -> JsonValue:
        if not 1 <= len(api_key) <= 4096:
            raise ConnectorAdapterError("credential_unavailable")
        try:
            base = await self._endpoint_validator.validate(endpoint, resolve_dns=True)
        except ValueError as error:
            raise ConnectorAdapterError("endpoint_denied") from error
        headers = {
            "accept": "application/json",
            "content-type": "application/json",
            "x-api-key": api_key,
        }
        if extra_headers is not None:
            headers.update(extra_headers)
        try:
            async with self._http_client.stream(
                method,
                f"{base.rstrip('/')}{path}",
                headers=headers,
                json=json_body,
                params=params,
                follow_redirects=False,
            ) as response:
                return await _read_response(response, max_bytes=self._response_max_bytes)
        except ConnectorAdapterError as error:
            if write and error.code in {
                "invalid_provider_response",
                "provider_unavailable",
                "response_too_large",
            }:
                raise ConnectorAdapterError(
                    error.code,
                    retryable=error.retryable,
                    outcome_unknown=True,
                    retry_after_seconds=error.retry_after_seconds,
                ) from error
            raise
        except (ConnectivityHttpError, httpx2.HTTPError) as error:
            code = error.code if isinstance(error, ConnectivityHttpError) else "provider_unavailable"
            raise ConnectorAdapterError(
                code,
                retryable=True,
                outcome_unknown=write,
                retry_after_seconds=getattr(error, "retry_after_seconds", None),
            ) from error


async def _read_response(response: httpx2.Response, *, max_bytes: int) -> JsonValue:
    retry_after = retry_after_seconds(response.headers.get("retry-after"))
    try:
        body = await bounded_response_body(response, max_bytes=max_bytes)
    except ConnectivityHttpError:
        raise
    if response.status_code == 429:
        raise ConnectorAdapterError(
            "rate_limited",
            retryable=True,
            retry_after_seconds=retry_after,
        )
    if response.status_code >= 500:
        raise ConnectorAdapterError("provider_unavailable", retryable=True)
    if response.status_code < 200 or response.status_code >= 300:
        raise ConnectorAdapterError("provider_rejected")
    if not body:
        return None
    try:
        return _JSON_VALUE.validate_python(json.loads(body))
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError, RecursionError) as error:
        raise ConnectorAdapterError("invalid_provider_response") from error


def required_api_key(credentials: JsonObject) -> str:
    value = credentials.get("api_key")
    if not isinstance(value, str) or not value:
        raise ConnectorAdapterError("credential_unavailable")
    return value
