"""Host-dialed, connect-only HTTP Envd Provider."""

from __future__ import annotations

import asyncio
import ssl
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from a13n_envd_client import EIPDeviceConnection, EIPSession, HttpTransport
from a13n_envd_client.eip.v1 import DeviceDescriptor, DirectoryListParams, DirectoryListResult
from pydantic import BaseModel

from ..attachments import DeviceEIPSessionSource
from ..errors import EnvironmentProviderErrorCategory as Category
from ..management import Environment, ProviderRuntimeContext
from ..models import EnvironmentState
from .configuration import HttpEnvdBackendConfiguration, HttpEnvdCredential, RemoteEnvdProviderConfiguration
from .environment import REQUIRED_METHODS, RemoteEnvdEnvironment, decode_state, provider_error
from .provider import RemoteEnvdProvider

HTTP_PROVIDER_KEY = "a13n.http-envd"


@dataclass(slots=True)
class HttpEnvdProviderRuntime:
    configuration: HttpEnvdBackendConfiguration
    credential: HttpEnvdCredential = field(repr=False)
    verify: ssl.SSLContext | str | bool = field(default=True, repr=False)

    _device: EIPDeviceConnection | None = field(default=None, init=False, repr=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)
    _closed: bool = field(default=False, init=False)
    _close_task: asyncio.Task[None] | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.configuration, HttpEnvdBackendConfiguration) or not isinstance(
            self.credential, HttpEnvdCredential
        ):
            raise TypeError("HTTP Envd requires validated backend configuration and credential")
        if self.verify is False:
            raise ValueError("HTTP TLS verification cannot be disabled")

    async def acquire_device(self, *, expected_device_id: str | None) -> EIPDeviceConnection:
        """Use None only for explicit first-contact registration by the Host."""
        async with self._lock:
            if self._closed:
                raise provider_error(HTTP_PROVIDER_KEY, "provider_connections_closed", Category.UNAVAILABLE)
            if self._device is not None and self._device.is_closed:
                # A later explicit use may replace a terminal carrier, never a
                # live Session or an uncertain operation from the previous one.
                await self._device.close()
                self._device = None
            if self._device is None:
                configuration = self.configuration
                transport = HttpTransport(
                    configuration.endpoint,
                    self.credential.token.get_secret_value(),
                    verify=self.verify,
                    request_timeout=configuration.request_timeout,
                    allow_plaintext_private_link=configuration.allow_plaintext_private_link,
                )
                try:
                    self._device = await EIPDeviceConnection.initialize(
                        transport,
                        expected_device_id=expected_device_id,
                        initialization_timeout=configuration.initialization_timeout,
                        request_timeout=configuration.request_timeout,
                        max_in_flight=configuration.max_in_flight,
                    )
                except TimeoutError:
                    raise provider_error(HTTP_PROVIDER_KEY, "provider_connection_timeout", Category.TIMEOUT) from None
                except Exception:
                    raise provider_error(
                        HTTP_PROVIDER_KEY, "provider_connection_failed", Category.UNAVAILABLE
                    ) from None
            if expected_device_id is not None and self._device.descriptor.device_id != expected_device_id:
                raise provider_error(HTTP_PROVIDER_KEY, "provider_device_mismatch", Category.INVALID)
            return self._device

    async def describe(self, *, expected_device_id: str | None) -> DeviceDescriptor:
        return await (await self.acquire_device(expected_device_id=expected_device_id)).describe()

    async def list_directories(self, params: DirectoryListParams) -> DirectoryListResult:
        return await (await self.acquire_device(expected_device_id=params.expected_device_id)).list_directories(params)

    @asynccontextmanager
    async def open_session(
        self,
        *,
        expected_device_id: str,
        required_methods: frozenset[str],
        working_directory: str | None = None,
    ) -> AsyncIterator[EIPSession]:
        device = await self.acquire_device(expected_device_id=expected_device_id)
        async with DeviceEIPSessionSource(device).open_session(
            expected_device_id=expected_device_id,
            required_methods=required_methods,
            working_directory=working_directory,
        ) as session:
            yield session

    async def close(self) -> None:
        if self._close_task is None:
            self._closed = True
            self._close_task = asyncio.create_task(self._shutdown(), name="http-envd-runtime-close")
        await asyncio.shield(self._close_task)

    async def _shutdown(self) -> None:
        async with self._lock:
            if self._device is not None:
                await self._device.close()

    async def __aenter__(self) -> HttpEnvdProviderRuntime:
        if self._closed:
            raise provider_error(HTTP_PROVIDER_KEY, "provider_connections_closed", Category.UNAVAILABLE)
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()


class HttpEnvdEnvironmentProvider(RemoteEnvdProvider):
    provider_configuration_model = HttpEnvdBackendConfiguration
    credential_model = HttpEnvdCredential

    @property
    def display_name(self) -> str:
        return "HTTP Envd"

    @property
    def key(self) -> str:
        return HTTP_PROVIDER_KEY

    def backend_identity(self, configuration: BaseModel) -> str:
        if not isinstance(configuration, HttpEnvdBackendConfiguration):
            raise TypeError("HTTP Envd requires HttpEnvdBackendConfiguration")
        return configuration.endpoint

    async def create_runtime(
        self, *, configuration: BaseModel, credential: BaseModel | None, context: ProviderRuntimeContext
    ) -> HttpEnvdProviderRuntime:
        if context.managed:
            raise provider_error(self.key, "provider_external_only", Category.UNSUPPORTED)
        if not isinstance(configuration, HttpEnvdBackendConfiguration) or not isinstance(
            credential, HttpEnvdCredential
        ):
            raise TypeError("HTTP Envd requires HttpEnvdBackendConfiguration and HttpEnvdCredential")
        return HttpEnvdProviderRuntime(configuration, credential)

    def create_environment(
        self,
        *,
        configuration: BaseModel,
        environment_id: str,
        state: EnvironmentState | None,
        runtime: object | None = None,
    ) -> Environment:
        if not isinstance(configuration, RemoteEnvdProviderConfiguration) or not isinstance(
            runtime, HttpEnvdProviderRuntime
        ):
            raise TypeError("HTTP Envd requires RemoteEnvdProviderConfiguration and HttpEnvdProviderRuntime")
        data = decode_state(self.key, state)
        assert state is not None
        return RemoteEnvdEnvironment(
            provider_key=self.key,
            environment_id=environment_id,
            state=state,
            session_context=runtime.open_session(
                expected_device_id=data.device_id,
                working_directory=configuration.working_directory,
                required_methods=REQUIRED_METHODS | frozenset(configuration.required_methods),
            ),
        )
