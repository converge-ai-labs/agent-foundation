"""Service relay bound to the shared SDK's one ready EIP Session."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from a13n_envd_client import EIPSession
from a13n_harness.providers.environment.eip.binding import EIPEnvironmentSession
from a13n_harness.providers.environment.models import (
    ENVIRONMENT_ACTION_DISPATCH,
    EnvironmentAction,
    EnvironmentError,
    EnvironmentPermissionSet,
)
from a13n_harness.providers.environment.remote_envd.connections import WEBSOCKET_PROVIDER_KEY
from pydantic import JsonValue

from ..domain import DomainModel
from .relay_commands import CommandRelayDispatch
from .relay_files import FileRelayDispatch
from .relay_protocol import DEFAULT_RELAY_LIMITS, ReadinessRequest, RelayEnvironmentSnapshot, RelayLimits, RelayRequest
from .relay_transfers import FileTransferPlan


@dataclass(frozen=True, slots=True)
class _MountBinding:
    name: str
    session: EIPEnvironmentSession


class EnvironmentRelayDispatch:
    """Keep mount-local opaque handles on Control without translating EIP twice.

    Preparation can precede Harness binding. Every operation captures its local
    mount identity, so a later local bind cannot retarget in-flight operations or
    handles. All these bounded bindings still belong to one exclusive Attempt use
    and one physical EIP Session; none survive its carrier.
    """

    def __init__(
        self,
        session: EIPSession,
        environment_id: str,
        authorize_mount: Callable[[str], Awaitable[frozenset[EnvironmentAction]]],
        *,
        limits: RelayLimits = DEFAULT_RELAY_LIMITS,
        max_mounts: int = 32,
    ) -> None:
        if not 1 <= max_mounts <= 128:
            raise ValueError("Relay mount binding capacity must be bounded")
        self._session = session
        self._environment_id = environment_id
        self._authorize_mount = authorize_mount
        self._permissions: dict[str, frozenset[EnvironmentAction]] = {}
        self._admission = asyncio.Lock()
        self._limits = limits
        self._max_mounts = max_mounts
        self._mounts: dict[str, _MountBinding] = {}

    def prepare(self, request: RelayRequest) -> Callable[[], Awaitable[JsonValue]] | FileTransferPlan:
        if request.operation == "scope.describe":
            DomainModel.model_validate(request.payload)

            async def describe() -> JsonValue:
                await self._admit_mount(request.mount_name)
                binding, permissions = self._binding(request)
                return self._snapshot(binding, permissions).model_dump(mode="json")

            return describe
        binding, permissions = self._binding(request)
        if request.operation == "scope.ready":
            readiness = ReadinessRequest.model_validate(request.payload)

            async def ready() -> JsonValue:
                await binding.ensure_ready(readiness.operations)
                return self._snapshot(binding, permissions).model_dump(mode="json")

            return ready
        if request.operation.startswith("file."):
            if binding.operations.files is None:
                raise EnvironmentError("File operations are unavailable", code="environment_unsupported")
            return FileRelayDispatch(binding.operations.files, permissions).prepare(request)
        return CommandRelayDispatch(binding.operations, permissions).prepare(request)

    async def _admit_mount(self, name: str) -> None:
        # Admission may read PG; operation preparation and raw EIP dispatch never do.
        # Serialize first admission so concurrent descriptions cannot exceed capacity
        # or publish policies from differently timed authorization observations.
        async with self._admission:
            if name in self._permissions:
                return
            if len(self._permissions) >= self._max_mounts:
                raise EnvironmentError("Relay mount capacity is exhausted", code="environment_overloaded")
            self._permissions[name] = await self._authorize_mount(name)

    def _binding(self, request: RelayRequest) -> tuple[EIPEnvironmentSession, frozenset[EnvironmentAction]]:
        permissions = self._permissions.get(request.mount_name)
        if permissions is None:
            raise EnvironmentError("The mount has not been admitted", code="environment_forbidden")
        owned = self._mounts.get(request.mount_id)
        if owned is None:
            if len(self._mounts) >= self._max_mounts:
                raise EnvironmentError("Relay mount binding capacity is exhausted", code="environment_overloaded")
            owned = _MountBinding(
                request.mount_name,
                EIPEnvironmentSession(
                    session=self._session,
                    provider_key=WEBSOCKET_PROVIDER_KEY,
                    environment_id=self._environment_id,
                    mount_id=request.mount_id,
                ),
            )
            self._mounts[request.mount_id] = owned
        if owned.name != request.mount_name:
            raise EnvironmentError("A mount binding cannot change its association", code="environment_forbidden")
        return owned.session, owned.session.descriptor.permissions.operations & permissions

    def _snapshot(
        self, binding: EIPEnvironmentSession, permissions: frozenset[EnvironmentAction]
    ) -> RelayEnvironmentSnapshot:
        descriptor = binding.descriptor
        bounds = {
            "max_request_bytes": self._limits.request_bytes,
            "max_response_bytes": self._limits.response_bytes,
            "max_operation_duration_ms": self._limits.delivery_ms,
            "max_transfer_frame_bytes": self._limits.chunk_bytes,
        }
        limits = dict(descriptor.limits)
        for key, bound in bounds.items():
            value = limits[key]
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError("Shared EIP descriptor returned a non-integral byte or duration limit")
            limits[key] = min(value, bound)
        families = frozenset(ENVIRONMENT_ACTION_DISPATCH[action].family for action in permissions)
        narrowed = descriptor.model_copy(
            update={
                "permissions": EnvironmentPermissionSet(operations=permissions),
                "operation_families": descriptor.operation_families & families,
                "limits": limits,
            }
        )
        availability = binding.availability.model_copy(
            update={
                "ready_families": binding.availability.ready_families & narrowed.operation_families,
            }
        )
        return RelayEnvironmentSnapshot(descriptor=narrowed, availability=availability)
