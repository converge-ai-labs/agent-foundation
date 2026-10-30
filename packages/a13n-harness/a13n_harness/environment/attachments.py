from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass

from a13n_envd_client import (
    EIPDeviceConnection,
    EIPSession,
    EIPSessionStateError,
)
from a13n_envd_client.eip.v1 import DeviceDescriptor, EgressPolicy

from a13n_harness.environment.envd_policy import EnvdBoundaryRequirement
from a13n_harness.environment.session_recovery import EnvdSessionRecovery


@dataclass(frozen=True)
class EIPSessionBinding:
    """A Session and the metadata of the Device connection that opened it."""

    session: EIPSession
    device_descriptor: DeviceDescriptor


class EIPSessionSource(ABC):
    """Single-use source for one independent, readiness-confirmed Session."""

    def __init__(self) -> None:
        self._claimed = False

    @abstractmethod
    def open_session(
        self,
        *,
        expected_device_id: str,
        required_methods: frozenset[str],
        working_directory: str | None = None,
        egress: EgressPolicy | None = None,
        expected_boundary: EnvdBoundaryRequirement | None = None,
    ) -> AbstractAsyncContextManager[EIPSession]: ...

    @abstractmethod
    async def discard(self) -> None:
        """Release an unentered source, not a borrowed Device connection."""

    def _claim(self) -> None:
        if self._claimed:
            raise RuntimeError("EIP Session source is single-use")
        self._claimed = True


class DeviceEIPSessionSource(EIPSessionSource):
    """Borrow a Device connection; own one attached or newly opened Session."""

    def __init__(
        self,
        device: EIPDeviceConnection,
        *,
        target: str | None = None,
        session_recovery: EnvdSessionRecovery | None = None,
    ) -> None:
        super().__init__()
        self._device = device
        self._target = target if target is not None else device.descriptor.device_id
        self._recovery = session_recovery

    @asynccontextmanager
    async def open_session(
        self,
        *,
        expected_device_id: str,
        required_methods: frozenset[str],
        working_directory: str | None = None,
        egress: EgressPolicy | None = None,
        expected_boundary: EnvdBoundaryRequirement | None = None,
    ) -> AsyncGenerator[EIPSession]:
        self._claim()
        if self._device.descriptor.device_id != expected_device_id:
            raise EIPSessionStateError("Session source belongs to another Device")
        if expected_boundary is not None:
            expected_boundary.check(self._device.descriptor.boundary)
        recovery = self._recovery
        if recovery is not None and egress is not None:
            recovery.state = None
            recovery = None
        session = (
            await recovery.attach(
                self._device,
                target=self._target,
                expected_device_id=expected_device_id,
                working_directory=working_directory,
                required_methods=required_methods,
                expected_boundary=expected_boundary,
            )
            if recovery is not None
            else None
        )
        if session is None:
            session = await self._device.open_session(
                working_directory=working_directory,
                required_methods=tuple(sorted(required_methods)),
                egress=egress,
            )
        async with session:
            if recovery is not None:
                recovery.observe(self._target, working_directory, session)
            try:
                yield session
            finally:
                if recovery is not None:
                    recovery.closed(session)

    async def discard(self) -> None:
        self._claimed = True
