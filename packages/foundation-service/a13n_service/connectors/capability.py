"""Signed short-lived Connector capabilities for one TurnAttempt."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from typing import Annotated, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .domain import PrincipalRef
from .errors import ConnectorError


class ConnectorCapabilityClaims(BaseModel):
    """Immutable claims derived from durable Turn and TurnAttempt facts."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    organization_id: Annotated[str, Field(min_length=1, max_length=72)]
    workspace_id: Annotated[str, Field(min_length=1, max_length=72)]
    connector_id: Annotated[str, Field(min_length=1, max_length=72)]
    connector_revision_id: Annotated[str, Field(min_length=1, max_length=72)]
    connection_id: Annotated[str, Field(min_length=1, max_length=72)] | None
    agent_preset_version_id: Annotated[str, Field(min_length=1, max_length=72)]
    turn_id: Annotated[str, Field(min_length=1, max_length=72)]
    turn_attempt_id: Annotated[str, Field(min_length=1, max_length=72)]
    attempt_fence: Annotated[int, Field(ge=1)]
    declaration_index: Annotated[int, Field(ge=0)]
    effective_tools: tuple[Annotated[str, Field(min_length=1, max_length=200)], ...]
    provider_contract_version: Annotated[str, Field(min_length=1, max_length=200)]
    issued_at: datetime
    expires_at: datetime

    @field_validator("effective_tools")
    @classmethod
    def validate_tools(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or len(value) != len(set(value)):
            raise ValueError("effective_tools must be non-empty and unique")
        return value

    @field_validator("issued_at", "expires_at")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("capability timestamps must include a timezone")
        return value.astimezone(UTC)


class ConnectorAttemptAuthorizer(Protocol):
    """Recheck capability claims against current durable Attempt authority."""

    async def authorize_connector_attempt(self, claims: ConnectorCapabilityClaims) -> PrincipalRef: ...


class ConnectorCapabilityCodec:
    """Issue and verify compact HMAC-authenticated capability tokens."""

    def __init__(self, signing_key: bytes, *, max_lifetime: timedelta = timedelta(minutes=5)) -> None:
        if len(signing_key) < 32:
            raise ValueError("Connector capability signing key must contain at least 32 bytes")
        if max_lifetime <= timedelta(0) or max_lifetime > timedelta(hours=1):
            raise ValueError("Connector capability max lifetime is invalid")
        self._signing_key = bytes(signing_key)
        self._max_lifetime = max_lifetime

    def issue(self, claims: ConnectorCapabilityClaims, *, now: datetime | None = None) -> str:
        current = (now or datetime.now(UTC)).astimezone(UTC)
        if claims.issued_at > current + timedelta(seconds=5):
            raise ConnectorError("Connector capability issue time is invalid.", code="connector_capability_invalid")
        if (
            claims.expires_at <= current
            or claims.expires_at <= claims.issued_at
            or claims.expires_at - claims.issued_at > self._max_lifetime
        ):
            raise ConnectorError("Connector capability lifetime is invalid.", code="connector_capability_invalid")
        payload = claims.model_dump_json(exclude_none=False).encode("utf-8")
        encoded_payload = _encode(payload)
        signature = hmac.digest(self._signing_key, encoded_payload.encode("ascii"), hashlib.sha256)
        return f"{encoded_payload}.{_encode(signature)}"

    def verify(self, token: str, *, now: datetime | None = None) -> ConnectorCapabilityClaims:
        if not token or len(token) > 16_384:
            raise _invalid_capability()
        try:
            encoded_payload, encoded_signature = token.split(".", 1)
            supplied = _decode(encoded_signature)
            expected = hmac.digest(self._signing_key, encoded_payload.encode("ascii"), hashlib.sha256)
            if not hmac.compare_digest(supplied, expected):
                raise _invalid_capability()
            payload = json.loads(_decode(encoded_payload))
            claims = ConnectorCapabilityClaims.model_validate(payload)
        except (UnicodeDecodeError, ValueError, ValidationError, json.JSONDecodeError):
            raise _invalid_capability() from None
        current = (now or datetime.now(UTC)).astimezone(UTC)
        if (
            claims.expires_at <= current
            or claims.expires_at <= claims.issued_at
            or claims.issued_at > current + timedelta(seconds=5)
        ):
            raise _invalid_capability()
        if claims.expires_at - claims.issued_at > self._max_lifetime:
            raise _invalid_capability()
        return claims


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.b64decode(value + padding, altchars=b"-_", validate=True)


def _invalid_capability() -> ConnectorError:
    return ConnectorError("Connector capability is invalid or expired.", code="connector_capability_invalid")


__all__ = [
    "ConnectorAttemptAuthorizer",
    "ConnectorCapabilityClaims",
    "ConnectorCapabilityCodec",
]
