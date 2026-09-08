"""Public and application-domain values for immutable Assets."""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from a13n_service.digests import Sha256Digest
from a13n_service.iam.domain import PrincipalRef
from a13n_service.ids import ObjectId, new_object_id

_MIME_TOKEN = r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+"
_MEDIA_TYPE_PATTERN = re.compile(rf"^(?P<type>{_MIME_TOKEN})/(?P<subtype>{_MIME_TOKEN})$")


class AssetSourceKind(StrEnum):
    upload = "upload"
    run_output = "run_output"


class UploadedAssetSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal[AssetSourceKind.upload] = AssetSourceKind.upload
    principal: PrincipalRef


class RunOutputAssetSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal[AssetSourceKind.run_output] = AssetSourceKind.run_output
    run_id: ObjectId | None = None


AssetSource = Annotated[UploadedAssetSource | RunOutputAssetSource, Field(discriminator="kind")]


class Asset(BaseModel):
    """One exact immutable binary publication and its lifecycle marker."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: ObjectId
    organization_id: ObjectId
    workspace_id: ObjectId
    filename: str
    media_type: str
    size_bytes: int = Field(ge=0)
    content_sha256: Sha256Digest
    source: AssetSource
    created_at: datetime
    deleted_at: datetime | None

    @field_validator("filename")
    @classmethod
    def validate_filename(cls, value: str) -> str:
        return normalize_asset_filename(value)

    @field_validator("media_type")
    @classmethod
    def validate_media_type(cls, value: str) -> str:
        return normalize_media_type(value)


class AssetRef(BaseModel):
    """Bounded safe reference returned by Asset publication."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1"] = "1"
    asset_id: ObjectId
    filename: str
    media_type: str
    size_bytes: int = Field(ge=0)
    content_sha256: Sha256Digest

    @classmethod
    def from_asset(cls, asset: Asset) -> AssetRef:
        return cls(
            asset_id=asset.id,
            filename=asset.filename,
            media_type=asset.media_type,
            size_bytes=asset.size_bytes,
            content_sha256=asset.content_sha256,
        )


class AssetCollection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[Asset, ...]
    next_cursor: str | None


def new_asset_id() -> str:
    return new_object_id("ast")


def normalize_asset_filename(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("filename must be a string")
    normalized = unicodedata.normalize("NFC", value)
    if not 1 <= len(normalized) <= 256:
        raise ValueError("filename must contain 1 through 256 Unicode scalar values")
    if normalized != normalized.strip():
        raise ValueError("filename must not contain leading or trailing whitespace")
    if any(character in {"/", "\\"} or unicodedata.category(character) == "Cc" for character in normalized):
        raise ValueError("filename must not contain path separators or control characters")
    if any(0xD800 <= ord(character) <= 0xDFFF for character in normalized):
        raise ValueError("filename must contain only Unicode scalar values")
    return normalized


def normalize_media_type(value: str | None) -> str:
    candidate = "application/octet-stream" if value is None else value
    if not isinstance(candidate, str):
        raise ValueError("media_type must be a string")
    match = _MEDIA_TYPE_PATTERN.fullmatch(candidate)
    if match is None or "*" in candidate or len(candidate) > 255:
        raise ValueError("media_type must be a MIME media-type essence without parameters or wildcards")
    return candidate.lower()
