"""Device reads and independent binding Sessions on one Control-owned carrier."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from a13n_envd_client import EIPClientError, EIPDeviceConnection, EIPSession
from a13n_envd_client.eip.v1 import DirectoryListParams
from a13n_harness.providers.environment.models import EnvironmentError
from pydantic import JsonValue

from ..domain import DomainModel
from .authority import DeviceReadIdentity, DispatchAuthority, DispatchDenied, LeaseDeadline, UseIdentity
from .coordination import ConfirmedObservation, ConnectionCoordination, CoordinationError
from .relay_consumer import RelayExecution
from .relay_dispatch import EnvironmentRelayDispatch
from .relay_protocol import RelayLimits, RelayRequest
from .transport import ClientWebSocket

type UseAuthorizer = Callable[[UseIdentity], Awaitable[str]]


@dataclass(slots=True)
class _BindingSession:
    identity: UseIdentity
    authority: DispatchAuthority
    confirmed_at: float
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    session: EIPSession | None = None
    dispatch: EnvironmentRelayDispatch | None = None
    closing: asyncio.Task[None] | None = None


class ConnectionDispatch:
    def __init__(
        self,
        device: EIPDeviceConnection,
        carrier: ClientWebSocket,
        coordination: ConnectionCoordination,
        authorize_use: UseAuthorizer,
        observation: ConfirmedObservation,
        *,
        required_methods: frozenset[str],
        limits: RelayLimits,
        cancel_scope: Callable[[UseIdentity], None],
    ) -> None:
        self._device, self._carrier = device, carrier
        self._coordination, self._authorize_use = coordination, authorize_use
        self._observation = observation
        self._required_methods, self._limits = required_methods, limits
        self._cancel_scope = cancel_scope
        self._bindings: dict[str, _BindingSession] = {}
        self._closed = False

    def refresh(self, observation: ConfirmedObservation) -> None:
        self._observation = observation
        for binding in tuple(self._bindings.values()):
            if binding.closing is not None or observation.request_started_at < binding.confirmed_at:
                continue
            try:
                binding.authority.renew(binding.identity, observation.deadline(use=binding.identity))
            except (CoordinationError, DispatchDenied):
                self._retire(binding)

    def _retire(self, binding: _BindingSession) -> None:
        binding.authority.invalidate()
        self._cancel_scope(binding.identity)
        if binding.closing is None:
            # One cleanup task per retained binding, within the same capacity bound.
            # A slow Session close never blocks Device renewal or sibling dispatch.
            binding.closing = asyncio.create_task(self._dispose(binding), name="environment-binding-close")

    async def _dispose(self, binding: _BindingSession) -> None:
        try:
            await binding.authority.fence()
            async with binding.lock:
                if binding.session is not None:
                    await binding.session.abort()
        finally:
            if self._bindings.get(binding.identity.use_id) is binding:
                del self._bindings[binding.identity.use_id]

    async def close(self) -> None:
        self._closed = True
        bindings = tuple(self._bindings.values())
        for binding in bindings:
            self._retire(binding)
        await asyncio.gather(*(binding.closing for binding in bindings if binding.closing is not None))

    async def _binding(self, identity: UseIdentity) -> _BindingSession:
        if self._closed:
            raise DispatchDenied("Device dispatch is closed")
        binding = self._bindings.get(identity.use_id)
        if binding is None:
            observed = await self._coordination.observe(
                identity.connection.organization_id, identity.connection.environment_id
            )
            if observed.value.connection != identity.connection or observed.value.status != "online":
                raise DispatchDenied("The binding connection is unavailable")
            authority = DispatchAuthority(identity, observed.deadline(use=identity))
            authority.check(identity)
            # Concurrent requests may have admitted the same use during Redis I/O.
            binding = self._bindings.get(identity.use_id)
            if binding is None:
                if self._closed:
                    raise DispatchDenied("Device dispatch is closed")
                if len(self._bindings) >= self._coordination.limits.max_uses:
                    raise EnvironmentError("Device Session capacity is exhausted", code="environment_overloaded")
                binding = _BindingSession(identity, authority, observed.request_started_at)
                self._bindings[identity.use_id] = binding
        if binding.identity != identity:
            raise DispatchDenied("Use identity changed")
        binding.authority.check(identity)
        return binding

    async def _open(self, binding: _BindingSession) -> EnvironmentRelayDispatch:
        async with binding.lock:
            binding.authority.check(binding.identity)
            if binding.dispatch is not None:
                return binding.dispatch
            working_directory = await self._authorize_use(binding.identity)
            # SQL has closed before refreshing shared authority or opening a Session.
            observed = await self._coordination.observe(
                binding.identity.connection.organization_id, binding.identity.connection.environment_id
            )
            if observed.value.connection != binding.identity.connection or observed.value.status != "online":
                raise DispatchDenied("Use changed during authorization")
            binding.authority.renew(binding.identity, observed.deadline(use=binding.identity))
            binding.confirmed_at = observed.request_started_at
            try:
                with self._carrier.dispatch_scope(binding.authority):
                    session = await self._device.open_session(
                        working_directory=working_directory,
                        required_methods=tuple(sorted(self._required_methods)),
                    )
                    binding.session = session
                    binding.authority.check(binding.identity)
                    binding.dispatch = EnvironmentRelayDispatch(
                        session,
                        binding.identity.connection.environment_id,
                        binding.identity.mount_name,
                        limits=self._limits,
                    )
            except BaseException:
                self._retire(binding)
                raise
            return binding.dispatch

    @asynccontextmanager
    async def __call__(self, request: RelayRequest) -> AsyncIterator[RelayExecution]:
        try:
            if isinstance(request.scope, DeviceReadIdentity):
                observed = self._observation
                deadline = LeaseDeadline.confirmed(
                    request_started_at=observed.request_started_at,
                    server_now_ms=observed.value.now_ms,
                    expires_at_ms=request.scope.authorization_deadline_ms,
                    safety_margin_seconds=observed.safety_margin_seconds,
                )
                authority = DispatchAuthority(request.scope, deadline)
                with self._carrier.dispatch_scope(authority):
                    if request.operation == "device.describe":
                        DomainModel.model_validate(request.payload)

                        async def describe() -> JsonValue:
                            return (await self._device.describe()).model_dump(mode="json")

                        yield describe
                    else:
                        params = DirectoryListParams.model_validate(request.payload)

                        async def directories() -> JsonValue:
                            return (await self._device.list_directories(params)).model_dump(mode="json")

                        yield directories
                return
            binding = await self._binding(request.scope)
            with self._carrier.dispatch_scope(binding.authority):
                if request.operation == "scope.close":
                    DomainModel.model_validate(request.payload)

                    async def close_scope() -> JsonValue:
                        try:
                            async with binding.lock:
                                if binding.session is not None:
                                    await binding.session.close()
                            await self._coordination.release_use(binding.identity)
                        finally:
                            self._retire(binding)
                        return None

                    yield close_scope
                elif request.operation == "operation.cancel":

                    async def cancel() -> JsonValue:
                        return None

                    yield cancel
                else:
                    if request.operation == "scope.describe":
                        dispatch = await self._open(binding)
                    elif binding.dispatch is not None:
                        dispatch = binding.dispatch
                    else:
                        raise EnvironmentError("The binding Session is not open", code="environment_unavailable")
                    yield dispatch.prepare(request)
        except CoordinationError as error:
            raise EnvironmentError("Binding authority is unavailable", code="environment_unavailable") from error
        except EIPClientError as error:
            raise EnvironmentError("Device operation failed", code="environment_unavailable") from error
