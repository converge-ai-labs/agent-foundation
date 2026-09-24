from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from a13n_envd_client import (
    EIPDeviceConnection,
    EIPSession,
    EIPSessionStateError,
)
from a13n_envd_client.eip.v1 import EgressPolicy

from .envd_policy import EnvdBoundaryRequirement


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
    """Borrow a Host-owned Device connection; own only the newly opened Session."""

    def __init__(self, device: EIPDeviceConnection) -> None:
        super().__init__()
        self._device = device

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
        session = await self._device.open_session(
            working_directory=working_directory,
            required_methods=tuple(sorted(required_methods)),
            egress=egress,
        )
        async with session:
            yield session

    async def discard(self) -> None:
        self._claimed = True
