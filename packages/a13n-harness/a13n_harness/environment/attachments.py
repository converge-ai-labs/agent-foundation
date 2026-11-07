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
from a13n_envd_client.eip.v1 import DeviceDescriptor, SessionConfiguration

from a13n_harness.environment.envd_policy import EnvdBoundaryRequirement
from a13n_harness.environment.session_recovery import (
    EnvdSessionBorrower,
    SessionConfigurationSource,
    resolve_configuration,
)


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
        configuration: SessionConfigurationSource | None = None,
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
        session_recovery: EnvdSessionBorrower | None = None,
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
        configuration: SessionConfigurationSource | None = None,
        expected_boundary: EnvdBoundaryRequirement | None = None,
    ) -> AsyncGenerator[EIPSession]:
        self._claim()
        if self._device.descriptor.device_id != expected_device_id:
            raise EIPSessionStateError("Session source belongs to another Device")
        configuration = configuration or SessionConfiguration()
        if self._recovery is not None:
            async with self._recovery.borrow(
                self._device,
                target=self._target,
                expected_device_id=expected_device_id,
                configuration=configuration,
                required_methods=required_methods,
                expected_boundary=expected_boundary,
            ) as session:
                yield session
        else:
            session = await self._device.open_session(
                configuration=await resolve_configuration(configuration),
                required_methods=tuple(sorted(required_methods)),
            )
            async with session:
                if expected_boundary is not None:
                    expected_boundary.check(session.descriptor.boundary)
                yield session

    async def discard(self) -> None:
        self._claimed = True
