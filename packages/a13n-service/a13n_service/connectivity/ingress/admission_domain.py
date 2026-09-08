"""Typed snapshots and the canonical Service input acceptance port."""

from datetime import datetime
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from a13n_service.connectivity.accounts.reception import InputBatchingPolicy
from a13n_service.connectivity.domain import JsonObject
from a13n_service.interactions.input import AgentInput

from .provider import ExternalRef


class BatchConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    account_id: str
    account_version: int
    target_id: str | None
    target_version: int | None
    target_kind: str
    external_target_id: str
    provider_key: str
    provider_context_version: str
    provider_context: JsonObject = Field(repr=False)
    provider_policy: JsonObject
    native_actions: tuple[str, ...]
    input_batching: InputBatchingPolicy

    def same_generation(self, other: "BatchConfiguration") -> bool:
        return self.model_dump(exclude={"provider_context"}) == other.model_dump(exclude={"provider_context"})


class PreparedIngressBatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    batch_id: str
    organization_id: str
    workspace_id: str
    binding_id: str
    external_ref: ExternalRef = Field(repr=False)
    configuration: BatchConfiguration
    claim_owner: str
    claim_generation: int
    agent_input: AgentInput = Field(repr=False)


class AcceptedInputOutcome(BaseModel):
    kind: Literal["accepted"] = "accepted"
    receipt_kind: Literal["run", "steer"]
    receipt_id: str


class RetryableInputOutcome(BaseModel):
    kind: Literal["retryable"] = "retryable"
    reason_code: str
    available_at: datetime


class RejectedInputOutcome(BaseModel):
    kind: Literal["rejected"] = "rejected"
    reason_code: str


class LostRaceInputOutcome(BaseModel):
    kind: Literal["lost_race"] = "lost_race"


type InputAcceptanceOutcome = AcceptedInputOutcome | RetryableInputOutcome | RejectedInputOutcome | LostRaceInputOutcome


class InputAcceptor(Protocol):
    async def accept_ingress_batch(self, batch: PreparedIngressBatch) -> InputAcceptanceOutcome: ...
