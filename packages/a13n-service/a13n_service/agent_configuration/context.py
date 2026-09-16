"""Dependency-light protected Run context and immutable application evidence."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from a13n_service.digests import Sha256Digest
from a13n_service.ids import ObjectId


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class VerificationAcknowledgement(StrictModel):
    outcome: Literal["unverified", "failed"]
    reason: str = Field(min_length=1, max_length=2048)


class ConfigurationRunContext(StrictModel):
    schema_version: Literal["1"] = "1"
    purpose: Literal["configuration_assistant"] = "configuration_assistant"
    session_id: ObjectId
    thread_id: str
    draft_id: ObjectId
    initial_draft_version: int = Field(ge=1)
    definition_digest: Sha256Digest
    model_selection_reason: str = Field(min_length=1, max_length=128)
    model_selection_policy: Literal["1"] = "1"
