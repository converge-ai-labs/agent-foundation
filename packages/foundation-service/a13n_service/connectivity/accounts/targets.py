"""Exact provider-object receiving configuration; no expression matching."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from a13n_service.connectivity.domain import JsonObject
from a13n_service.iam.domain import PrincipalRef

from .reception import InputBatchingPolicy, InputOverride


class TargetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    target_kind: Literal["conversation", "repository"]
    external_target_id: str = Field(min_length=1, max_length=2048)
    agent_id: str | None = None
    config_override: InputOverride | None = None
    input_batching: InputBatchingPolicy | None = None
    provider_policy: JsonObject | None = None
    receive_enabled: bool = True


class AccountTarget(TargetConfig):
    id: str
    organization_id: str
    workspace_id: str
    account_id: str
    version: int = Field(ge=1)
    created_by: PrincipalRef
    created_at: datetime
    updated_at: datetime


class TargetCollection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    items: tuple[AccountTarget, ...]
    next_cursor: str | None = None


class ReplaceTargetRequest(TargetConfig):
    expected_version: int = Field(ge=1)
