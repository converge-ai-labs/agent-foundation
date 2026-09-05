"""One external EIP operation scope, independent of the daemon's lifecycle."""

from collections.abc import Mapping
from contextlib import AbstractAsyncContextManager
from typing import Literal

from a13n_envd_client import EIPSession

from ..eip.binding import EIPEnvironmentSession, configured_descriptor
from ..errors import (
    EnvironmentProviderError,
    EnvironmentProviderErrorContext,
)
from ..errors import (
    EnvironmentProviderErrorCategory as Category,
)
from ..errors import (
    EnvironmentProviderOutcomeCertainty as Certainty,
)
from ..errors import (
    EnvironmentProviderRecoveryHint as Recovery,
)
from ..management import Environment
from ..models import (
    EnvironmentAvailability,
    EnvironmentDescriptor,
    EnvironmentError,
    EnvironmentOperationFamily,
    EnvironmentState,
)
from ..operations import EnvironmentOperations
from .configuration import RemoteEnvdStateData

REQUIRED_METHODS = frozenset({"environment.describe", "environment.readiness", "session.close"})


def provider_error(key: str, code: str, category: Category) -> EnvironmentProviderError:
    return EnvironmentProviderError(
        "Remote Envd provider could not complete the requested action.",
        code=code,
        category=category,
        certainty=Certainty.NOT_DISPATCHED,
        recovery_hint=Recovery.REFRESH_RUNTIME
        if category in {Category.UNAVAILABLE, Category.TIMEOUT}
        else Recovery.FIX_INPUT,
        context=EnvironmentProviderErrorContext(provider_key=key),
    )


def decode_state(key: str, state: EnvironmentState | None) -> RemoteEnvdStateData:
    if state is None or state.provider_key != key or state.state_version != "1":
        raise provider_error(key, "provider_state_invalid", Category.INVALID)
    try:
        return RemoteEnvdStateData.model_validate(state.state)
    except ValueError:
        raise provider_error(key, "provider_state_invalid", Category.INVALID) from None


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

    async def _prepare(
        self, *, thread_id: str, run_id: str, agent_instance_id: str, mount_id: str, host_refs: Mapping[str, str]
    ) -> None:
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

    async def reconcile(self) -> Literal["running", "stopped", "absent"]:
        # A connection probe cannot prove external target absence or reconcile provisioning.
        raise provider_error(self.provider_key, "provider_operation_unsupported", Category.UNSUPPORTED)

    async def _stop(self) -> None:
        raise provider_error(self.provider_key, "provider_operation_unsupported", Category.UNSUPPORTED)

    async def _destroy(self) -> None:
        raise provider_error(self.provider_key, "provider_operation_unsupported", Category.UNSUPPORTED)
