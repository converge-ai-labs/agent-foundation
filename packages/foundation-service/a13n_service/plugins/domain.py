"""Public Plugin and immutable PluginVersion resources."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from a13n_harness import SafeFailure
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from a13n_service.iam.domain import ObjectId, ResourceRef

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


class PluginTaskStatus(StrEnum):
    running = "running"
    succeeded = "succeeded"
    failed = "failed"


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


class PluginTaskReceipt(DomainModel):
    operation_id: ObjectId
    status: PluginTaskStatus
    result_refs: tuple[ResourceRef, ...] = Field(default=(), max_length=8)
    error: SafeFailure | None = None

    @model_validator(mode="after")
    def validate_status_shape(self) -> PluginTaskReceipt:
        if self.status is PluginTaskStatus.running and (self.result_refs or self.error is not None):
            raise ValueError("running Plugin tasks cannot carry results or errors")
        if self.status is PluginTaskStatus.failed and (self.result_refs or self.error is None):
            raise ValueError("failed Plugin tasks require exactly one safe error")
        if self.status is PluginTaskStatus.succeeded and self.error is not None:
            raise ValueError("succeeded Plugin tasks cannot carry an error")
        return self
