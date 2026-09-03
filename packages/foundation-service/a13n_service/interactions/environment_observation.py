"""Exact live observations around fresh Harness Environment adapters."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol

from a13n_environment_provider import (
    Environment,
    EnvironmentAvailability,
    EnvironmentDescriptor,
    EnvironmentError,
    EnvironmentOperationFamily,
    EnvironmentOperations,
    EnvironmentProviderError,
    EnvironmentState,
)
from a13n_harness import EnvironmentAccess, EnvironmentEntry, EnvironmentMount
from a13n_harness.environment.entry_observation import EnvironmentEntryValidationObserver
from pydantic import JsonValue, TypeAdapter

logger = logging.getLogger("a13n_service.interactions.environment_observation")
_JSON_LIST = TypeAdapter(list[JsonValue])

type EnvironmentHookName = Literal[
    "environment.entry.started",
    "environment.entry.ready",
    "environment.entry.failed",
    "environment.adapter.closed",
]


@dataclass(frozen=True, slots=True)
class EnvironmentHookObservation:
    event_type: EnvironmentHookName
    thread_id: str
    harness_run_id: str
    mount_id: str
    occurred_at: datetime
    payload: Mapping[str, JsonValue]


class EnvironmentHookProjector(Protocol):
    def project_environment(self, observation: EnvironmentHookObservation) -> None: ...


class ObservedEnvironment(Environment, EnvironmentEntryValidationObserver):
    """Preserve one adapter exactly while observing its owned lifecycle calls."""

    def __init__(
        self,
        delegate: Environment,
        projector: EnvironmentHookProjector,
        *,
        access: EnvironmentAccess,
    ) -> None:
        super().__init__(delegate.dump_state())
        self._delegate = delegate
        self._projector = projector
        self._access = access
        self._correlation: tuple[str, str, str] | None = None

    @property
    def provider_key(self) -> str:
        return self._delegate.provider_key

    @property
    def environment_id(self) -> str:
        return self._delegate.environment_id

    @property
    def descriptor(self) -> EnvironmentDescriptor:
        return self._delegate.descriptor

    @property
    def availability(self) -> EnvironmentAvailability:
        return self._delegate.availability

    @property
    def operations(self) -> EnvironmentOperations:
        return self._delegate.operations

    def dump_state(self) -> EnvironmentState | None:
        return self._delegate.dump_state()

    async def _enter(
        self,
        *,
        thread_id: str,
        run_id: str,
        agent_instance_id: str,
        mount_id: str,
        host_refs: Mapping[str, str],
    ) -> None:
        self._correlation = (thread_id, run_id, mount_id)
        self._emit(
            "environment.entry.started",
            {
                "access": self._access.value,
            },
        )
        try:
            await self._delegate.enter(
                thread_id=thread_id,
                run_id=run_id,
                agent_instance_id=agent_instance_id,
                mount_id=mount_id,
                host_refs=host_refs,
            )
        except BaseException as error:
            self._emit(
                "environment.entry.failed",
                {
                    "failure": _safe_failure(error, phase="entry"),
                },
            )
            raise

    def environment_entry_validated(
        self,
        *,
        mount_id: str,
        descriptor: EnvironmentDescriptor,
        availability: EnvironmentAvailability,
    ) -> None:
        self._require_mount_id(mount_id)
        operation_families = _JSON_LIST.validate_python(sorted(descriptor.operation_families), strict=True)
        effective_permissions = descriptor.permissions.operations & self._access.permission_set().operations
        permissions = _JSON_LIST.validate_python(sorted(item.value for item in effective_permissions), strict=True)
        ready_families = _JSON_LIST.validate_python(sorted(availability.ready_families), strict=True)
        self._emit(
            "environment.entry.ready",
            {
                "operation_families": operation_families,
                "permissions": permissions,
                "availability": availability.status,
                "ready_families": ready_families,
            },
        )

    def environment_entry_rejected(self, *, mount_id: str, error: BaseException) -> None:
        self._require_mount_id(mount_id)
        self._emit(
            "environment.entry.failed",
            {
                "failure": _safe_failure(error, phase="entry"),
            },
        )

    async def _ensure_ready(self, operations: frozenset[EnvironmentOperationFamily]) -> None:
        await self._delegate.ensure_ready(operations)

    async def _close(self) -> None:
        try:
            await self._delegate.close()
        except BaseException as error:
            self._emit(
                "environment.adapter.closed",
                {
                    "status": "failed",
                    "failure": _safe_failure(error, phase="close"),
                },
            )
            raise
        self._emit("environment.adapter.closed", {"status": "closed"})

    async def _destroy(self) -> None:
        await self._delegate.destroy()

    def _emit(self, event_type: EnvironmentHookName, fields: dict[str, JsonValue]) -> None:
        correlation = self._correlation
        if correlation is None:
            return
        thread_id, harness_run_id, mount_id = correlation
        try:
            payload: dict[str, JsonValue] = {
                "mount_id": mount_id,
                "provider_key": self.provider_key,
                **fields,
            }
            self._projector.project_environment(
                EnvironmentHookObservation(
                    event_type=event_type,
                    thread_id=thread_id,
                    harness_run_id=harness_run_id,
                    mount_id=mount_id,
                    occurred_at=datetime.now(UTC),
                    payload=payload,
                )
            )
        except Exception:
            logger.exception(
                "Environment live observation projection failed",
                extra={
                    "event": "environment_live_projection_failed",
                    "harness_run_id": harness_run_id,
                    "mount_id": mount_id,
                    "hook_name": event_type,
                },
            )

    def _require_mount_id(self, mount_id: str) -> None:
        correlation = self._correlation
        if correlation is None or correlation[2] != mount_id:
            raise RuntimeError("Environment validation observation does not match its entry")


def observe_environment_entry(
    entry: EnvironmentEntry,
    projector: EnvironmentHookProjector,
) -> EnvironmentEntry:
    mount = entry if isinstance(entry, EnvironmentMount) else EnvironmentMount(entry)
    return EnvironmentMount(
        ObservedEnvironment(mount.environment, projector, access=mount.access),
        access=mount.access,
        working_directory=mount.working_directory,
    )


def _safe_failure(error: BaseException, *, phase: Literal["entry", "close"]) -> dict[str, JsonValue]:
    if isinstance(error, EnvironmentProviderError):
        safe = error.safe_projection()
        return {"code": safe.code, "message": safe.message}
    if isinstance(error, EnvironmentError):
        return {"code": error.code, "message": f"The Environment {phase} operation failed."}
    return {
        "code": f"environment_{phase}_failed",
        "message": f"The Environment {phase} operation failed.",
    }


__all__ = [
    "EnvironmentHookObservation",
    "EnvironmentHookProjector",
    "ObservedEnvironment",
    "observe_environment_entry",
]
