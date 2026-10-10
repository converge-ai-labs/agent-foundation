"""Immutable mount publications and provider artifact validation."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from typing import Any

from a13n_environment.commands import (
    BoundProcessHandle,
)
from a13n_environment.computer import ComputerObservation
from a13n_environment.models import EnvironmentError, EnvironmentOperationReceipt, EnvironmentPermissionSet
from a13n_environment.operations import EnvironmentOperations as EnvironmentProviderOperations
from a13n_environment.retention import (
    BoundOutputCursor,
    BoundOutputReference,
)
from pydantic import BaseModel

from a13n_harness.environment.models import EnvironmentMountInfo

from ._activation import _MountExecution
from .sources import EnvironmentMount


@dataclass(frozen=True, slots=True)
class _MountRequest:
    name: str
    permission_ceiling: EnvironmentPermissionSet
    default_working_directory: str | None
    mount_path: str | None
    mount: EnvironmentMount
    provider_root: str = "/"


@dataclass(frozen=True, slots=True)
class _EnteredMount:
    mount_id: str
    configured: EnvironmentMountInfo
    provider: _MountExecution
    environment_id: str

    operations: EnvironmentProviderOperations = field(init=False)
    public: EnvironmentMountInfo = field(init=False)

    def __post_init__(self) -> None:
        descriptor = self.provider.descriptor
        if not descriptor.permissions.operations <= self.configured.descriptor.permissions.operations:
            raise EnvironmentError("Provider broadened configured permissions", code="environment_provider_failure")
        public = self.configured.model_copy(
            update={
                "descriptor": descriptor,
                "provider_type": self.provider.provider_key,
                "permission_ceiling": EnvironmentPermissionSet(
                    operations=self.configured.permission_ceiling.operations & descriptor.permissions.operations
                ),
            }
        )
        object.__setattr__(self, "public", public)
        object.__setattr__(self, "operations", self.provider.operations)


@dataclass(frozen=True, slots=True)
class _ResolvedPath:
    entered: _EnteredMount
    provider_path: str
    mount_path: str


type _MountKey = str


@dataclass(slots=True)
class _OwnedProviderScope:
    entered: _EnteredMount
    scope: AbstractAsyncContextManager[_MountExecution]


def _validate_provider_artifacts(entered: _EnteredMount, value: Any) -> None:
    bound_types = (
        ComputerObservation,
        BoundProcessHandle,
        BoundOutputReference,
        BoundOutputCursor,
        EnvironmentOperationReceipt,
    )
    if isinstance(value, bound_types):
        if (
            value.execution_id != entered.provider.execution_id
            or value.observed_generation != entered.public.descriptor.generation
        ):
            raise EnvironmentError(
                "Environment provider returned an artifact for another mount incarnation.",
                code="environment_provider_failure",
            )
        return
    if isinstance(value, BaseModel):
        for name in type(value).model_fields:
            _validate_provider_artifacts(entered, getattr(value, name))
        return
    if isinstance(value, Mapping):
        for item in value.values():
            _validate_provider_artifacts(entered, item)
        return
    if isinstance(value, tuple | list):
        for item in value:
            _validate_provider_artifacts(entered, item)


def _validate_process_result_identity(expected: BoundProcessHandle, value: Any) -> None:
    if isinstance(value, BoundProcessHandle):
        if value != expected:
            raise EnvironmentError(
                "Environment provider retargeted a process operation to another handle.",
                code="environment_provider_failure",
            )
        return
    if isinstance(value, BaseModel):
        for name in type(value).model_fields:
            _validate_process_result_identity(expected, getattr(value, name))
        return
    if isinstance(value, Mapping):
        for item in value.values():
            _validate_process_result_identity(expected, item)
        return
    if isinstance(value, tuple | list):
        for item in value:
            _validate_process_result_identity(expected, item)
