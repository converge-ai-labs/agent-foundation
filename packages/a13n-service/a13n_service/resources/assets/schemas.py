"""Asset requests and representations."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, JsonValue

from a13n_service.resources.uploads.schemas import FileName, UploadId

SourceKind = Literal["user_upload", "agent_run"]
AssetStatus = Literal["all", "active", "retired"]


class AssetCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    upload_id: UploadId
    name: FileName


class Asset(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    workspace_id: str
    name: str
    content_type: str
    size: int
    digest: str
    source: dict[str, JsonValue] | None
    retired_at: datetime | None
    version: int
    created_by_id: str
    created_at: datetime
    updated_at: datetime


class ListedAsset(Asset):
    creator_name: str
    thread_id: str | None
    session_id: str | None


class AssetPage(BaseModel):
    items: list[ListedAsset]
    next_cursor: str | None
    total_count: int


class StorageBytes(BaseModel):
    active_bytes: int
    retired_bytes: int
    staged_upload_bytes: int
    total_bytes: int


class CreatorStorage(StorageBytes):
    created_by_id: str
    creator_name: str


class WorkspaceStorage(StorageBytes):
    by_creator: list[CreatorStorage]
