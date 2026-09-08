"""Protected OAuth setup and credential bundle boundaries."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import TypeAdapter, ValidationError

from a13n_service.connectivity.domain import JsonObject

from .oauth_client import MCPOAuthError, OAuthPreparation

_JSON_OBJECT = TypeAdapter(JsonObject)


def oauth_preparation(setup: JsonObject) -> OAuthPreparation:
    return OAuthPreparation(
        resource_url=_required_string(setup, "resource_url"),
        issuer_url=_required_string(setup, "issuer_url"),
        authorization_endpoint=_required_string(setup, "authorization_endpoint"),
        token_endpoint=_required_string(setup, "token_endpoint"),
        registration_endpoint=_optional_string(setup, "registration_endpoint"),
        client_id=_required_string(setup, "client_id"),
        client_secret=_optional_string(setup, "client_secret"),
        token_endpoint_auth_method=_required_string(setup, "token_endpoint_auth_method"),
        registration_access_token=_optional_string(setup, "registration_access_token"),
        registration_client_uri=_optional_string(setup, "registration_client_uri"),
        scope=_optional_string(setup, "scope"),
    )


def oauth_setup_bundle(preparation: OAuthPreparation, *, state: str, verifier: str) -> JsonObject:
    return {
        "resource_url": preparation.resource_url,
        "issuer_url": preparation.issuer_url,
        "authorization_endpoint": preparation.authorization_endpoint,
        "token_endpoint": preparation.token_endpoint,
        "registration_endpoint": preparation.registration_endpoint,
        "client_id": preparation.client_id,
        "client_secret": preparation.client_secret,
        "token_endpoint_auth_method": preparation.token_endpoint_auth_method,
        "registration_access_token": preparation.registration_access_token,
        "registration_client_uri": preparation.registration_client_uri,
        "scope": preparation.scope,
        "state": state,
        "verifier": verifier,
    }


def decode_oauth_bundle(value: str) -> JsonObject:
    try:
        return validate_oauth_bundle(json.loads(value))
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError, ValidationError) as error:
        raise ValueError("invalid protected OAuth bundle") from error


def validate_oauth_bundle(value: object) -> JsonObject:
    try:
        return _JSON_OBJECT.validate_python(value)
    except ValidationError as error:
        raise ValueError("invalid protected OAuth bundle") from error


def required_oauth_string(value: JsonObject, key: str) -> str:
    return _required_string(value, key)


def with_expiration(bundle: dict[str, Any], now: datetime) -> dict[str, Any]:
    expires_in = bundle.get("expires_in")
    if expires_in is None:
        bundle["expires_at"] = None
        return bundle
    if isinstance(expires_in, bool) or not isinstance(expires_in, int) or not 1 <= expires_in <= 366 * 24 * 3600:
        raise MCPOAuthError("invalid_token_response")
    bundle["expires_at"] = (now + timedelta(seconds=expires_in)).isoformat()
    return bundle


def optional_expiration(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("invalid protected OAuth bundle")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError("invalid protected OAuth bundle") from error
    if parsed.tzinfo is None:
        raise ValueError("invalid protected OAuth bundle")
    return parsed.astimezone(UTC)


def _required_string(value: JsonObject, key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise ValueError("invalid protected OAuth bundle")
    return item


def _optional_string(value: JsonObject, key: str) -> str | None:
    item = value.get(key)
    if item is None:
        return None
    if not isinstance(item, str) or not item:
        raise ValueError("invalid protected OAuth bundle")
    return item
