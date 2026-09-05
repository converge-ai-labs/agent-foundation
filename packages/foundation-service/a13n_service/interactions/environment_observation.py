"""Live observations around fresh Harness Environment adapters."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol

from a13n_environment_provider import (
    Environment,
    EnvironmentError,
    EnvironmentProviderError,
)
from a13n_environment_provider.management import EnvironmentScope
from a13n_harness import EnvironmentAccess, EnvironmentEntry, EnvironmentMount
from a13n_logging import get_logger
from pydantic import JsonValue, TypeAdapter

logger = get_logger(__name__)
_JSON_LIST = TypeAdapter(list[JsonValue])

type EnvironmentHookName = Literal[
    "environment.preparation.started",
    "environment.preparation.ready",
    "environment.preparation.failed",
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


class EnvironmentObserver:
    """Project events without wrapping or controlling the adapter."""

    def __init__(
        self, environment: Environment, projector: EnvironmentHookProjector, *, access: EnvironmentAccess
    ) -> None:
        self.environment = environment
        self._projector = projector
        self._access = access
        self._correlation: tuple[str, str, str] | None = None

    def observe(self, event: str, scope: EnvironmentScope, error: BaseException | None) -> None:
        self._correlation = (scope.thread_id, scope.run_id, scope.mount_id)
        if event == "ready":
            self._emit_ready()
        elif event == "started":
            self._emit("environment.preparation.started", {"access": self._access.value})
        elif event == "failed":
            assert error is not None
            self._emit("environment.preparation.failed", {"failure": _safe_failure(error, phase="preparation")})
        elif event == "closed":
            fields: dict[str, JsonValue] = {"status": "failed" if error else "closed"}
            if error is not None:
                fields["failure"] = _safe_failure(error, phase="close")
            self._emit("environment.adapter.closed", fields)

    def _emit_ready(self) -> None:
        try:
            descriptor = self.environment.descriptor
            availability = self.environment.availability
            operation_families = _JSON_LIST.validate_python(sorted(descriptor.operation_families), strict=True)
            effective_permissions = descriptor.permissions.operations & self._access.permission_set().operations
            permissions = _JSON_LIST.validate_python(sorted(item.value for item in effective_permissions), strict=True)
            ready_families = _JSON_LIST.validate_python(sorted(availability.ready_families), strict=True)
            self._emit(
                "environment.preparation.ready",
                {
                    "operation_families": operation_families,
                    "permissions": permissions,
                    "availability": availability.status,
                    "ready_families": ready_families,
                },
            )
        except Exception:
            logger.exception(
                "Environment ready observation failed",
                extra={"event": "environment_ready_observation_failed"},
            )

    def _emit(self, event_type: EnvironmentHookName, fields: dict[str, JsonValue]) -> None:
        correlation = self._correlation
        if correlation is None:
            return
        thread_id, harness_run_id, mount_id = correlation
        try:
            payload: dict[str, JsonValue] = {
                "mount_id": mount_id,
                "provider_key": self.environment.provider_key,
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


def observe_environment_entry(
    entry: EnvironmentEntry,
    projector: EnvironmentHookProjector,
) -> EnvironmentEntry:
    mount = entry if isinstance(entry, EnvironmentMount) else EnvironmentMount(entry)
    observer = EnvironmentObserver(mount.environment, projector, access=mount.access)
    mount.environment.observe(observer.observe)
    return mount


def _safe_failure(error: BaseException, *, phase: Literal["preparation", "close"]) -> dict[str, JsonValue]:
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
    "observe_environment_entry",
]
