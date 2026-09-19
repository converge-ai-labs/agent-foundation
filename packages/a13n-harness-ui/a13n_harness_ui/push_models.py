"""Browser Web Push subscriptions and detached delivery values."""

from __future__ import annotations

import base64
from hashlib import sha256
from urllib.parse import urlsplit

from cryptography.hazmat.primitives.asymmetric import ec
from pydantic import BaseModel, ConfigDict, Field, field_validator


def decode_key(value: str) -> bytes:
    return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)


class PushKeys(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    p256dh: str = Field(max_length=128)
    auth: str = Field(max_length=32)

    @field_validator("p256dh")
    @classmethod
    def validate_public_key(cls, value: str) -> str:
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), decode_key(value))
        return value

    @field_validator("auth")
    @classmethod
    def validate_auth(cls, value: str) -> str:
        if len(decode_key(value)) != 16:
            raise ValueError("Push authentication must contain 16 bytes")
        return value


class PushSubscriptionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    endpoint: str = Field(max_length=4096)
    keys: PushKeys
    origin: str = Field(max_length=512)

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, value: str) -> str:
        url = urlsplit(value)
        # Subscription URLs become outbound requests. Limit them to the browser
        # push services we support, not arbitrary URLs on the Host's network.
        host = url.hostname or ""
        supported = host in {"fcm.googleapis.com", "updates.push.services.mozilla.com"} or host.endswith(
            (".push.services.mozilla.com", ".push.apple.com")
        )
        if (
            not supported
            or url.scheme != "https"
            or url.port not in (None, 443)
            or url.username
            or url.password
            or url.fragment
        ):
            raise ValueError("Unsupported browser push service")
        return value

    @field_validator("origin")
    @classmethod
    def validate_origin(cls, value: str) -> str:
        url = urlsplit(value)
        secure = url.scheme == "https" or (url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1", "::1"})
        if not secure or not url.hostname or url.username or url.password or url.path or url.query or url.fragment:
            raise ValueError("A secure browser origin is required")
        return value

    @property
    def subscription_id(self) -> str:
        return sha256(self.endpoint.encode()).hexdigest()


class PushConfiguration(BaseModel):
    public_key: str


class PushSubscriptionView(BaseModel):
    subscription_id: str


class PushTestResult(BaseModel):
    accepted: bool
