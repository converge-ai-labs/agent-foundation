"""Detached planned-update handoff values; never a general execution queue."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from a13n_harness_ui.storage.objects import ObjectRef


class RestartPrincipal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    issuer: str
    subject: str
    claims: dict[str, str]


class RestartItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    thread_id: str
    run_id: str
    composition: ObjectRef
    checkpoint: ObjectRef
    execution_id: str | None = None
    parent_thread_id: str | None = None
    root_thread_id: str
    parent_composition: ObjectRef | None = None
    parent_run_id: str | None = None
    identity: RestartPrincipal | None = None
    parent_identity: RestartPrincipal | None = None
    usage_limits: dict[str, int | float | bool | None] | None = None


class MaintenanceTask(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    thread_id: str
    execution_id: str | None = None
    state: Literal["draining", "paused", "restoring", "restored", "blocked"]
    message: str | None = None
    resumed_run_id: str | None = None
    resumed_execution_id: str | None = None


class RestartBatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    batch_id: str
    owner_id: str
    state: Literal["preparing", "ready", "claimed", "finished", "blocked"]
    items: tuple[RestartItem, ...] = ()
    results: tuple[MaintenanceTask, ...] = ()
    error: str | None = None


class DismissUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    previous_instance_stopped: Literal[True]


class MaintenanceView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool
    phase: Literal["idle", "draining", "paused", "stopping", "restoring", "finished", "blocked"] = "idle"
    batch_id: str | None = None
    tasks: tuple[MaintenanceTask, ...] = ()
    message: str | None = None
    can_cancel: bool = False
    can_dismiss: bool = False
