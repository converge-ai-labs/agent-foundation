"""Durable Thread bookmarks and the detached WebUI status view."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ThreadLinkKind = Literal["worktree", "url"]


class _Link(BaseModel):
    model_config = ConfigDict(frozen=True)

    label: str | None
    created_at: datetime
    updated_at: datetime


class WorktreeLink(_Link):
    kind: Literal["worktree"] = "worktree"
    path: str


class UrlLink(_Link):
    kind: Literal["url"] = "url"
    url: str


type ThreadLink = WorktreeLink | UrlLink


class WorktreeLinkView(BaseModel):
    path: str
    label: str | None = None
    implicit: bool = False
    state: Literal["unavailable", "ready", "detached", "removed"] = "unavailable"
    branch: str | None = None
    head_oid: str | None = None


class GitHubLinkView(BaseModel):
    kind: Literal["pr", "issue", "discussion"]
    number: int
    status: Literal["open", "draft", "merged", "closed", "answered"] | None = None
    title: str | None = None


class UrlLinkView(UrlLink):
    github: GitHubLinkView | None = None


class ThreadLinksView(BaseModel):
    worktrees: tuple[WorktreeLinkView, ...]
    urls: tuple[UrlLinkView, ...]
    worktree_mode_available: bool


class UnlinkThreadResource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ThreadLinkKind
    target: str = Field(min_length=1, max_length=4096)
