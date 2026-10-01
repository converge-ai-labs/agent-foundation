"""Host-dialed, connect-only HTTP Envd Provider."""

from __future__ import annotations

import asyncio
import ssl
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from a13n_envd_client import EIPDeviceConnection, EIPSession, HttpTransport
from a13n_envd_client.eip.v1 import DeviceDescriptor, DirectoryListParams, DirectoryListResult, ErrorType
from a13n_envd_client.errors import EIPMethodError
from pydantic import BaseModel

from a13n_harness.http import outbound_tls_verify

from ...authentication import Authentication, CredentialMode
from ..attachments import DeviceEIPSessionSource
from ..definition import EnvironmentProviderDefinition
from ..envd_policy import (
    EnvdBoundaryRequirement,
    EnvdCredentialResolver,
    EnvdEgressConfiguration,
    EnvironmentEnvdCredentialResolver,
    resolve_egress,
)
from ..errors import EnvironmentProviderErrorCategory as Category
from ..errors import provider_error
from ..management import Environment
from ..models import EnvironmentState
from .configuration import HttpEnvdConnectionConfiguration, HttpEnvdCredential, RemoteEnvdEnvironmentConfiguration
from .environment import (
    REQUIRED_METHODS,
    RemoteEnvdEnvironment,
    decode_state,
    describe_environment,
    target_identity,
)

HTTP_PROVIDER_KEY = "http_envd"


@dataclass(slots=True)
class HttpEnvdProviderRuntime:
    """One Host-owned Device connection; independent Sessions share it."""

    configuration: HttpEnvdConnectionConfiguration
    credential: HttpEnvdCredential = field(repr=False)
    verify: ssl.SSLContext | str | bool = field(default_factory=outbound_tls_verify, repr=False)

    credential_resolver: EnvdCredentialResolver = field(default_factory=EnvironmentEnvdCredentialResolver, repr=False)

    _device: EIPDeviceConnection | None = field(default=None, init=False, repr=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)
    _closed: bool = field(default=False, init=False)
    _close_task: asyncio.Task[None] | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.configuration, HttpEnvdConnectionConfiguration) or not isinstance(
            self.credential, HttpEnvdCredential
        ):
            raise TypeError("HTTP Envd requires validated backend configuration and credential")

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
                except Exception as error:
                    if expected_device_id is not None and _refused_as_incompatible(error):
                        raise provider_error(HTTP_PROVIDER_KEY, "provider_device_mismatch", Category.INVALID) from None
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
        egress: EnvdEgressConfiguration | None = None,
        expected_boundary: EnvdBoundaryRequirement | None = None,
    ) -> AsyncIterator[EIPSession]:
        device = await self.acquire_device(expected_device_id=expected_device_id)
        async with DeviceEIPSessionSource(device).open_session(
            expected_device_id=expected_device_id,
            required_methods=required_methods,
            working_directory=working_directory,
            egress=await resolve_egress(egress, self.credential_resolver),
            expected_boundary=expected_boundary,
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


def _refused_as_incompatible(error: Exception) -> bool:
    """Whether the daemon refused to initialize as incompatible: for an expected device, that it is another
    device, or one no longer speaking the only protocol version the client offers."""
    return isinstance(error, EIPMethodError) and error.error.data.error_type == ErrorType.PROTOCOL_INCOMPATIBLE


def _backend_identity(configuration: BaseModel) -> str:
    if not isinstance(configuration, HttpEnvdConnectionConfiguration):
        raise TypeError("HTTP Envd requires HttpEnvdConnectionConfiguration")
    return configuration.endpoint


async def _runtime(*, configuration: BaseModel, credential: BaseModel | None) -> HttpEnvdProviderRuntime:
    if not isinstance(configuration, HttpEnvdConnectionConfiguration) or not isinstance(credential, HttpEnvdCredential):
        raise TypeError("HTTP Envd requires HttpEnvdConnectionConfiguration and HttpEnvdCredential")
    return HttpEnvdProviderRuntime(configuration, credential)


def _construct(
    *,
    configuration: RemoteEnvdEnvironmentConfiguration,
    environment_id: str,
    state: EnvironmentState | None,
    runtime: HttpEnvdProviderRuntime | None,
    operation_id: str,
    allow_create: bool,
) -> Environment:
    del operation_id, allow_create
    if not isinstance(configuration, RemoteEnvdEnvironmentConfiguration) or runtime is None:
        raise TypeError("HTTP Envd requires RemoteEnvdEnvironmentConfiguration and HttpEnvdProviderRuntime")
    data = decode_state(HTTP_PROVIDER_KEY, state)
    assert state is not None
    return RemoteEnvdEnvironment(
        provider_key=HTTP_PROVIDER_KEY,
        environment_id=environment_id,
        working_directory=configuration.working_directory,
        state=state,
        session_context=runtime.open_session(
            expected_device_id=data.device_id,
            working_directory=configuration.working_directory,
            required_methods=REQUIRED_METHODS | frozenset(configuration.required_methods),
            egress=configuration.egress,
            expected_boundary=configuration.expected_boundary,
        ),
    )


HTTP_ENVD = EnvironmentProviderDefinition(
    type=HTTP_PROVIDER_KEY,
    display_name="HTTP Envd",
    configuration_model=HttpEnvdConnectionConfiguration,
    credential_model=HttpEnvdCredential,
    environment_model=RemoteEnvdEnvironmentConfiguration,
    construct=_construct,
    describe_environment=describe_environment,
    target_identity=lambda **kwargs: target_identity(HTTP_PROVIDER_KEY, **kwargs),
    backend_identity=_backend_identity,
    runtime_factory=_runtime,
    supports_managed=False,
    authentication=Authentication(mode=CredentialMode.required),
)
