"""Stable Webhook delivery envelope and HMAC request authentication."""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime
from typing import Annotated, Literal

import rfc8785
from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, model_validator

from a13n_service.lifecycle.domain import MAX_LIFECYCLE_PAYLOAD_BYTES
from a13n_service.temporal import UtcDateTime, require_aware_utc

DELIVERY_ID_HEADER = "X-A13n-Delivery-Id"
SIGNATURE_HEADER = "X-A13n-Webhook-Signature"
TIMESTAMP_HEADER = "X-A13n-Webhook-Timestamp"

_MAX_CANONICAL_ENVELOPE_BYTES = MAX_LIFECYCLE_PAYLOAD_BYTES + 16 * 1024


BoundedId = Annotated[str, StringConstraints(min_length=1, max_length=256)]


class DeliveryEnvelope(BaseModel):
    """Canonical durable Hook body sent to one Webhook destination."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    delivery_id: BoundedId
    hook_subscription_id: BoundedId
    hook_name: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    hook_schema_version: Annotated[str, StringConstraints(pattern=r"^[1-9][0-9]{0,31}$")]
    source_kind: Literal["lifecycle_event"] = "lifecycle_event"
    source_id: BoundedId
    workspace_id: BoundedId
    resource_type: Literal["run", "run_attempt"]
    resource_id: BoundedId
    resource_seq: int = Field(ge=1)
    resource_version: int = Field(ge=1)
    session_id: BoundedId | None = None
    thread_id: BoundedId | None = None
    run_id: BoundedId | None = None
    run_attempt_id: BoundedId | None = None
    harness_run_id: BoundedId | None = None
    occurred_at: UtcDateTime
    payload: dict[str, JsonValue]

    @model_validator(mode="after")
    def encoded_body_is_bounded(self) -> DeliveryEnvelope:
        if len(self.canonical_bytes()) > _MAX_CANONICAL_ENVELOPE_BYTES:
            raise ValueError("delivery envelope exceeds the encoded size limit")
        return self

    def canonical_bytes(self) -> bytes:
        return rfc8785.dumps(self.model_dump(mode="json", exclude_none=True))


def signed_request_headers(
    envelope: DeliveryEnvelope,
    *,
    signing_secret: str,
    signed_at: datetime,
) -> dict[str, str]:
    """Sign ``<unix-seconds>.<canonical-body>`` with HMAC-SHA256."""

    if not signing_secret:
        raise ValueError("Webhook signing Secret must not be empty")
    timestamp = str(int(require_aware_utc(signed_at).timestamp()))
    signature_input = timestamp.encode("ascii") + b"." + envelope.canonical_bytes()
    digest = hmac.new(signing_secret.encode("utf-8"), signature_input, hashlib.sha256).hexdigest()
    return {
        "Content-Type": "application/json",
        DELIVERY_ID_HEADER: envelope.delivery_id,
        TIMESTAMP_HEADER: timestamp,
        SIGNATURE_HEADER: f"v1={digest}",
    }


__all__ = [
    "DELIVERY_ID_HEADER",
    "SIGNATURE_HEADER",
    "TIMESTAMP_HEADER",
    "DeliveryEnvelope",
    "signed_request_headers",
]
