"""Host-dialed, connect-only HTTP Envd Provider."""

import asyncio
import ssl
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from a13n_envd_client import EIPSession
from pydantic import BaseModel

from ...authentication import Authentication, CredentialMode
from ..attachments import HttpEIPSessionSource
from ..definition import EnvironmentProviderDefinition
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


@dataclass(frozen=True, slots=True)
class HttpEnvdProviderRuntime:
    configuration: HttpEnvdConnectionConfiguration
    credential: HttpEnvdCredential = field(repr=False)
    verify: ssl.SSLContext | str | bool = field(default=True, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.configuration, HttpEnvdConnectionConfiguration) or not isinstance(
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


def _backend_identity(configuration: BaseModel) -> str:
    if not isinstance(configuration, HttpEnvdConnectionConfiguration):
        raise TypeError("HTTP Envd requires HttpEnvdConnectionConfiguration")
    return configuration.endpoint


async def _runtime(
    *, configuration: BaseModel, credential: BaseModel | None, operation_id: str, allow_create: bool
) -> HttpEnvdProviderRuntime:
    del operation_id
    if allow_create:
        raise provider_error(HTTP_PROVIDER_KEY, "provider_external_only", Category.UNSUPPORTED)
    if not isinstance(configuration, HttpEnvdConnectionConfiguration) or not isinstance(credential, HttpEnvdCredential):
        raise TypeError("HTTP Envd requires HttpEnvdConnectionConfiguration and HttpEnvdCredential")
    return HttpEnvdProviderRuntime(configuration, credential)


def _construct(
    *,
    configuration: BaseModel,
    environment_id: str,
    state: EnvironmentState | None,
    runtime: HttpEnvdProviderRuntime | None,
) -> Environment:
    if not isinstance(configuration, RemoteEnvdEnvironmentConfiguration) or runtime is None:
        raise TypeError("HTTP Envd requires RemoteEnvdEnvironmentConfiguration and HttpEnvdProviderRuntime")
    data = decode_state(HTTP_PROVIDER_KEY, state)
    assert state is not None
    return RemoteEnvdEnvironment(
        provider_key=HTTP_PROVIDER_KEY,
        environment_id=environment_id,
        state=state,
        session_context=runtime.open_session(
            expected_environment_id=data.daemon_environment_id,
            required_methods=REQUIRED_METHODS | frozenset(configuration.required_methods),
        ),
    )


HTTP_ENVD = EnvironmentProviderDefinition(
    type=HTTP_PROVIDER_KEY,
    display_name="HTTP Envd",
    configuration_model=HttpEnvdConnectionConfiguration,
    credential_model=HttpEnvdCredential,
    environment_models={"1": RemoteEnvdEnvironmentConfiguration},
    construct=_construct,
    describe_environment=describe_environment,
    target_identity=lambda **kwargs: target_identity(HTTP_PROVIDER_KEY, **kwargs),
    backend_identity=_backend_identity,
    runtime_factory=_runtime,
    supports_managed=False,
    authentication=Authentication(mode=CredentialMode.required),
)
