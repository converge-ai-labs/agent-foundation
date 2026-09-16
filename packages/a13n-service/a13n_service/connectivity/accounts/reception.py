"""Account reception settings and the bounded inbound invocation override."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from a13n_service.agents.domain import AgentRunOverride, ModelOverride, SkillSelection
from a13n_service.connectivity.domain import JsonObject
from a13n_service.connectivity.selection_domain import ConnectionToolSelection
from a13n_service.memory.bots.domain import MemorySettings


class InputBatchingPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    min_interval_ms: int = Field(ge=1)
    max_batch_events: int = Field(ge=1)


class InputOverride(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    model: ModelOverride | None = None
    skills: tuple[SkillSelection, ...] | None = Field(default=None, max_length=512)
    connection_tools: tuple[ConnectionToolSelection, ...] | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def validate_canonical_override(self) -> "InputOverride":
        for field in ("skills", "connection_tools"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} must be omitted or a collection, not null")
        self.invocation_override()
        return self

    def invocation_override(self) -> AgentRunOverride:
        return AgentRunOverride.model_validate(self.model_dump(exclude_unset=True))


class ReceptionScope(StrEnum):
    all_accessible = "all_accessible"
    configured_targets = "configured_targets"


class Reception(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    memory: MemorySettings | None = None
    reception_scope: ReceptionScope = ReceptionScope.all_accessible
    receive_enabled: bool = False
    default_agent_id: str | None = None
    execution_service_account_id: str | None = None
    input_batching: InputBatchingPolicy | None = None
    provider_policy: JsonObject | None = None

    @model_validator(mode="after")
    def require_receiver(self) -> "Reception":
        if self.receive_enabled and (not self.default_agent_id or not self.execution_service_account_id):
            raise ValueError("Receiving messages requires a default Agent and execution Service Account")
        return self
