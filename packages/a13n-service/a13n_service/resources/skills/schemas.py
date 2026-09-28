"""Skill requests, the frozen revision manifest and representations."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from a13n_service.infra.ids import ObjectId
from a13n_service.infra.labels import Labels
from a13n_service.resources.uploads.schemas import Digest, UploadId

SkillName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)]
Description = Annotated[str, StringConstraints(max_length=16384)]
Repository = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}$")]
Commit = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]
GitRef = Annotated[str, StringConstraints(min_length=1, max_length=255, pattern=r"^[^\x00-\x20~^:?*\[\\]+$")]


class UploadSource(BaseModel):
    """A package archive staged through `/uploads`."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["upload"]
    upload_id: UploadId


class GitHubSource(BaseModel):
    """A directory of a public GitHub repository.

    `ref` defaults to the default branch. On a request `commit` is an optional expectation the resolved ref
    must meet; in a manifest it records the commit the package was read from.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["github"]
    repository: Repository
    ref: GitRef | None = None
    path: str = Field(default="", max_length=1024)
    commit: Commit | None = None

    @field_validator("path")
    @classmethod
    def relative_directory(cls, value: str) -> str:
        path = value.strip("/")
        if path and any(segment in {"", ".", ".."} or "\\" in segment for segment in path.split("/")):
            raise ValueError("path must be a relative directory")
        return path


PackageSource = Annotated[UploadSource | GitHubSource, Field(discriminator="kind")]
# The `kind` of a package source, by which skill lists filter.
SourceKind = Literal["upload", "github"]


class SkillFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    path: str
    size: int


class SkillManifest(BaseModel):
    """The frozen configuration of a skill revision: what its SKILL.md declares and the exact package bytes."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str
    description: str
    root: str = Field(description='The archive directory holding SKILL.md: "" or "<directory>/"')
    files: tuple[SkillFile, ...]
    size: int = Field(description="Expanded bytes of all files")
    package_digest: Digest
    package_size: int
    source: PackageSource


class SkillPin(BaseModel):
    """An agent revision's edge to one exact revision of a skill."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    skill_id: ObjectId
    revision_id: ObjectId


class SkillCreate(BaseModel):
    """`name` and `description` default to what the package's SKILL.md declares."""

    model_config = ConfigDict(extra="forbid")
    name: SkillName | None = None
    description: Description | None = None
    labels: Labels = Field(default_factory=dict)
    source: PackageSource


class SkillUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: SkillName | None = None
    description: Description | None = None
    labels: Labels | None = None


class SkillRevisionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: PackageSource
    note: str | None = Field(default=None, max_length=2048)
    make_default: bool = True


class SkillValidate(BaseModel):
    """A package to read and check as creating a skill or revision would, storing nothing."""

    model_config = ConfigDict(extra="forbid")
    source: PackageSource


class SkillRevisionSummary(BaseModel):
    """What a skill's representation shows of its default revision."""

    id: str
    number: int
    source: PackageSource


class Skill(BaseModel):
    id: str
    organization_id: str
    workspace_id: str
    name: str
    description: str
    labels: dict[str, str]
    default_revision_id: str | None
    default_revision: SkillRevisionSummary | None
    archived_at: datetime | None
    version: int
    created_by_id: str
    updated_by_id: str
    created_at: datetime
    updated_at: datetime


class SkillRevision(BaseModel):
    id: str
    skill_id: str
    workspace_id: str
    number: int
    config: SkillManifest
    digest: str
    note: str | None
    created_by_id: str
    created_at: datetime


class SkillPage(BaseModel):
    items: list[Skill]
    next_cursor: str | None


class SkillRevisionPage(BaseModel):
    items: list[SkillRevision]
    next_cursor: str | None
