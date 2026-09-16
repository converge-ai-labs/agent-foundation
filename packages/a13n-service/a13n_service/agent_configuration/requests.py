"""Human configuration commands; model edits have a narrower separate schema."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from a13n_service.agents.domain import AgentConfig, StrictModel
from a13n_service.digests import Sha256Digest
from a13n_service.ids import ObjectId

from .context import VerificationAcknowledgement
from .domain import CreationMetadata
from .editing import Operation


class SourceSelection(StrictModel):
    selector: Literal["current", "explicit", "empty"] = "current"
    revision_id: ObjectId | None = None

    @model_validator(mode="after")
    def validate_selection(self) -> SourceSelection:
        if (self.selector == "explicit") != (self.revision_id is not None):
            raise ValueError("Only explicit source selection supplies a Revision ID.")
        return self


class CreateSessionRequest(StrictModel):
    target_agent_id: ObjectId | None = None
    source: SourceSelection | None = None


class CreateConfigurationThreadRequest(StrictModel):
    source: SourceSelection | None = None
    fork_from_run_id: ObjectId


class UpdateConfigurationDraftRequest(StrictModel):
    expected_version: int = Field(ge=1)
    expected_digest: Sha256Digest | None = None
    operations: tuple[Operation, ...] = Field(default=(), max_length=32)
    creation_metadata: CreationMetadata | None = None

    @model_validator(mode="after")
    def require_change(self) -> UpdateConfigurationDraftRequest:
        if not self.operations and "creation_metadata" not in self.model_fields_set:
            raise ValueError("Provide configuration operations or creation metadata.")
        return self


class RebaseDraftRequest(StrictModel):
    expected_version: int = Field(ge=1)
    expected_target_version: int = Field(ge=1)
    config: AgentConfig


class DiscardDraftRequest(StrictModel):
    expected_version: int = Field(ge=1)


class ApplyDraftRequest(StrictModel):
    expected_version: int = Field(ge=1)
    content_digest: Sha256Digest
    expected_target_version: int | None = Field(default=None, ge=1)
    dependency_digest: Sha256Digest
    verification_run_ids: tuple[ObjectId, ...] = Field(default=(), max_length=32)
    verification_acknowledgement: VerificationAcknowledgement | None = None
