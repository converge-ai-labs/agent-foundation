"""Typed current-context GitHub native-action contracts."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

BoundedMarkdown = Annotated[str, StringConstraints(min_length=1, max_length=65_536)]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class GitHubActionBinding(_StrictModel):
    repository_id: int = Field(gt=0, repr=False)
    owner: str = Field(min_length=1, max_length=256, repr=False)
    repository: str = Field(min_length=1, max_length=256, repr=False)
    number: int = Field(gt=0, repr=False)
    target_kind: Literal["issue", "pull_request"]


class GitHubAddCommentArguments(_StrictModel):
    body: BoundedMarkdown = Field(repr=False)


class GitHubCommentReceipt(_StrictModel):
    comment_id: int = Field(gt=0, repr=False)
    node_id: str = Field(min_length=1, max_length=512, repr=False)
    html_url: str = Field(min_length=1, max_length=2048, repr=False)
    request_id: str = Field(min_length=1, max_length=128)


class GitHubAddCommentSucceeded(_StrictModel):
    kind: Literal["succeeded"] = "succeeded"
    receipt: GitHubCommentReceipt


class GitHubAddCommentOutcomeUnknown(_StrictModel):
    kind: Literal["outcome_unknown"] = "outcome_unknown"
    request_id: str = Field(min_length=1, max_length=128)


type GitHubAddCommentOutcome = GitHubAddCommentSucceeded | GitHubAddCommentOutcomeUnknown


class GitHubReadCommentsArguments(_StrictModel):
    page: int = Field(default=1, ge=1, le=10_000)
    per_page: int = Field(default=30, ge=1, le=50)


class GitHubComment(_StrictModel):
    comment_id: int = Field(gt=0, repr=False)
    node_id: str = Field(min_length=1, max_length=512, repr=False)
    author_login: str | None = Field(default=None, max_length=256)
    author_id: int | None = Field(default=None, gt=0)
    body: str | None = Field(default=None, max_length=65_536, repr=False)
    html_url: str = Field(min_length=1, max_length=2048, repr=False)
    created_at: str | None = Field(default=None, max_length=64)
    updated_at: str | None = Field(default=None, max_length=64)


class GitHubCommentPage(_StrictModel):
    items: tuple[GitHubComment, ...]
    page: int
    has_more: bool


class GitHubReadTargetArguments(_StrictModel):
    include_body: bool = True
    include_labels: bool = True


class GitHubTarget(_StrictModel):
    number: int = Field(gt=0, repr=False)
    kind: Literal["issue", "pull_request"]
    title: str = Field(max_length=40_000, repr=False)
    body: str | None = Field(default=None, max_length=262_144, repr=False)
    state: str = Field(min_length=1, max_length=128)
    labels: tuple[str, ...] = Field(max_length=256)
    html_url: str = Field(min_length=1, max_length=2048, repr=False)
    draft: bool | None = None
    merged: bool | None = None


class GitHubListPrFilesArguments(_StrictModel):
    page: int = Field(default=1, ge=1, le=10_000)
    per_page: int = Field(default=30, ge=1, le=50)


class GitHubPrFile(_StrictModel):
    filename: str = Field(min_length=1, max_length=4096)
    status: str = Field(min_length=1, max_length=128)
    additions: int = Field(ge=0)
    deletions: int = Field(ge=0)
    changes: int = Field(ge=0)
    patch: str | None = Field(default=None, max_length=16_384, repr=False)
    patch_truncated: bool


class GitHubPrFilePage(_StrictModel):
    items: tuple[GitHubPrFile, ...]
    page: int
    has_more: bool
    patches_truncated: bool
