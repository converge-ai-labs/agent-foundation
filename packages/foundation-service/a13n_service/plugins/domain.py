"""Public Plugin and immutable PluginVersion resources."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from a13n_service.iam.domain import ObjectId

PLUGIN_ID_PREFIX = "plg"
PLUGIN_VERSION_ID_PREFIX = "plgv"
ContentDigest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
PluginKey = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_.-]{1,127}$")]


class PluginSource(StrEnum):
    builtin = "builtin"
    uploaded = "uploaded"


class PluginLifecycleState(StrEnum):
    available = "available"
    archived = "archived"


class DomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Plugin(DomainModel):
    id: ObjectId
    source: PluginSource
    plugin_key: PluginKey
    distribution_name: str = Field(min_length=1, max_length=256)
    top_level_package: str = Field(min_length=1, max_length=256)
    active_version_id: ObjectId | None
    lifecycle_state: PluginLifecycleState


class PluginVersion(DomainModel):
    id: ObjectId
    plugin_id: ObjectId
    version: str = Field(min_length=1, max_length=256)
    content_digest: ContentDigest
    artifact_ref: str = Field(min_length=1, max_length=1024)
    requires_dist: tuple[str, ...] = Field(default=(), max_length=512)
    status: Literal["ready"] = "ready"


class PluginCollection(DomainModel):
    items: tuple[Plugin, ...]
    next_cursor: str | None


class PluginVersionCollection(DomainModel):
    items: tuple[PluginVersion, ...]
    next_cursor: str | None
