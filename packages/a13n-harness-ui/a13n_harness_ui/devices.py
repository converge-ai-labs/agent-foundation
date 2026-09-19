"""App-owned Device connections and Session-free directory discovery."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
from collections.abc import Iterable
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Literal

from a13n_envd_client import EIPClientError
from a13n_envd_client.eip.v1 import DeviceDescriptor, DirectoryListParams, DirectoryListResult
from a13n_envd_client.websocket import WebSocketConnection
from a13n_harness.providers.environment.errors import EnvironmentProviderError
from a13n_harness.providers.environment.management import Environment
from a13n_harness.providers.environment.models import EnvironmentState
from a13n_harness.providers.environment.remote_envd.configuration import (
    HttpEnvdCredential,
    RemoteEnvdEnvironmentConfiguration,
)
from a13n_harness.providers.environment.remote_envd.connections import WebSocketEnvdConnections
from a13n_harness.providers.environment.remote_envd.http import HTTP_ENVD, HttpEnvdProviderRuntime
from a13n_harness.providers.environment.remote_envd.pairing import credential_matches
from a13n_harness.providers.environment.remote_envd.websocket import WEBSOCKET_ENVD, WebSocketEnvdProviderRuntime
from pydantic import SecretStr, ValidationError

from a13n_harness_ui.composition.models import ResolvedEnvironmentBinding
from a13n_harness_ui.configuration.models import (
    DeviceResource,
    HttpDeviceTransport,
    PairedDeviceAuthentication,
    StrictModel,
    WebSocketDeviceTransport,
    canonical_digest,
)
from a13n_harness_ui.errors import EnvironmentLifecycleError
from a13n_harness_ui.model_accounts.api_keys import ApiKeyStore


class DeviceSummary(StrictModel):
    id: str
    name: str
    transport: Literal["http", "websocket"]
    registration: Literal["configured", "paired", "revoked"] = "configured"

    @classmethod
    def from_resource(cls, resource: DeviceResource) -> DeviceSummary:
        authentication = resource.authentication
        registration = "configured"
        if isinstance(authentication, PairedDeviceAuthentication):
            registration = "revoked" if authentication.revoked else "paired"
        return cls(id=resource.id, name=resource.name, transport=resource.transport.kind, registration=registration)


class DeviceInfo(DeviceSummary):
    available: bool
    path_style: str | None = None
    default_working_directory: str | None = None
    directory_discovery: bool = False
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class DeviceAttachment:
    """Authenticated carrier owner, bound to the exact use-time credential."""

    device_id: str
    connections: WebSocketEnvdConnections

    async def attach(self, connection: WebSocketConnection) -> None:
        try:
            await self.connections.attach(self.device_id, connection)
        except asyncio.CancelledError:
            # Revocation cancels the SDK-owned carrier task, not this HTTP handler.
            # Treat that as normal closure while preserving handler cancellation.
            task = asyncio.current_task()
            if task is None or task.cancelling():
                raise


class DeviceConnections:
    """Share Device carriers, never initialized Sessions or execution-owned handles."""

    def __init__(self, *, api_keys: ApiKeyStore | None = None) -> None:
        self._api_keys = api_keys
        self._http: dict[tuple[str, str], HttpEnvdProviderRuntime] = {}
        self._websocket: dict[tuple[str, str], WebSocketEnvdConnections] = {}
        self._stack = AsyncExitStack()
        self._closed = False
        self._revoked: set[str] = set()
        self._close_task: asyncio.Task[None] | None = None

    async def _credential(self, resource: DeviceResource) -> str:
        authentication = resource.authentication
        if isinstance(authentication, PairedDeviceAuthentication):
            raise TypeError("Paired Device credentials cannot be recovered")
        if authentication.env is not None:
            token = os.environ.get(authentication.env)
        elif self._api_keys is not None and authentication.credential_ref is not None:
            token = await self._api_keys.load(authentication.credential_ref)
        else:
            token = None
        if not token:
            raise EnvironmentLifecycleError("The Device credential is unavailable.", code="device_credential_missing")
        return token

    async def _runtime_key(self, resource: DeviceResource, token: str | None = None) -> tuple[tuple[str, str], str]:
        authentication = resource.authentication
        if isinstance(authentication, PairedDeviceAuthentication):
            fingerprint = authentication.credential_digest
            if authentication.revoked or fingerprint in self._revoked:
                raise EnvironmentLifecycleError("Device registration is revoked.", code="device_revoked")
            token = ""
        else:
            token = await self._credential(resource) if token is None else token
            fingerprint = hashlib.sha256(token.encode()).hexdigest()
        if self._closed:
            raise EnvironmentLifecycleError("Device connections are closed.", code="device_connections_closed")
        recipe = resource.model_dump(mode="json", exclude={"id", "name"})
        # Runtime-only fingerprint allows credential rotation without interrupting
        # execution owners still using the previous connection. Neither is persisted.
        return (canonical_digest(recipe), fingerprint), token

    async def _http_runtime(self, resource: DeviceResource) -> HttpEnvdProviderRuntime:
        if not isinstance(resource.transport, HttpDeviceTransport):
            raise TypeError("HTTP Device resource required")
        key, token = await self._runtime_key(resource)
        if key not in self._http:
            runtime = HttpEnvdProviderRuntime(
                resource.transport.configuration, HttpEnvdCredential(token=SecretStr(token))
            )
            self._http[key] = runtime
            self._stack.push_async_callback(runtime.close)
        return self._http[key]

    async def _websocket_connections(
        self, resource: DeviceResource, token: str | None = None
    ) -> WebSocketEnvdConnections:
        if not isinstance(resource.transport, WebSocketDeviceTransport):
            raise TypeError("WebSocket Device resource required")
        key, _token = await self._runtime_key(resource, token)
        if key not in self._websocket:
            connections = WebSocketEnvdConnections(max_connections=1)
            self._websocket[key] = connections
            self._stack.push_async_callback(connections.close)
        return self._websocket[key]

    async def authenticate_attachment(self, resource: DeviceResource, token: str) -> DeviceAttachment:
        authentication = resource.authentication
        authenticated = (
            not authentication.revoked and credential_matches(token, authentication.credential_digest)
            if isinstance(authentication, PairedDeviceAuthentication)
            else hmac.compare_digest(token.encode(), (await self._credential(resource)).encode())
        )
        if not isinstance(resource.transport, WebSocketDeviceTransport) or not authenticated:
            raise EnvironmentLifecycleError(
                "Device attachment authentication failed.", code="device_authentication_failed"
            )

        return DeviceAttachment(resource.device_id, await self._websocket_connections(resource, token))

    async def synchronize_registrations(self, resources: Iterable[DeviceResource]) -> None:
        for resource in resources:
            if isinstance(resource.authentication, PairedDeviceAuthentication) and resource.authentication.revoked:
                await self.revoke(resource)

    async def revoke(self, resource: DeviceResource) -> None:
        authentication = resource.authentication
        if not isinstance(authentication, PairedDeviceAuthentication):
            raise TypeError("Paired Device resource required")
        digest = authentication.credential_digest
        self._revoked.add(digest)
        for key, connections in tuple(self._websocket.items()):
            if key[1] == digest:
                await connections.close()

    async def info(self, resource: DeviceResource) -> DeviceInfo:
        summary = DeviceSummary.from_resource(resource)
        try:
            descriptor = await self.describe(resource)
        except EnvironmentLifecycleError as error:
            return DeviceInfo(**summary.model_dump(), available=False, error_code=error.code)
        return DeviceInfo(
            **summary.model_dump(),
            available=True,
            path_style=descriptor.path_style.value,
            default_working_directory=descriptor.default_working_directory,
            directory_discovery=descriptor.directory_discovery,
        )

    async def describe(self, resource: DeviceResource) -> DeviceDescriptor:
        try:
            if isinstance(resource.transport, HttpDeviceTransport):
                runtime = await self._http_runtime(resource)
                return await runtime.describe(expected_device_id=resource.device_id)
            connections = await self._websocket_connections(resource)
            return await connections.describe(
                expected_device_id=resource.device_id, timeout=resource.transport.configuration.connection_timeout
            )
        except (EIPClientError, EnvironmentProviderError) as error:
            raise EnvironmentLifecycleError("The selected Device is unavailable.", code="device_unavailable") from error

    async def list_directories(
        self, resource: DeviceResource, *, path: str | None = None, offset: int = 0, limit: int = 100
    ) -> DirectoryListResult:
        if not 1 <= limit <= 200 or not 0 <= offset <= 1_000_000:
            raise EnvironmentLifecycleError(
                "Directory query is outside supported bounds.", code="device_directory_query_invalid"
            )
        descriptor = await self.describe(resource)
        if not descriptor.directory_discovery:
            raise EnvironmentLifecycleError(
                "Directory browsing is disabled on this Device.", code="device_discovery_disabled"
            )
        try:
            params = DirectoryListParams(
                expected_device_id=descriptor.device_id,
                expected_generation=descriptor.generation,
                path=descriptor.default_working_directory if path is None else path,
                offset=offset,
                limit=limit,
            )
        except ValidationError as error:
            raise EnvironmentLifecycleError(
                "Directory path is not a canonical Device path.", code="device_directory_query_invalid"
            ) from error
        try:
            if isinstance(resource.transport, HttpDeviceTransport):
                return await (await self._http_runtime(resource)).list_directories(params)
            return await (await self._websocket_connections(resource)).list_directories(
                params, timeout=resource.transport.configuration.connection_timeout
            )
        except (EIPClientError, EnvironmentProviderError) as error:
            # A directory failure does not mark the connection offline.
            raise EnvironmentLifecycleError(
                "The Device directory is unavailable.", code="device_directory_unavailable"
            ) from error

    async def bind(
        self, binding: ResolvedEnvironmentBinding, *, environment_id: str, state: EnvironmentState | None = None
    ) -> Environment:
        resource = binding.device
        configuration = RemoteEnvdEnvironmentConfiguration(working_directory=binding.selection.working_directory)
        if isinstance(resource.transport, HttpDeviceTransport):
            return HTTP_ENVD.construct(
                configuration=configuration,
                environment_id=environment_id,
                state=state
                or EnvironmentState(
                    provider_key=HTTP_ENVD.type, state_version="1", state={"device_id": resource.device_id}
                ),
                runtime=await self._http_runtime(resource),
            )
        return WEBSOCKET_ENVD.construct(
            configuration=configuration,
            environment_id=environment_id,
            state=state
            or EnvironmentState(
                provider_key=WEBSOCKET_ENVD.type, state_version="1", state={"device_id": resource.device_id}
            ),
            runtime=WebSocketEnvdProviderRuntime(
                await self._websocket_connections(resource), resource.transport.configuration
            ),
        )

    async def close(self) -> None:
        if self._close_task is None:
            self._closed = True
            self._close_task = asyncio.create_task(self._stack.aclose(), name="harness-ui-devices-close")
        await asyncio.shield(self._close_task)
