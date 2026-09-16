"""Protected configuration scope and reviewable candidate values."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from a13n_service.agents.domain import AgentConfig, AgentDescription, AgentName, StrictModel
from a13n_service.digests import Sha256Digest, digest_request
from a13n_service.ids import ObjectId, new_object_id

from .context import VerificationAcknowledgement


def new_draft_id() -> str:
    return new_object_id("cdraft")


class CreationMetadata(StrictModel):
    name: AgentName
    description: AgentDescription | None = None


class ConfigurationApplicationReceipt(StrictModel):
    draft_id: ObjectId
    reviewed_version: int = Field(ge=1)
    reviewed_digest: Sha256Digest
    reviewed_mode: Literal["create", "update"]
    reviewed_target_agent_id: ObjectId | None
    reviewed_base_agent_revision_id: ObjectId | None
    reviewed_base_agent_version: int | None = Field(ge=1)
    reviewed_creation_metadata: CreationMetadata | None
    agent_id: ObjectId
    agent_revision_id: ObjectId
    agent_version: int = Field(ge=1)
    applied_by_user_id: ObjectId
    applied_at: datetime
    no_change: bool
    verification_acknowledgement: VerificationAcknowledgement | None = None
    verification_run_ids: tuple[ObjectId, ...] = Field(default=(), max_length=32)


class ConfigurationValidation(StrictModel):
    draft_version: int = Field(ge=1)
    content_digest: Sha256Digest
    dependency_digest: Sha256Digest
    checked_at: datetime
    warnings: tuple[str, ...] = Field(default=(), max_length=32)


class ConfigurationDraft(StrictModel):
    id: ObjectId
    organization_id: ObjectId
    workspace_id: ObjectId
    session_id: ObjectId
    mode: Literal["create", "update"]
    target_agent_id: ObjectId | None
    source_selector: Literal["current", "explicit", "empty"]
    source_agent_revision_id: ObjectId | None
    source_agent_revision_version: int | None = Field(default=None, ge=1)
    base_agent_revision_id: ObjectId | None
    base_agent_version: int | None = Field(default=None, ge=1)
    version: int = Field(ge=1)
    config: AgentConfig | None
    creation_metadata: CreationMetadata | None = None
    content_digest: Sha256Digest
    status: Literal["open", "discarded", "expired"]
    latest_validation: ConfigurationValidation | None = None
    evidence_refs: tuple[ObjectId, ...] = Field(default=(), max_length=32)
    terminal_reason: str | None = None
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def validate_provenance(self) -> ConfigurationDraft:
        if self.source_selector == "empty":
            if self.source_agent_revision_id is not None or self.source_agent_revision_version is not None:
                raise ValueError("Empty original sources have no Revision.")
        elif self.source_agent_revision_id is None or self.source_agent_revision_version is None:
            raise ValueError("A retained original source requires its exact Revision and version.")
        if self.mode == "update":
            if (
                self.target_agent_id is None
                or self.base_agent_revision_id is None
                or self.base_agent_version is None
                or self.config is None
            ):
                raise ValueError("Update drafts require a target, baseline and complete configuration.")
        elif (
            self.target_agent_id is not None
            or self.base_agent_revision_id is not None
            or self.base_agent_version is not None
            or self.source_selector != "empty"
        ):
            raise ValueError("Create drafts retain their empty original source and absent target.")
        if self.content_digest != candidate_digest(self.config, self.creation_metadata):
            raise ValueError("The candidate digest does not match its content.")
        return self


def candidate_digest(config: AgentConfig | None, metadata: CreationMetadata | None) -> str:
    return digest_request(
        {
            "config": None if config is None else config.model_dump(mode="json", by_alias=True),
            "creation_metadata": None if metadata is None else metadata.model_dump(mode="json"),
        }
    )
