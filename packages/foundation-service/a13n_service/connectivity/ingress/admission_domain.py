"""Durable Ingress admission state and Foundation input port."""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from a13n_service.interactions.input import AgentInput
from a13n_service.temporal import Clock, utc_now

from .domain import JsonObject
from .provider import ExternalRef


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AdmissionStatus(StrEnum):
    pending = "pending"
    accepted = "accepted"
    rejected = "rejected"


class BindingState(StrEnum):
    bound = "bound"
    unbound = "unbound"


class ProtectedRawRef(_StrictModel):
    object_key: str = Field(repr=False)
    size_bytes: int = Field(ge=0)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expires_at: datetime


class PreparedIngressBatch(_StrictModel):
    batch_id: str
    organization_id: str
    workspace_id: str
    ingress_id: str
    ingress_version: int = Field(ge=1)
    execution_service_account_id: str
    provider_key: str
    provider_config_version: str
    provider_context_version: str
    route_id: str | None
    route_version: int | None
    selected_agent_id: str
    external_ref: ExternalRef = Field(repr=False)
    binding_state: BindingState
    binding_id: str | None
    mapping_digest: str
    provider_context: JsonObject = Field(repr=False)
    provider_policy: JsonObject
    native_actions: tuple[str, ...]
    capability_overlay: JsonObject | None
    agent_input: AgentInput = Field(repr=False)
    admission_ids: tuple[str, ...] = Field(min_length=1)


class AcceptedInputOutcome(_StrictModel):
    kind: Literal["accepted"] = "accepted"
    receipt_kind: Literal["run", "steer"]
    receipt_id: str


class RetryableInputOutcome(_StrictModel):
    kind: Literal["retryable"] = "retryable"
    reason_code: str
    available_at: datetime


class RejectedInputOutcome(_StrictModel):
    kind: Literal["rejected"] = "rejected"
    reason_code: str


class LostRaceInputOutcome(_StrictModel):
    kind: Literal["lost_race"] = "lost_race"


type InputAcceptanceOutcome = AcceptedInputOutcome | RetryableInputOutcome | RejectedInputOutcome | LostRaceInputOutcome


class FoundationInputAcceptor(Protocol):
    async def accept_ingress_batch(self, batch: PreparedIngressBatch) -> InputAcceptanceOutcome: ...


class UnavailableFoundationInputAcceptor:
    def __init__(
        self,
        *,
        retry_seconds: float = 30,
        clock: Clock = utc_now,
    ) -> None:
        self._retry_seconds = retry_seconds
        self._clock = clock

    async def accept_ingress_batch(self, batch: PreparedIngressBatch) -> InputAcceptanceOutcome:
        del batch
        return RetryableInputOutcome(
            reason_code="input_bridge_unavailable",
            available_at=self._clock() + timedelta(seconds=self._retry_seconds),
        )
