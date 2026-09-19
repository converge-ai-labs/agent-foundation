"""Operation adapter for one admitted binding's independent EIP Session."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from a13n_envd_client import EIPSession
from a13n_environment import (
    EnvironmentAction,
    EnvironmentError,
)
from a13n_environment.eip.binding import EIPEnvironmentSession
from a13n_environment.remote_envd.connections import WEBSOCKET_PROVIDER_KEY
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

    Preparation can precede Harness binding. Local mount IDs may change as the
    Harness installs its facade, but they never change the accepted association,
    directory or native Session.
    """

    def __init__(
        self,
        session: EIPSession,
        environment_id: str,
        mount_name: str,
        *,
        limits: RelayLimits = DEFAULT_RELAY_LIMITS,
        max_mounts: int = 32,
    ) -> None:
        if not 1 <= max_mounts <= 128:
            raise ValueError("Relay mount binding capacity must be bounded")
        self._session = session
        self._environment_id = environment_id
        self._mount_name = mount_name
        self._limits = limits
        self._max_mounts = max_mounts
        self._mounts: dict[str, _MountBinding] = {}

    def prepare(self, request: RelayRequest) -> Callable[[], Awaitable[JsonValue]] | FileTransferPlan:
        if request.operation == "scope.describe":
            DomainModel.model_validate(request.payload)

            async def describe() -> JsonValue:
                binding, _ = self._binding(request)
                return self._snapshot(binding).model_dump(mode="json")

            return describe
        binding, permissions = self._binding(request)
        if request.operation == "scope.ready":
            readiness = ReadinessRequest.model_validate(request.payload)

            async def ready() -> JsonValue:
                await binding.ensure_ready(readiness.operations)
                return self._snapshot(binding).model_dump(mode="json")

            return ready
        if request.operation.startswith("file."):
            if binding.operations.files is None:
                raise EnvironmentError("File operations are unavailable", code="environment_unsupported")
            return FileRelayDispatch(binding.operations.files, permissions).prepare(request)
        return CommandRelayDispatch(binding.operations, permissions).prepare(request)

    def _binding(self, request: RelayRequest) -> tuple[EIPEnvironmentSession, frozenset[EnvironmentAction]]:
        if request.mount_name != self._mount_name:
            raise EnvironmentError("The use cannot select a different binding", code="environment_forbidden")
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
        return owned.session, owned.session.descriptor.permissions.operations

    def _snapshot(self, binding: EIPEnvironmentSession) -> RelayEnvironmentSnapshot:
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
        return RelayEnvironmentSnapshot(
            descriptor=descriptor.model_copy(update={"limits": limits}),
            availability=binding.availability,
        )
