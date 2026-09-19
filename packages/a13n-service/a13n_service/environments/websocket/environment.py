"""Fresh Worker Environment facets over a Control-owned EIP Session."""

from __future__ import annotations

from typing import TYPE_CHECKING

from a13n_harness.providers.environment.management import Environment
from a13n_harness.providers.environment.models import (
    EnvironmentAction,
    EnvironmentAvailability,
    EnvironmentDescriptor,
    EnvironmentError,
    EnvironmentOperationFamily,
)
from a13n_harness.providers.environment.operations import EnvironmentOperations
from a13n_harness.providers.environment.remote_envd.connections import WEBSOCKET_PROVIDER_KEY
from a13n_logging import get_logger

from a13n_service.observability import observe_phase

from .relay_client import RelayUseClient
from .relay_file_operations import RelayFileOperations
from .relay_processes import RelayOutputOperations, RelayPortOperations, RelayProcessOperations, RelayShellOperations
from .relay_protocol import ReadinessRequest, RelayEnvironmentSnapshot
from .worker_connections import WorkerClientConnections
from .worker_resources import ClientUseResources

if TYPE_CHECKING:
    from a13n_service.interactions.attempts import AttemptContext

logger = get_logger(__name__)


class ClientRunEnvironment(Environment):
    recover_on_unavailable = True

    def __init__(
        self,
        resources: ClientUseResources,
        connections: WorkerClientConnections,
        attempt: AttemptContext,
        environment_id: str,
        descriptor: EnvironmentDescriptor,
        *,
        mount_name: str = "workspace",
    ) -> None:
        super().__init__(None)
        self._resources, self._connections, self._attempt = resources, connections, attempt
        self._environment_id = environment_id
        self._mount_name = mount_name
        self._descriptor = descriptor
        self._availability = EnvironmentAvailability(status="preparing")
        self._operations = EnvironmentOperations()
        self._client: RelayUseClient | None = None
        self._generation = 0

    @property
    def provider_key(self) -> str:
        return WEBSOCKET_PROVIDER_KEY

    @property
    def environment_id(self) -> str:
        return self._environment_id

    @property
    def backing_generation(self) -> int:
        return self._generation

    @property
    def descriptor(self) -> EnvironmentDescriptor:
        return self._descriptor

    @property
    def availability(self) -> EnvironmentAvailability:
        if self._client is not None and not self._client.available:
            return EnvironmentAvailability(status="unavailable", reason_code="environment_unavailable")
        return self._availability

    @property
    def operations(self) -> EnvironmentOperations:
        return self._operations

    async def _prepare(self, *, mount_id: str) -> None:
        with observe_phase("a13n.service.environment.prepare"):
            await self._close()
            target = await self._resources.admit(self._attempt, self.environment_id, mount_name=self._mount_name)
            client = await self._connections.acquire(
                self._attempt, self.environment_id, frozenset(EnvironmentAction), mount_name=self._mount_name
            )
            self._client = client
            if self.is_entered:
                client.bind_mount(mount_id)
            try:
                snapshot = RelayEnvironmentSnapshot.model_validate(await client.call("scope.describe"))
                self._generation = target.generation
                self._cache_state(target.state)
                self._apply_snapshot(snapshot)
                families = self._descriptor.operation_families
                self._operations = EnvironmentOperations(
                    files=RelayFileOperations(client) if "files" in families else None,
                    shell=RelayShellOperations(client) if "shell" in families else None,
                    processes=RelayProcessOperations(client) if "processes" in families else None,
                    ports=RelayPortOperations(client) if "ports" in families else None,
                    outputs=RelayOutputOperations(client) if "outputs" in families else None,
                )
            except BaseException:
                await self._close()
                raise
            logger.info(
                "client_environment_use_ready",
                extra={
                    "environment_id": self.environment_id,
                    "run_attempt_id": self._attempt.run_attempt_id,
                    "connection_id": client.identity.connection.connection_id,
                },
            )

    def _apply_snapshot(self, snapshot: RelayEnvironmentSnapshot) -> None:
        self._descriptor = snapshot.descriptor.model_copy(
            update={"backing_identity": f"{self.environment_id}:{self._generation}"}
        )
        self._availability = snapshot.availability

    def _bind_mount(self, mount_id: str) -> None:
        if self._client is not None:
            self._client.bind_mount(mount_id)

    async def _ensure_ready(self, operations: frozenset[EnvironmentOperationFamily]) -> None:
        if self._client is None:
            raise EnvironmentError("Client Environment has no use scope", code="environment_unavailable")
        snapshot = RelayEnvironmentSnapshot.model_validate(
            await self._client.call("scope.ready", ReadinessRequest(operations=operations).model_dump(mode="json"))
        )
        self._apply_snapshot(snapshot)

    async def _close(self) -> None:
        self._availability = EnvironmentAvailability(status="unavailable")
        self._operations = EnvironmentOperations()
        client, self._client = self._client, None
        if client is not None:
            await self._connections.release(client)

    async def _destroy(self) -> None:
        raise RuntimeError("Run objects do not own target deletion authority")
