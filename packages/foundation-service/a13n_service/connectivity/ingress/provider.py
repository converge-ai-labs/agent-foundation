"""Provider-facing contracts for authenticated Ingress delivery."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from .domain import InputBatchingPolicy, JsonObject

BoundedProviderName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_.-]{0,127}$")]
BoundedProviderId = Annotated[str, StringConstraints(min_length=1, max_length=2048)]
BoundedHeaderName = Annotated[str, StringConstraints(min_length=1, max_length=128)]
BoundedHeaderValue = Annotated[str, StringConstraints(max_length=8192)]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProviderRequest(_StrictModel):
    method: Literal["POST"] = "POST"
    headers: dict[BoundedHeaderName, BoundedHeaderValue] = Field(max_length=128, repr=False)
    body: bytes = Field(max_length=8 * 1024 * 1024, repr=False)
    content_type: str | None = Field(default=None, max_length=255)


class ProviderHttpResponse(_StrictModel):
    status_code: int = Field(ge=100, le=599)
    headers: dict[str, str] = Field(default_factory=dict, max_length=32)
    body: bytes = Field(default=b"", max_length=1024 * 1024, repr=False)


class ExternalRef(_StrictModel):
    kind: BoundedProviderName
    id: BoundedProviderId = Field(repr=False)


class InboundEvent(_StrictModel):
    identity_kind: BoundedProviderName
    external_event_id: BoundedProviderId = Field(repr=False)
    normalization_version: BoundedProviderName
    type: BoundedProviderName
    occurred_at: datetime | None = None
    received_at: datetime
    text: Annotated[str, StringConstraints(max_length=262_144)] | None = Field(default=None, repr=False)
    actor: JsonObject | None = Field(default=None, repr=False)
    context: JsonObject = Field(repr=False)
    refs: dict[str, ExternalRef] = Field(min_length=1, max_length=32, repr=False)
    data: JsonObject = Field(repr=False)
    ordering_key: Annotated[str, StringConstraints(min_length=1, max_length=2048)] = Field(repr=False)
    retain_raw: bool = False


class DefaultRoute(_StrictModel):
    external_ref_key: str = Field(min_length=1, max_length=128)
    provider_context: JsonObject = Field(repr=False)
    input_mapping: JsonObject
    input_batching: InputBatchingPolicy
    provider_policy: JsonObject
    native_actions: tuple[str, ...] = Field(default=(), max_length=64)


class ProviderEventDecision(_StrictModel):
    kind: Literal["event"] = "event"
    event: InboundEvent


class ProviderCompleteDecision(_StrictModel):
    kind: Literal["complete"] = "complete"
    response: ProviderHttpResponse


type ProviderRequestDecision = ProviderEventDecision | ProviderCompleteDecision


class DurableAdmissionReceipt(_StrictModel):
    admission_id: str
    status: Literal["pending", "accepted", "rejected"]
    duplicate: bool
    reason_code: str | None = None


class IrrelevantAdmissionReceipt(_StrictModel):
    admission_id: None = None
    status: Literal["irrelevant"] = "irrelevant"
    duplicate: Literal[False] = False
    reason_code: str


type AdmissionReceipt = DurableAdmissionReceipt | IrrelevantAdmissionReceipt


class ProviderRequestError(Exception):
    """Authenticated-provider failure with an adapter-owned safe response."""

    def __init__(self, response: ProviderHttpResponse, *, reason_code: str) -> None:
        super().__init__(reason_code)
        self.response = response
        self.reason_code = reason_code


type HeaderMap = Mapping[str, str]
