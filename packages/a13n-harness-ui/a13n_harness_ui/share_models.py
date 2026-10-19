"""Detached values for temporary, resource-scoped browser collaboration."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from a13n_harness_ui.surfaces import SurfaceModel

ShareScope = Literal["project", "thread"]
SharePermission = Literal["view", "prompt"]


class ShareCreate(SurfaceModel):
    scope: ShareScope
    target_id: str = Field(min_length=1, max_length=128)
    permission: SharePermission = "view"
    lifetime_hours: Literal[1, 24, 168] = 24


class ShareUpdate(SurfaceModel):
    permission: SharePermission


class ShareGrant(SurfaceModel):
    share_id: str
    scope: ShareScope
    target_id: str
    permission: SharePermission
    created_at: datetime
    expires_at: datetime
    revoked_at: datetime | None = None


class ShareCreated(SurfaceModel):
    grant: ShareGrant
    key: str = Field(repr=False)


class ShareAccess(SurfaceModel):
    grant: ShareGrant
    title: str


class SharedThreadCreate(SurfaceModel):
    thread_id: str = Field(pattern=r"^thread[-_][0-9a-f]{32}$")
    title: str | None = Field(default=None, min_length=1, max_length=200)
