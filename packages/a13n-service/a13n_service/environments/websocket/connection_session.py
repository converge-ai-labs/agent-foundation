"""One admitted Device carrier, from fenced handover through independent uses."""

from __future__ import annotations

import asyncio
from time import monotonic

from a13n_envd_client import EIPDeviceConnection
from a13n_envd_client.websocket import AcceptedWebSocketTransport
from a13n_logging import get_logger
from anyio import move_on_after

from a13n_service.ids import new_object_id
from a13n_service.storage import is_database_unavailable

from ..errors import EnvironmentManagementError
from .authority import DispatchAuthority, DispatchDenied, LeaseDeadline
from .connection_dispatch import ConnectionDispatch, UseAuthorizer
from .coordination import ConfirmedObservation, ConnectionFailure, CoordinationError
from .relay_consumer import RelayControlConsumer
from .relay_storage import ConnectionRelayStore
from .resources import ConnectionTarget
from .service import ClientConnectionService
from .transport import ClientWebSocket

logger = get_logger(__name__)


class ClientConnectionSession:
    def __init__(
        self,
        service: ClientConnectionService,
        carrier: ClientWebSocket,
        admission: ConfirmedObservation,
        store: ConnectionRelayStore,
        authorize_use: UseAuthorizer,
    ) -> None:
        if admission.value.connection != store.connection or admission.value.status != "connecting":
            raise ValueError("Connection Session requires its exact admitted candidate")
        self._service, self._carrier, self._store = service, carrier, store
        self._connection = store.connection
        self._admission = admission
        self._initialize_by = admission.deadline().monotonic_at
        self._authorize_use = authorize_use
        self._current = admission
        self._dispatch: ConnectionDispatch | None = None
        self._detached = False
        self._failure: ConnectionFailure = "environment_initialization_failed"

    def begin_drain(self) -> None:
        self._failure = "control_draining"
        self._carrier.invalidate()

    async def run(self) -> None:
        promoted = False
        try:
            async with asyncio.timeout_at(self._initialize_by):
                target, granted = await self._promote()
            promoted = True
            await self._own(target, granted)
        finally:
            self._carrier.invalidate()
            with move_on_after(3, shield=True):
                await self._carrier.close()
                coordination = self._service.coordination
                if promoted:
                    try:
                        await coordination.retire(self._connection, error=self._failure)
                        if self._detached:
                            await coordination.acknowledge(self._connection)
                    except CoordinationError:
                        # Lost coordination cannot authorize a replacement early;
                        # retained grant horizons remain the handover barrier.
                        pass
                    await self._publish_disconnected()
                else:
                    try:
                        await coordination.abandon(self._connection, error=self._failure)
                    except CoordinationError:
                        pass

    async def _promote(self) -> tuple[ConnectionTarget, ConfirmedObservation]:
        coordination = self._service.coordination
        observed = self._admission
        while True:
            value = observed.value
            if value.connection != self._connection or value.status != "connecting":
                raise CoordinationError("candidate_expired")
            if value.now_ms >= value.barrier_ms or (value.retiring is not None and value.retiring.acknowledged):
                target = await self._service.resources.capture(
                    self._connection.organization_id, self._connection.environment_id
                )
                try:
                    granted = await coordination.promote(self._connection)
                    return target, granted
                except CoordinationError as error:
                    if error.code != "handover_pending":
                        raise
            await asyncio.sleep(0.05)
            observed = await coordination.observe(self._connection.organization_id, self._connection.environment_id)

    async def _own(self, target: ConnectionTarget, grant: ConfirmedObservation) -> None:
        authority = DispatchAuthority(self._connection, grant.deadline())
        self._carrier.bind_connection(authority)
        self._current = grant
        try:
            async with asyncio.TaskGroup() as tasks:
                renewer = tasks.create_task(self._renew(authority), name="client-environment-connection-renew")
                try:
                    await self._ready_device(target, authority)
                finally:
                    renewer.cancel()
        finally:
            self._carrier.invalidate()
            with move_on_after(3, shield=True):
                await authority.fence()
                await self._carrier.close()
                self._detached = True

    async def _ready_device(self, target: ConnectionTarget, authority: DispatchAuthority) -> None:
        async with asyncio.timeout_at(self._initialize_by):
            device = await EIPDeviceConnection.initialize(
                AcceptedWebSocketTransport(self._carrier),
                expected_device_id=target.device_id,
                initialization_timeout=self._initialize_by - monotonic(),
                request_timeout=60,
                max_in_flight=32,
            )
        try:
            self._carrier.require_scope()
            async with asyncio.timeout_at(self._initialize_by):
                await self._publish_running(target, authority)
                await self._store.prepare()
                online = await self._service.coordination.online(self._connection)
                self._record(online, authority)
            self._failure = "environment_unavailable"
            logger.info(
                "client_environment_online",
                extra={
                    "environment_id": self._connection.environment_id,
                    "connection_id": self._connection.connection_id,
                },
            )
            dispatch = ConnectionDispatch(
                device,
                self._carrier,
                self._service.coordination,
                self._authorize_use,
                online,
                required_methods=target.required_methods,
                limits=self._store.limits,
                cancel_scope=lambda identity: consumer.cancel_scope(identity),
            )
            consumer = RelayControlConsumer(self._store, authority, online, dispatch)
            self._dispatch = dispatch
            await consumer.run()
        finally:
            self._carrier.invalidate()
            with move_on_after(2, shield=True):
                await authority.fence()
                if self._dispatch is not None:
                    await self._dispatch.close()
                await device.close()

    def _record(self, observation: ConfirmedObservation, authority: DispatchAuthority) -> None:
        value = observation.value
        if value.connection != self._connection or value.status == "offline":
            raise DispatchDenied("Connection authority changed")
        authority.renew(self._connection, observation.deadline())
        if self._dispatch is not None:
            self._dispatch.refresh(observation)
        self._current = observation

    async def _renew(self, authority: DispatchAuthority) -> None:
        try:
            while True:
                await asyncio.sleep(self._service.coordination.limits.lease_ms / 3000)
                observation = await self._service.coordination.renew(self._connection)
                self._record(observation, authority)
                if observation.value.status == "online":
                    await self._store.touch()
        finally:
            self._carrier.invalidate()

    async def _publish_running(self, initial: ConnectionTarget, authority: DispatchAuthority) -> None:
        target = initial
        for capture in range(2):
            publication = new_object_id("aud")
            published = False
            for attempt in range(2):
                authority.check(self._connection)
                try:
                    published = await self._service.resources.publish(
                        target,
                        "running",
                        publication_id=publication,
                        evidence_deadline=LeaseDeadline(min(self._initialize_by, authority.deadline)),
                    )
                    break
                except Exception as error:
                    if not is_database_unavailable(error) or attempt:
                        raise
            if published:
                return
            if capture == 0:
                target = await self._service.resources.capture(
                    self._connection.organization_id, self._connection.environment_id
                )
        raise DispatchDenied("Connection target publication was superseded")

    async def _publish_disconnected(self) -> None:
        try:
            target = await self._service.resources.capture(
                self._connection.organization_id, self._connection.environment_id
            )
            observed = await self._service.observe(self._connection.organization_id, self._connection.environment_id)
            if observed.value.status != "offline":
                return
            await self._service.resources.publish(
                target,
                "unavailable",
                publication_id=new_object_id("aud"),
                evidence_deadline=LeaseDeadline(observed.request_started_at + 1),
            )
        except Exception as error:
            if not isinstance(error, EnvironmentManagementError) and not is_database_unavailable(error):
                raise
            logger.warning(
                "client_environment_disconnect_observation_deferred",
                extra={
                    "environment_id": self._connection.environment_id,
                },
            )
