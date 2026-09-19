"""One external EIP operation scope, independent of the daemon's lifecycle."""

from contextlib import AbstractAsyncContextManager

from a13n_envd_client import EIPSession
from pydantic import BaseModel

from ..eip.binding import EIPEnvironmentSession, configured_descriptor
from ..errors import EnvironmentProviderErrorCategory as Category
from ..errors import provider_error
from ..management import Environment
from ..models import (
    EnvironmentAvailability,
    EnvironmentDescriptor,
    EnvironmentError,
    EnvironmentOperationFamily,
    EnvironmentState,
    decode_state_envelope,
)
from ..operations import EnvironmentOperations
from .configuration import RemoteEnvdEnvironmentConfiguration, RemoteEnvdStateData

REQUIRED_METHODS = frozenset({"environment.describe", "environment.readiness", "session.close"})


def decode_state(key: str, state: EnvironmentState | None) -> RemoteEnvdStateData:
    """An external Envd target is only addressable through accepted daemon state."""
    return decode_state_envelope(key, state, RemoteEnvdStateData)


def describe_environment(configuration: BaseModel) -> EnvironmentDescriptor:
    if not isinstance(configuration, RemoteEnvdEnvironmentConfiguration):
        raise TypeError("Remote Envd requires its target configuration")
    return configured_descriptor()


def target_identity(provider_type: str, *, configuration: BaseModel, state: EnvironmentState | None) -> str:
    describe_environment(configuration)
    return decode_state(provider_type, state).daemon_environment_id


class RemoteEnvdEnvironment(Environment):
    def __init__(
        self,
        *,
        provider_key: str,
        environment_id: str,
        state: EnvironmentState,
        session_context: AbstractAsyncContextManager[EIPSession],
    ) -> None:
        super().__init__(state)
        self._provider_key = provider_key
        self._environment_id = environment_id
        self._session_context = session_context
        self._entered_session = False
        self._bound: EIPEnvironmentSession | None = None
        self._descriptor = configured_descriptor()
        self._availability = EnvironmentAvailability(status="preparing")
        self._operations = EnvironmentOperations()
        self._attempted = False

    @property
    def provider_key(self) -> str:
        return self._provider_key

    @property
    def environment_id(self) -> str:
        return self._environment_id

    @property
    def descriptor(self) -> EnvironmentDescriptor:
        return self._descriptor

    @property
    def availability(self) -> EnvironmentAvailability:
        return self._availability

    @property
    def operations(self) -> EnvironmentOperations:
        return self._operations

    async def _prepare(self, *, mount_id: str) -> None:
        if self._attempted:
            raise provider_error(self.provider_key, "provider_runtime_consumed", Category.UNAVAILABLE)
        self._attempted = True
        try:
            session = await self._session_context.__aenter__()
            self._entered_session = True
            self._bound = EIPEnvironmentSession(
                session=session, provider_key=self.provider_key, environment_id=self.environment_id, mount_id=mount_id
            )
            self._descriptor = self._bound.descriptor
            self._operations = self._bound.operations
            self._availability = self._bound.availability
        except BaseException:
            self._availability = EnvironmentAvailability(status="unavailable")
            if self._entered_session:
                await self._close()
            raise

    def _bind_mount(self, mount_id: str) -> None:
        if self._bound is not None:
            self._bound.bind_mount(mount_id)
            self._operations = self._bound.operations

    async def _ensure_ready(self, operations: frozenset[EnvironmentOperationFamily]) -> None:
        if self._bound is None:
            raise EnvironmentError("Remote Envd connection is unavailable", code="environment_unavailable")
        try:
            await self._bound.ensure_ready(operations)
        finally:
            self._availability = self._bound.availability

    async def _close(self) -> None:
        self._availability = EnvironmentAvailability(status="unavailable")
        self._operations = EnvironmentOperations()
        errors: list[BaseException] = []
        if self._bound is not None:
            try:
                await self._bound.close()
            except BaseException as error:
                errors.append(error)
            self._bound = None
        if self._entered_session:
            self._entered_session = False
            try:
                await self._session_context.__aexit__(None, None, None)
            except BaseException as error:
                errors.append(error)
        if len(errors) == 1:
            raise errors[0]
        if errors:
            raise BaseExceptionGroup("Remote Envd local cleanup failed", errors)
