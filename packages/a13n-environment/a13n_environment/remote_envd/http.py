"""Host-dialed, connect-only HTTP Envd Provider."""

import asyncio
import ssl
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from a13n_envd_client import EIPSession
from pydantic import BaseModel

from ..attachments import HttpEIPSessionSource
from ..errors import EnvironmentProviderErrorCategory as Category
from ..management import Environment, ProviderRuntimeContext
from ..models import EnvironmentState
from .configuration import HttpEnvdBackendConfiguration, HttpEnvdCredential, RemoteEnvdProviderConfiguration
from .environment import REQUIRED_METHODS, RemoteEnvdEnvironment, decode_state, provider_error
from .provider import RemoteEnvdProvider

HTTP_PROVIDER_KEY = "a13n.http-envd"


@dataclass(frozen=True, slots=True)
class HttpEnvdProviderRuntime:
    configuration: HttpEnvdBackendConfiguration
    credential: HttpEnvdCredential = field(repr=False)
    verify: ssl.SSLContext | str | bool = field(default=True, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.configuration, HttpEnvdBackendConfiguration) or not isinstance(
            self.credential, HttpEnvdCredential
        ):
            raise TypeError("HTTP Envd requires validated backend configuration and credential")
        if self.verify is False:
            raise ValueError("HTTP TLS verification cannot be disabled")

    @asynccontextmanager
    async def open_session(
        self, *, expected_environment_id: str, required_methods: frozenset[str]
    ) -> AsyncIterator[EIPSession]:
        configuration = self.configuration
        source = HttpEIPSessionSource(
            configuration.endpoint,
            self.credential.token.get_secret_value(),
            verify=self.verify,
            initialization_timeout=configuration.initialization_timeout,
            request_timeout=configuration.request_timeout,
            max_in_flight=configuration.max_in_flight,
            allow_plaintext_private_link=configuration.allow_plaintext_private_link,
        )
        context = source.open_session(
            expected_environment_id=expected_environment_id, required_methods=required_methods
        )
        try:
            session = await context.__aenter__()
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            raise provider_error(HTTP_PROVIDER_KEY, "provider_connection_timeout", Category.TIMEOUT) from None
        except Exception:
            raise provider_error(HTTP_PROVIDER_KEY, "provider_connection_failed", Category.UNAVAILABLE) from None
        try:
            yield session
        finally:
            try:
                await context.__aexit__(None, None, None)
            except asyncio.CancelledError:
                await session.abort()
                raise
            except Exception:
                raise provider_error(HTTP_PROVIDER_KEY, "provider_cleanup_failed", Category.CLEANUP) from None


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
                expected_environment_id=data.daemon_environment_id,
                required_methods=REQUIRED_METHODS | frozenset(configuration.required_methods),
            ),
        )
