"""Dependency-light protected Run context and immutable application evidence."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from a13n_service.digests import Sha256Digest
from a13n_service.ids import ObjectId


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BuiltinSkillBundleRef(StrictModel):
    bundle_id: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9][a-z0-9.-]*$")
    content_digest: Sha256Digest
    logical_root: Literal["/environment/builtin-skills"] = "/environment/builtin-skills"


class DefinitionIdentity(StrictModel):
    generation: int = Field(ge=1)
    content_digest: Sha256Digest
    knowledge_bundle: BuiltinSkillBundleRef


class VerificationAcknowledgement(StrictModel):
    outcome: Literal["unverified", "failed"]
    reason: str = Field(min_length=1, max_length=2048)


class ConfigurationApplicationReceipt(StrictModel):
    draft_id: ObjectId
    reviewed_version: int = Field(ge=1)
    reviewed_digest: Sha256Digest
    agent_id: ObjectId
    agent_revision_id: ObjectId
    agent_version: int = Field(ge=1)
    applied_by_user_id: ObjectId
    applied_at: datetime
    no_change: bool
    verification_acknowledgement: VerificationAcknowledgement | None = None
    verification_run_ids: tuple[ObjectId, ...] = Field(default=(), max_length=32)


class ConfigurationRunContext(StrictModel):
    schema_version: Literal["1"] = "1"
    purpose: Literal["configuration_assistant"] = "configuration_assistant"
    session_id: ObjectId
    thread_id: str
    draft_id: ObjectId
    mode: Literal["create", "update"]
    target_agent_id: ObjectId | None
    initial_draft_version: int = Field(ge=1)
    source_agent_revision_id: ObjectId | None
    previous_application_receipt: ConfigurationApplicationReceipt | None = None
    definition_digest: Sha256Digest
    knowledge_bundle: BuiltinSkillBundleRef
    model_selection_reason: str = Field(min_length=1, max_length=128)
    model_selection_policy: Literal["1"] = "1"
