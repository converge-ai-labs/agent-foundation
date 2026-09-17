"""Immutable document commands and safe Bot memory projections."""

from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator

from a13n_service.ids import ObjectId
from a13n_service.memory.domain import MemoryText


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MemorySettings(StrictModel):
    provider_id: ObjectId
    use_memory: bool = True
    save_on_request: bool = True
    timezone: str = Field(default="UTC", max_length=128)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError) as error:
            raise ValueError("Select an IANA time zone") from error
        return value


class ScopeSettings(StrictModel):
    visibility: Literal["group", "installation"] = "group"
    enabled: bool = True
    use_memory: bool = True
    save_on_request: bool = True
    timezone: str = Field(default="UTC", max_length=128)

    _timezone = field_validator("timezone")(MemorySettings.valid_timezone.__func__)


class ConfigureScope(ScopeSettings):
    external_conversation_id: str = Field(min_length=1, max_length=2048)
    # Audience is established by the verified platform observation, never this command.
    expected_version: int | None = Field(default=None, ge=1)


class Scope(ScopeSettings):
    id: ObjectId
    account_id: ObjectId
    provider_id: ObjectId
    external_conversation_id: str
    name: str
    audience: Literal["public", "private", "direct", "unknown"]
    version: int


class ScopeCollection(StrictModel):
    items: tuple[Scope, ...]
    next_cursor: str | None = None


class CreateDocument(StrictModel):
    text: MemoryText
    title: str = Field(min_length=1, max_length=160, pattern=r"\S")
    description: str = Field(default="", max_length=320)
    kind: Literal["daily", "long_term"] = "long_term"
    activity_date: date | None = None
    correction_of: ObjectId | None = None


class DocumentEntry(StrictModel):
    version: int = 1
    id: ObjectId
    scope_id: ObjectId
    path: str
    title: str
    description: str
    kind: Literal["daily", "long_term"]
    activity_date: date
    timezone: str
    saved_at: datetime | None
    state: Literal["pending", "active", "unconfirmed", "deleting", "deleted"]
    correction_of: ObjectId | None = None
    publication_source_id: ObjectId | None = None
    shared: bool = False


class DocumentAccessReason(StrictModel):
    kind: Literal["owner", "installation"]


class Document(DocumentEntry):
    text: str
    owner_name: str | None = None
    access_reasons: tuple[DocumentAccessReason, ...] = ()


class DocumentCollection(StrictModel):
    items: tuple[DocumentEntry, ...]
    next_cursor: str | None = None


class MemoryIndex(StrictModel):
    path: Literal["MEMORY.md"] = "MEMORY.md"
    text: str
    entries: tuple[DocumentEntry, ...]
    next_cursor: str | None = None


class SearchDocuments(StrictModel):
    query: str = Field(min_length=1, max_length=16000)
    limit: int = Field(default=20, ge=1, le=100)
    include_shared: bool = True
