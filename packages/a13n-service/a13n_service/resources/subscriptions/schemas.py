"""Lifecycle kinds, subscription requests and representations, and the webhook delivery shapes."""

import base64
from datetime import datetime
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    SecretStr,
    StringConstraints,
    field_validator,
)

from a13n_service.infra.crypto import Envelope
from a13n_service.infra.ids import ObjectId

type LifecycleKind = Literal[
    "run.accepted",
    "run.running",
    "run.waiting",
    "run.completed",
    "run.failed",
    "run.cancelled",
    "run_attempt.leased",
    "run_attempt.running",
    "run_attempt.succeeded",
    "run_attempt.yielded",
    "run_attempt.failed",
    "run_attempt.cancelled",
    "schedule.enabled",
    "schedule.paused",
    "schedule.completed",
    "schedule.archived",
    "schedule_occurrence.submitted",
    "schedule_occurrence.skipped",
    "schedule_occurrence.failed",
    "schedule_occurrence.cancelled",
    "schedule_occurrence.completed",
]

LIFECYCLE_KINDS: tuple[str, ...] = LifecycleKind.__value__.__args__
# The error of a delivery that cannot be signed because its signing secret cannot be decrypted or is not a
# Standard Webhooks key, such as one stored before that scheme.
SECRET_UNAVAILABLE = "signing_secret_unavailable"

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
Url = Annotated[str, StringConstraints(min_length=1, max_length=2048)]
Kinds = Annotated[list[LifecycleKind], Field(min_length=1, max_length=len(LIFECYCLE_KINDS))]
SECRET_PREFIX = "whsec_"


def signing_key(secret: str) -> bytes:
    """The HMAC key of a Standard Webhooks secret: `whsec_` and the standard base64 of a 24 to 64 byte key.

    The one owner of the secret format; a value that is not that shape raises `ValueError`.
    """
    try:
        key = (
            base64.b64decode(secret.removeprefix(SECRET_PREFIX), validate=True)
            if secret.startswith(SECRET_PREFIX)
            else b""
        )
    except ValueError:  # binascii.Error
        key = b""
    if not 24 <= len(key) <= 64:
        raise ValueError("must be whsec_ followed by standard base64 of a 24 to 64 byte key")
    return key


def _standard_signing_secret(value: SecretStr) -> SecretStr:
    signing_key(value.get_secret_value())
    return value


SigningSecret = Annotated[
    SecretStr,
    Field(min_length=len(SECRET_PREFIX) + 32, max_length=len(SECRET_PREFIX) + 88),
    AfterValidator(_standard_signing_secret),
]


class SubscriptionFilter(BaseModel):
    """Absent fields match every event; a specified identity must exist and match."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    agent_id: ObjectId | None = None
    session_id: ObjectId | None = None
    thread_id: ObjectId | None = None
    schedule_id: ObjectId | None = None


class SubscriptionCreate(BaseModel):
    """Without `signing_secret` the service generates one; either way it is returned only by this request."""

    model_config = ConfigDict(extra="forbid")
    name: Name
    url: Url
    kinds: Kinds
    filter: SubscriptionFilter = Field(default_factory=SubscriptionFilter)
    enabled: bool = True
    signing_secret: SigningSecret | None = None

    @field_validator("kinds")
    @classmethod
    def unique_kinds(cls, value: list[LifecycleKind]) -> list[LifecycleKind]:
        return list(dict.fromkeys(value))


class SubscriptionUpdate(BaseModel):
    """`signing_secret` replaces the secret for deliveries queued after this change."""

    model_config = ConfigDict(extra="forbid")
    name: Name | None = None
    url: Url | None = None
    kinds: Kinds | None = None
    filter: SubscriptionFilter | None = None
    enabled: bool | None = None
    signing_secret: SigningSecret | None = None

    @field_validator("kinds")
    @classmethod
    def unique_kinds(cls, value: list[LifecycleKind] | None) -> list[LifecycleKind] | None:
        return None if value is None else list(dict.fromkeys(value))


class Subscription(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    workspace_id: str
    name: str
    url: str
    kinds: list[str]
    filter: SubscriptionFilter
    enabled: bool
    version: int
    created_by_id: str
    updated_by_id: str
    created_at: datetime
    updated_at: datetime


class CreatedSubscription(Subscription):
    signing_secret: str


class SubscriptionPage(BaseModel):
    items: list[Subscription]
    next_cursor: str | None


class WebhookTarget(BaseModel):
    """What a webhook outbox row copies from its subscription; the secret is encrypted for the outbox row."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    url: str
    signing_secret: Envelope


class WebhookDelivery(BaseModel):
    """One webhook outbox row; its ID is the delivery ID receivers deduplicate by."""

    id: str
    subscription_id: str
    url: str
    status: Literal["pending", "delivered", "dead"]
    attempts: int
    available_at: datetime
    last_error: str | None
    created_at: datetime
    delivered_at: datetime | None
    payload: dict[str, JsonValue]


class DeliveryPage(BaseModel):
    items: list[WebhookDelivery]
    next_cursor: str | None
