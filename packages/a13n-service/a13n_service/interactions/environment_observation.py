"""Live observations around fresh Harness Environment adapters."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal, Protocol

from a13n_harness import EnvironmentAccess, EnvironmentEntry, EnvironmentMount
from a13n_harness.environment.sources import EnvironmentScope
from a13n_harness.providers.environment.errors import EnvironmentProviderError
from a13n_harness.providers.environment.models import EnvironmentError
from a13n_logging import get_logger
from pydantic import JsonValue, TypeAdapter

from a13n_service.temporal import utc_now

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

    def __init__(self, mount: EnvironmentMount, projector: EnvironmentHookProjector) -> None:
        self._mount = mount
        self._projector = projector
        self._correlation: tuple[str, str, str] | None = None

    def observe(self, event: str, scope: EnvironmentScope, error: BaseException | None) -> None:
        self._correlation = (scope.thread_id, scope.run_id, scope.mount_id)
        if event == "ready":
            self._emit_ready()
        elif event == "started":
            access = self._mount.access
            projection: JsonValue = (
                access.value
                if isinstance(access, EnvironmentAccess)
                else {"operations": _JSON_LIST.validate_python(sorted(item.value for item in access.operations))}
            )
            self._emit("environment.preparation.started", {"access": projection})
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
            descriptor = self._mount.environment.descriptor
            availability = self._mount.environment.availability
            operation_families = _JSON_LIST.validate_python(sorted(descriptor.operation_families), strict=True)
            effective_permissions = descriptor.permissions.operations & self._mount.permissions.operations
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
                "provider_key": self._mount.environment.provider_key,
                **fields,
            }
            self._projector.project_environment(
                EnvironmentHookObservation(
                    event_type=event_type,
                    thread_id=thread_id,
                    harness_run_id=harness_run_id,
                    mount_id=mount_id,
                    occurred_at=utc_now(),
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


def observe_environment_entry(entry: EnvironmentEntry, projector: EnvironmentHookProjector) -> EnvironmentMount:
    """Declare live observation on the mount, before the Run binds and enters it."""
    mount = entry if isinstance(entry, EnvironmentMount) else EnvironmentMount(entry)
    return replace(mount, observer=EnvironmentObserver(mount, projector).observe)


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
