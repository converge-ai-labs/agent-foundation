"""Human configuration commands; model edits have a narrower separate schema."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

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
    fork_from_run_id: ObjectId


class UpdateConfigurationDraftRequest(StrictModel):
    expected_version: int = Field(ge=1)
    expected_digest: Sha256Digest | None = None
    operations: tuple[Operation, ...] = Field(default=(), max_length=32)
    creation_metadata: CreationMetadata | None = None
    suggested_change_summary: Annotated[str, StringConstraints(max_length=2048)] | None = None

    @model_validator(mode="after")
    def require_change(self) -> UpdateConfigurationDraftRequest:
        if not self.operations and not self.model_fields_set.intersection(
            {"creation_metadata", "suggested_change_summary"}
        ):
            raise ValueError("Provide configuration operations, creation metadata, or a suggested summary.")
        return self


class RebaseDraftRequest(StrictModel):
    expected_version: int = Field(ge=1)
    expected_target_etag: str = Field(min_length=1, max_length=256)
    config: AgentConfig


class DiscardDraftRequest(StrictModel):
    expected_version: int = Field(ge=1)


class ApplyDraftRequest(StrictModel):
    expected_version: int = Field(ge=1)
    content_digest: Sha256Digest
    dependency_digest: Sha256Digest
    change_summary: Annotated[str, StringConstraints(max_length=2048)] | None = None
    verification_run_ids: tuple[ObjectId, ...] = Field(default=(), max_length=32)
    verification_acknowledgement: VerificationAcknowledgement | None = None
