"""Memory subjects, Agent selection, and bounded public representations."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from a13n_harness.providers.memory import MemoryProviderDefinition
from a13n_harness.providers.memory.contracts import MemoryScope as ScopeKind
from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, model_serializer, model_validator

from a13n_service.iam.domain import PrincipalRef
from a13n_service.ids import ObjectId
from a13n_service.interactions.domain import ThreadId
from a13n_service.names import DisplayName
from a13n_service.provider_metadata import ProviderMetadata, provider_metadata_core

MemoryText = Annotated[str, StringConstraints(min_length=1, max_length=8000, pattern=r"\S")]


class MemorySelection(BaseModel):
    """Opt-in Agent behavior; backend credentials and subject IDs are host-owned."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    provider_id: ObjectId
    scope: ScopeKind | None = None
    auto_recall: bool = True
    toolset: bool = True
    recall_limit: int = Field(default=5, ge=1, le=100)
    recall_threshold: float | None = Field(default=None, ge=0, le=1)
    recall_timeout: float = Field(default=2, gt=0, le=300)
    recall_required: bool = False


class ManagedMemoryBackend(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    provider_id: ObjectId


class InlineMemoryBackend(BaseModel):
    """A document Provider configured on the Agent instead of a saved Memory Provider."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    type: str = Field(min_length=1, max_length=128)
    configuration: dict[str, JsonValue] = Field(default_factory=dict)


class MemoryEntrySelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,23}$")
    mode: Literal["records", "documents"]
    description: str = Field(min_length=1, max_length=2000, pattern=r"\S")
    backend: ManagedMemoryBackend | InlineMemoryBackend
    scope: ScopeKind | None = None
    toolset: bool = True
    recall_required: bool = False
    auto_organize: bool = False
    auto_recall: bool = True
    recall_limit: int = Field(default=5, ge=1, le=100)
    recall_threshold: float | None = Field(default=None, ge=0, le=1)
    recall_timeout: float = Field(default=2, gt=0, le=300)

    @model_serializer(mode="wrap")
    def serialize_mode(self, handler):
        result = handler(self)
        excluded = (
            {"auto_organize"}
            if self.mode == "records"
            else {"auto_recall", "recall_limit", "recall_threshold", "recall_timeout"}
        )
        return {key: value for key, value in result.items() if key not in excluded}

    @model_validator(mode="after")
    def mode_options(self) -> MemoryEntrySelection:
        if self.mode == "records":
            if isinstance(self.backend, InlineMemoryBackend):
                raise ValueError("An inline backend requires documents mode")
            if "auto_organize" in self.model_fields_set:
                raise ValueError("auto_organize is a document option")
        elif self.model_fields_set & {"auto_recall", "recall_limit", "recall_threshold", "recall_timeout"}:
            raise ValueError("Recall options are only available in records mode")
        return self


class MemoryEntries(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    entries: tuple[MemoryEntrySelection, ...] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def unique_names(self) -> MemoryEntries:
        if len({entry.name for entry in self.entries}) != len(self.entries):
            raise ValueError("Memory entry names must be unique")
        return self


type MemoryConfiguration = MemorySelection | MemoryEntries


def memory_provider_ids(selection: MemoryConfiguration) -> tuple[str, ...]:
    if isinstance(selection, MemorySelection):
        return (selection.provider_id,)
    return tuple(
        dict.fromkeys(
            entry.backend.provider_id for entry in selection.entries if isinstance(entry.backend, ManagedMemoryBackend)
        )
    )


class MemoryScope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    scope: ScopeKind
    subject_id: ThreadId | None = None

    @model_validator(mode="after")
    def validate_subject(self) -> MemoryScope:
        if (self.scope is ScopeKind.USER) != (self.subject_id is None):
            raise ValueError("thread and agent scopes require subject_id; user scope uses the authenticated User")
        return self


class MemoryAccess(BaseModel):
    """Current subject permissions; each content operation authorizes again."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    can_write: bool


class MemoryWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    text: MemoryText


class MemorySearch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    query: Annotated[str, StringConstraints(min_length=1, max_length=16000)]
    limit: int = Field(default=20, ge=1, le=100)
    threshold: float | None = Field(default=None, ge=0, le=1)


class Memory(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(min_length=1, max_length=512)
    memory: str = Field(min_length=1, max_length=256 * 1024)
    score: float | None = Field(default=None, allow_inf_nan=False)


class MemoryPagination(BaseModel):
    """Native traversal; a null cursor means the final page of that traversal."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    next_cursor: str | None


class MemoryCollection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    items: tuple[Memory, ...]
    pagination: MemoryPagination | None = Field(
        default=None,
        description="Native pagination when available. Null means a bounded result, not a complete collection.",
    )


class CreateMemoryProviderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    type: str = Field(min_length=1, max_length=128)
    name: DisplayName
    configuration: dict[str, JsonValue] = Field(default_factory=dict)
    credential: dict[str, JsonValue] | None = Field(default=None, repr=False, json_schema_extra={"writeOnly": True})
    enabled: bool = True


class UpdateMemoryProviderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    name: DisplayName | None = None
    credential: dict[str, JsonValue] | None = Field(default=None, repr=False, json_schema_extra={"writeOnly": True})
    enabled: bool | None = None

    @model_validator(mode="after")
    def validate_changes(self) -> UpdateMemoryProviderRequest:
        if not self.model_fields_set:
            raise ValueError("at least one field must be supplied")
        values = {"name": self.name, "credential": self.credential, "enabled": self.enabled}
        if any(values[key] is None for key in self.model_fields_set - {"credential"}):
            raise ValueError("supplied fields cannot be null")
        return self


class MemoryProvider(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: ObjectId
    organization_id: ObjectId
    workspace_id: ObjectId | None
    type: str
    name: str
    configuration: dict[str, object]
    credential_configured: bool
    enabled: bool
    created_by: PrincipalRef
    updated_by: PrincipalRef
    created_at: datetime
    updated_at: datetime


class MemoryProviderCollection(BaseModel):
    items: tuple[MemoryProvider, ...]
    next_cursor: str | None = None


class MemoryProviderMetadata(ProviderMetadata):
    supports_documents: bool = False
    supports_records: bool = True
    supports_revisions: bool = False
    supports_changes: bool = False

    @classmethod
    def describe(cls, definition: MemoryProviderDefinition) -> MemoryProviderMetadata:
        return cls(
            **provider_metadata_core(definition),
            supports_documents=definition.supports_documents,
            supports_records=definition.supports_records,
            supports_revisions=definition.supports_revisions,
            supports_changes=definition.supports_changes,
        )


class MemoryProviderReference(BaseModel):
    agent_id: ObjectId
    agent_revision_id: ObjectId
    version: int
    is_current: bool


class MemoryProviderReferenceCollection(BaseModel):
    items: tuple[MemoryProviderReference, ...]
    next_cursor: str | None = None
