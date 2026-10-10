"""Developer-facing Environment inputs and aggregate normalization."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from a13n_environment.execution import EnvironmentConnector
from a13n_environment.models import (
    FILE_EXECUTION_ACTIONS,
    EnvironmentDescriptor,
    EnvironmentError,
    EnvironmentPermissionSet,
    EnvironmentState,
)

from ._mount_path import parse_mount_path, validate_working_directory
from .providers import EnvironmentRuntime

_DEFAULT_ACTIONS = EnvironmentPermissionSet(operations=FILE_EXECUTION_ACTIONS)


@runtime_checkable
class EnvironmentSource(Protocol):
    """Host-owned selection and readiness; metadata access never activates a target."""

    @property
    def provider_key(self) -> str: ...

    @property
    def environment_id(self) -> str: ...

    @property
    def descriptor(self) -> EnvironmentDescriptor: ...

    @property
    def state(self) -> EnvironmentState | None: ...

    async def ensure_ready(self) -> EnvironmentConnector: ...


@dataclass(frozen=True, slots=True)
class EnvironmentScope:
    thread_id: str
    run_id: str
    agent_instance_id: str
    mount_id: str


@dataclass(frozen=True, slots=True)
class EnvironmentMount:
    """One Host source plus Run-local permission and path policy."""

    source: EnvironmentSource
    permission_ceiling: EnvironmentPermissionSet = _DEFAULT_ACTIONS
    working_directory: str | None = None
    mount_path: str | None = None
    provider_root: str = "/"
    observer: Callable[[str, EnvironmentScope, BaseException | None], None] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.source, EnvironmentSource):
            raise TypeError("EnvironmentMount source must implement EnvironmentSource")
        if not isinstance(self.permission_ceiling, EnvironmentPermissionSet):
            raise TypeError("EnvironmentMount permission_ceiling must be an EnvironmentPermissionSet")
        if self.working_directory is None:
            object.__setattr__(self, "working_directory", self.source.descriptor.working_directory)
        validate_working_directory(self.working_directory)
        validate_working_directory(self.provider_root)
        if self.mount_path is not None:
            parse_mount_path(self.mount_path)


type EnvironmentEntry = EnvironmentSource | EnvironmentMount


def normalize_environment_inputs(
    *,
    environment: EnvironmentEntry | None,
    environments: Mapping[str, EnvironmentEntry] | None,
    default_environment: str | None,
    advanced_binding: EnvironmentRuntime | None,
) -> EnvironmentRuntime:
    from .coordinator import create_empty_environment_runtime, create_environment_runtime

    if environment is not None and environments is not None:
        raise EnvironmentError(
            "environment and environments are mutually exclusive.", code="environment_request_invalid"
        )
    if default_environment is not None and environments is None:
        raise EnvironmentError(
            "default_environment is valid only with environments.", code="environment_request_invalid"
        )
    if advanced_binding is not None and (environment is not None or environments is not None):
        raise EnvironmentError(
            "High-level Environment inputs conflict with RunBindings.environment.",
            code="environment_request_invalid",
        )
    if environment is None and environments is None:
        return advanced_binding or create_empty_environment_runtime()

    if environment is not None:
        entries = (("workspace", _normalize_entry(environment)),)
        default_alias = "workspace"
    else:
        if not isinstance(environments, Mapping) or not environments:
            raise EnvironmentError("environments must be a non-empty mapping.", code="environment_request_invalid")
        entries = tuple((alias, _normalize_entry(entry)) for alias, entry in environments.items())
        aliases = {alias for alias, _entry in entries}
        if default_environment is not None and default_environment not in aliases:
            raise EnvironmentError(
                "default_environment is not present in environments.", code="environment_request_invalid"
            )
        default_alias = (
            default_environment if default_environment is not None else entries[0][0] if len(entries) == 1 else None
        )

    return create_environment_runtime(mounts=dict(entries), default_mount=default_alias)


def _normalize_entry(entry: EnvironmentEntry) -> EnvironmentMount:
    if isinstance(entry, EnvironmentMount):
        return entry
    if isinstance(entry, EnvironmentSource):
        return EnvironmentMount(entry)
    raise EnvironmentError(
        "Environment inputs must be EnvironmentSource or EnvironmentMount values.",
        code="environment_request_invalid",
    )
