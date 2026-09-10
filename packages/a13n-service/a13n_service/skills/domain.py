"""Portable package and provenance values owned by Skill Management."""

from __future__ import annotations

import unicodedata
from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

from a13n_service.digests import Sha256Digest
from a13n_service.iam.domain import PrincipalRef
from a13n_service.ids import ObjectId, new_object_id

SKILL_KEY_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
SkillKey = Annotated[
    str,
    StringConstraints(pattern=SKILL_KEY_PATTERN, min_length=1, max_length=64),
]


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
    skill_name: SkillKey
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

    name: SkillName | None = None
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
    key: SkillKey
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


class SkillListItem(Skill):
    source_kind: Literal["zip", "github"]


class SkillCollection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[SkillListItem, ...]
    next_cursor: str | None


class SkillRevisionCollection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[SkillRevision, ...]
    next_cursor: str | None


class SkillAgentReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_id: ObjectId
    agent_revision_id: ObjectId
    agent_name: str = Field(min_length=1, max_length=128)
    agent_key: str


class SkillAgentReferenceCollection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[SkillAgentReference, ...]
    next_cursor: str | None


class SkillRevisionLock(BaseModel):
    """Exact managed Skill revision frozen for one accepted Run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    skill_id: ObjectId
    skill_revision_id: ObjectId
    skill_key: SkillKey
    version: int = Field(ge=1)
    content_digest: Sha256Digest
