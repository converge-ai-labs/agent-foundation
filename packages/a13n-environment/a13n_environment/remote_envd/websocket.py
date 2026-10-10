"""External reverse WebSocket Provider with an explicit Host SDK collaborator."""

from dataclasses import dataclass, field

from pydantic import BaseModel

from .._backend import ExecutionBackend
from .._backend_factory import BackendFactory
from ..definition import EnvironmentProviderDefinition
from ..errors import EnvironmentProviderErrorCategory as Category
from ..errors import provider_error
from ..models import EnvironmentState
from .configuration import RemoteEnvdEnvironmentConfiguration, WebSocketEnvdConnectionConfiguration
from .connections import WEBSOCKET_PROVIDER_KEY, WebSocketEnvdConnections
from .environment import (
    REQUIRED_METHODS,
    RemoteEnvdExecution,
    decode_state,
    describe_environment,
    target_identity,
)


@dataclass(frozen=True, slots=True)
class WebSocketEnvdProviderRuntime:
    connections: WebSocketEnvdConnections = field(repr=False)
    configuration: WebSocketEnvdConnectionConfiguration = field(default_factory=WebSocketEnvdConnectionConfiguration)

    def __post_init__(self) -> None:
        if not isinstance(self.connections, WebSocketEnvdConnections) or not isinstance(
            self.configuration, WebSocketEnvdConnectionConfiguration
        ):
            raise TypeError("WebSocket Envd requires a Host-owned connection SDK and validated configuration")


def _backend_identity(configuration: BaseModel) -> str:
    if not isinstance(configuration, WebSocketEnvdConnectionConfiguration):
        raise TypeError("WebSocket Envd requires WebSocketEnvdConnectionConfiguration")
    # One explicitly injected connection SDK owns the daemon identity namespace.
    return WEBSOCKET_PROVIDER_KEY


async def _runtime(*, configuration: BaseModel, credential: BaseModel | None) -> WebSocketEnvdProviderRuntime:
    if not isinstance(configuration, WebSocketEnvdConnectionConfiguration) or credential is not None:
        raise TypeError("WebSocket Envd requires WebSocketEnvdConnectionConfiguration and no stored credential")
    raise provider_error(WEBSOCKET_PROVIDER_KEY, "provider_runtime_required", Category.UNAVAILABLE)


def _construct(
    *,
    configuration: RemoteEnvdEnvironmentConfiguration,
    environment_id: str,
    state: EnvironmentState | None,
    runtime: WebSocketEnvdProviderRuntime | None,
) -> ExecutionBackend:
    if not isinstance(configuration, RemoteEnvdEnvironmentConfiguration) or runtime is None:
        raise TypeError("WebSocket Envd requires RemoteEnvdEnvironmentConfiguration and WebSocketEnvdProviderRuntime")
    data = decode_state(WEBSOCKET_PROVIDER_KEY, state)
    assert state is not None
    return RemoteEnvdExecution(
        provider_key=WEBSOCKET_PROVIDER_KEY,
        environment_id=environment_id,
        working_directory=configuration.working_directory,
        state=state,
        session_context=runtime.connections.open_session(
            expected_device_id=data.device_id,
            working_directory=configuration.working_directory,
            required_methods=REQUIRED_METHODS | frozenset(configuration.required_methods),
            egress=configuration.egress,
            expected_boundary=configuration.expected_boundary,
            timeout=runtime.configuration.connection_timeout,
        ),
    )


_factory = BackendFactory(
    key=WEBSOCKET_PROVIDER_KEY,
    environment_model=RemoteEnvdEnvironmentConfiguration,
    execution=_construct,
    describe=describe_environment,
    target_identity=lambda **kwargs: target_identity(WEBSOCKET_PROVIDER_KEY, **kwargs),
    runtime_factory=_runtime,
)


WEBSOCKET_ENVD = EnvironmentProviderDefinition(
    type=WEBSOCKET_PROVIDER_KEY,
    display_name="WebSocket Envd",
    configuration_model=WebSocketEnvdConnectionConfiguration,
    environment_model=RemoteEnvdEnvironmentConfiguration,
    connector_factory=_factory.connector,
    describe_environment=describe_environment,
    target_identity=lambda **kwargs: target_identity(WEBSOCKET_PROVIDER_KEY, **kwargs),
    backend_identity=_backend_identity,
    supports_managed=False,
)
