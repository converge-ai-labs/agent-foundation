"""DirectLocal shared implementation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .._backend import BackendReference
from ..errors import (
    EnvironmentProviderError,
    EnvironmentProviderErrorCategory,
    EnvironmentProviderErrorContext,
    EnvironmentProviderOutcomeCertainty,
    EnvironmentProviderRecoveryHint,
)
from ..models import (
    FILE_ACTIONS,
    EnvironmentAction,
    EnvironmentDescriptor,
    EnvironmentMountDescriptor,
    EnvironmentOperationFamily,
    EnvironmentPermissionSet,
)
from .configuration import DirectLocalEnvironmentConfiguration

_PROVIDER_KEY = "direct_local"


@dataclass(frozen=True, slots=True)
class _DirectLocalFilePolicy:
    max_value_bytes: int


@dataclass(frozen=True, slots=True)
class _DirectLocalProcessPolicy:
    allowed_executables: frozenset[Path]
    allowed_environment_keys: frozenset[str] | None
    inherit_environment: bool
    max_concurrent_processes: int
    max_wall_time_seconds: float
    terminate_grace_seconds: float


@dataclass(frozen=True, slots=True)
class _DirectLocalOutputPolicy:
    max_buffer_bytes: int
    max_spool_bytes: int


@dataclass(frozen=True, slots=True)
class _DirectLocalPortPolicy:
    allowed_ports: frozenset[int]


class DirectLocalReference(BackendReference):
    def __init__(self, configuration: DirectLocalEnvironmentConfiguration, *, environment_id: str) -> None:
        super().__init__(None)
        self._environment_id = environment_id
        self._configuration = configuration.model_copy(deep=True)

    @property
    def provider_key(self) -> str:
        return _PROVIDER_KEY

    @property
    def environment_id(self) -> str:
        return self._environment_id


def _resolve_shared_root(path: Path) -> Path:
    try:
        root = path.resolve(strict=True)
        if not root.is_dir():
            raise _operation_error("Direct Local root is not a directory.", "environment_request_invalid")
        return root
    except FileNotFoundError as error:
        raise _operation_error("Direct Local root does not exist.", "environment_not_found") from error
    except PermissionError as error:
        raise _operation_error("Direct Local root is not accessible.", "environment_denied") from error
    except OSError as error:
        raise _operation_error("Direct Local root could not be inspected.", "environment_provider_failure") from error


def _provider_error(
    message: str,
    *,
    code: str,
    category: EnvironmentProviderErrorCategory,
) -> EnvironmentProviderError:
    return EnvironmentProviderError(
        message,
        code=code,
        category=category,
        certainty=EnvironmentProviderOutcomeCertainty.NOT_DISPATCHED,
        recovery_hint=EnvironmentProviderRecoveryHint.FIX_INPUT,
        context=EnvironmentProviderErrorContext(provider_key=_PROVIDER_KEY),
    )


def _operation_error(message: str, code: str):
    from ..models import EnvironmentError

    return EnvironmentError(message, code=code)


def _descriptor(
    configuration: DirectLocalEnvironmentConfiguration, generation: str, *, backing_identity: str | None = None
) -> EnvironmentDescriptor:
    process_enabled = bool(configuration.allowed_executables or configuration.shell_profiles)
    permissions = set(FILE_ACTIONS)
    families: set[EnvironmentOperationFamily] = {"files"}
    if process_enabled:
        permissions.add(EnvironmentAction.SHELL_EXEC)
        families.add("shell")
        permissions.update(
            action
            for action in EnvironmentAction
            if action.value.startswith("environment.process.") and action != EnvironmentAction.PROCESS_LIST
        )
        permissions.update({EnvironmentAction.OUTPUT_READ, EnvironmentAction.OUTPUT_RELEASE})
        families.update({"processes", "outputs"})
    if configuration.allowed_ports:
        permissions.update({EnvironmentAction.PORT_INSPECT, EnvironmentAction.PORT_WAIT})
        families.add("ports")
    limits: dict[str, int | float] = {"max_value_bytes": configuration.max_value_bytes}
    if process_enabled:
        limits.update(
            max_wall_time_seconds=configuration.max_wall_time_seconds,
            max_buffer_bytes=configuration.max_buffer_bytes,
            max_spool_bytes=configuration.max_spool_bytes,
        )
    return EnvironmentDescriptor(
        generation=generation,
        backing_identity=backing_identity,
        operation_families=frozenset(families),
        permissions=EnvironmentPermissionSet(operations=frozenset(permissions)),
        limits=limits,
        mounts=(EnvironmentMountDescriptor(name="root", path="/"),),
    )
