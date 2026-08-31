"""Portable package and provenance values owned by Skill Management."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Sha256Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class ManagedSkillPackageFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=1, max_length=1024)
    size_bytes: int = Field(ge=0, le=16 * 1024 * 1024)
    sha256: Sha256Digest


class ManagedSkillPackageManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1"] = "1"
    skill_name: str = Field(min_length=1, max_length=256)
    description: str = Field(min_length=1, max_length=16 * 1024)
    harness_skill_contract: Literal["1"] = "1"
    files: tuple[ManagedSkillPackageFile, ...] = Field(min_length=1, max_length=4096)
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
