"""Portable package and provenance values owned by Skill Management."""

from __future__ import annotations

import unicodedata
from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints, field_validator

from a13n_service.iam.domain import PrincipalRef
from a13n_service.ids import new_object_id

Sha256Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
ObjectId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9]{1,7}_[a-z0-9]{16,64}$")]


def new_skill_id() -> str:
    return new_object_id("sk")


def new_skill_revision_id() -> str:
    return new_object_id("skr")


def new_skill_upload_id() -> str:
    return new_object_id("sku")


class SkillPackageFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=1, max_length=1024)
    size_bytes: int = Field(ge=0, le=16 * 1024 * 1024)
    sha256: Sha256Digest


class SkillPackageManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1"] = "1"
    skill_name: str = Field(min_length=1, max_length=256)
    description: str = Field(min_length=1, max_length=16 * 1024)
    harness_skill_contract: Literal["1"] = "1"
    files: tuple[SkillPackageFile, ...] = Field(min_length=1, max_length=4096)
    total_size_bytes: int = Field(ge=0, le=64 * 1024 * 1024)
    content_digest: Sha256Digest


class ZipSkillImportProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["zip"] = "zip"
    archive_sha256: Sha256Digest


class GitHubSkillImportProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["github"] = "github"
    repository_url: str = Field(min_length=1, max_length=2048)
    requested_ref: str | None = Field(default=None, min_length=1, max_length=1024)
    resolved_commit_sha: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]
    subdirectory: str = Field(max_length=1024)


SkillImportProvenance = Annotated[
    ZipSkillImportProvenance | GitHubSkillImportProvenance,
    Field(discriminator="kind"),
]


class GitHubRevisionSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["github"] = "github"
    repository_url: str = Field(min_length=1, max_length=2048)
    ref: str | None = Field(default=None, min_length=1, max_length=1024)
    subdirectory: str = Field(default="", max_length=1024)
    expected_commit_sha: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")] | None = None
    credential_secret_id: (
        Annotated[
            str,
            StringConstraints(pattern=r"^sec_[a-z0-9]{16,64}$"),
        ]
        | None
    ) = None


class ZipUploadSkillSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["zip_upload"] = "zip_upload"
    upload_id: Annotated[str, StringConstraints(pattern=r"^sku_[a-z0-9]{16,64}$")]


SkillRevisionSource = Annotated[
    ZipUploadSkillSource | GitHubRevisionSource,
    Field(discriminator="kind"),
]


def _normalize_name(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value)
    if not 1 <= len(normalized) <= 256:
        raise ValueError("name must contain between 1 and 256 Unicode scalar values")
    if normalized[0].isspace() or normalized[-1].isspace():
        raise ValueError("name must not have leading or trailing whitespace")
    if any(unicodedata.category(character) in {"Cc", "Cs"} for character in normalized):
        raise ValueError("name must not contain control or surrogate characters")
    return normalized


SkillName = Annotated[str, AfterValidator(_normalize_name)]


class CreateSkillRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: SkillName
    source: SkillRevisionSource


class CreateSkillRevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    source: SkillRevisionSource


class UpdateSkillRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: SkillName


class Skill(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: ObjectId
    organization_id: ObjectId
    workspace_id: ObjectId
    name: str
    version: int = Field(ge=1)
    current_revision_id: ObjectId
    created_at: datetime
    created_by: PrincipalRef
    updated_at: datetime
    updated_by: PrincipalRef
    deleted_at: datetime | None


class SkillRevision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: ObjectId
    skill_id: ObjectId
    organization_id: ObjectId
    workspace_id: ObjectId
    version: int = Field(ge=1)
    manifest: SkillPackageManifest
    imported_from: SkillImportProvenance
    created_at: datetime
    created_by: PrincipalRef


class SkillUploadReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    upload_id: ObjectId
    workspace_id: ObjectId
    archive_sha256: Sha256Digest
    manifest: SkillPackageManifest
    expires_at: datetime
    consumed_by_revision_id: ObjectId | None


class SkillPublicationReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    skill: Skill
    revision: SkillRevision
    outcome: Literal["published", "already_current"]


class SkillCollection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[Skill, ...]
    next_cursor: str | None


class SkillRevisionCollection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[SkillRevision, ...]
    next_cursor: str | None


class SkillRevisionLock(BaseModel):
    """Exact managed Skill identity copied into an immutable AgentRevision."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    skill_revision_id: ObjectId
    skill_name: str = Field(min_length=1, max_length=256)
    content_digest: Sha256Digest

    @field_validator("skill_name")
    @classmethod
    def _valid_skill_name(cls, value: str) -> str:
        _validate_skill_names((value,))
        return value


def _validate_skill_names(values: tuple[str, ...]) -> None:
    if len(values) != len(set(values)):
        raise ValueError("Skill names must be unique")
    for value in values:
        if not value.strip() or len(value) > 256 or "\x00" in value:
            raise ValueError("Skill names must be non-blank bounded strings without NUL")
