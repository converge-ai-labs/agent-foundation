"""Envd enrollment values for Harness UI, the Host that accepts device pairing.

The daemon posts to PAIRING_PATH with its locally retained Bearer credential.
Harness UI approves enrollment through its ordinary authenticated management surface;
subsequent connections use the same narrowly scoped credential, not a user API key.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

PAIRING_LIFETIME = timedelta(minutes=10)
MAX_PENDING_PAIRINGS = 128
PAIRING_PATH = "/api/envd/pair"


class PairingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    device_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    name: str = Field(min_length=1, max_length=128)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if value != value.strip() or any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("Device name must be nonempty text without control characters")
        return value


class PairingChallenge(PairingRequest):
    """Safe details shown to both the registering operator and approving user."""

    pairing_id: str
    verification_code: str
    expires_at: datetime


class PendingPairing(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    challenge: PairingChallenge
    credential_digest: str = Field(repr=False)

    @classmethod
    def create(cls, request: PairingRequest, digest: str, *, now: datetime | None = None) -> PendingPairing:
        now = now or datetime.now(UTC)
        code = digest[24:32].upper()
        return cls(
            challenge=PairingChallenge(
                **request.model_dump(),
                pairing_id=pairing_id(digest),
                verification_code=f"{code[:4]}-{code[4:]}",
                expires_at=now + PAIRING_LIFETIME,
            ),
            credential_digest=digest,
        )

    def expired(self, now: datetime | None = None) -> bool:
        return self.challenge.expires_at <= (now or datetime.now(UTC))


class PairingPending(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["pending"] = "pending"
    challenge: PairingChallenge
    approval_url: str | None = None
    poll_after_seconds: int = 2


class PairingApproved(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["approved"] = "approved"
    resource_id: str
    websocket_url: str


type PairingResponse = PairingPending | PairingApproved


def credential_digest(credential: str) -> str:
    """Accept the 256-bit generated credential without including it in errors."""
    if re.fullmatch(r"[0-9a-f]{64}", credential) is None:
        raise ValueError("Invalid envd pairing credential")
    return hashlib.sha256(credential.encode("ascii")).hexdigest()


def credential_matches(credential: str, digest: str) -> bool:
    try:
        actual = credential_digest(credential)
    except ValueError:
        return False
    return hmac.compare_digest(actual, digest)


def pairing_id(digest: str) -> str:
    return f"pair-{digest[:24]}"
