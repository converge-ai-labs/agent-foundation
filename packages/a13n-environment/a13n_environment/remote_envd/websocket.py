"""External reverse WebSocket Provider with an explicit Host SDK collaborator."""

from dataclasses import dataclass, field

from pydantic import BaseModel

from ..errors import EnvironmentProviderErrorCategory as Category
from ..management import Environment, ProviderRuntimeContext
from ..models import EnvironmentState
from .configuration import RemoteEnvdProviderConfiguration, WebSocketEnvdBackendConfiguration
from .connections import WEBSOCKET_PROVIDER_KEY, WebSocketEnvdConnections
from .environment import REQUIRED_METHODS, RemoteEnvdEnvironment, decode_state, provider_error
from .provider import RemoteEnvdProvider


@dataclass(frozen=True, slots=True)
class WebSocketEnvdProviderRuntime:
    connections: WebSocketEnvdConnections = field(repr=False)
    configuration: WebSocketEnvdBackendConfiguration = field(default_factory=WebSocketEnvdBackendConfiguration)

    def __post_init__(self) -> None:
        if not isinstance(self.connections, WebSocketEnvdConnections) or not isinstance(
            self.configuration, WebSocketEnvdBackendConfiguration
        ):
            raise TypeError("WebSocket Envd requires a Host-owned connection SDK and validated configuration")


class WebSocketEnvdEnvironmentProvider(RemoteEnvdProvider):
    provider_configuration_model = WebSocketEnvdBackendConfiguration

    def __init__(self, *, connections: WebSocketEnvdConnections | None = None) -> None:
        # Catalog discovery is inert. Embedded Hosts can explicitly register an
        # instance wired to their connection SDK instead of the unwired built-in.
        self._connections = connections

    @property
    def display_name(self) -> str:
        return "WebSocket Envd"

    @property
    def key(self) -> str:
        return WEBSOCKET_PROVIDER_KEY

    def backend_identity(self, configuration: BaseModel) -> str:
        if not isinstance(configuration, WebSocketEnvdBackendConfiguration):
            raise TypeError("WebSocket Envd requires WebSocketEnvdBackendConfiguration")
        # One explicitly injected connection SDK owns the daemon identity namespace.
        return self.key

    async def create_runtime(
        self, *, configuration: BaseModel, credential: BaseModel | None, context: ProviderRuntimeContext
    ) -> WebSocketEnvdProviderRuntime:
        if context.managed:
            raise provider_error(self.key, "provider_external_only", Category.UNSUPPORTED)
        if not isinstance(configuration, WebSocketEnvdBackendConfiguration) or credential is not None:
            raise TypeError("WebSocket Envd requires WebSocketEnvdBackendConfiguration and no stored credential")
        if self._connections is None:
            raise provider_error(self.key, "provider_runtime_required", Category.UNAVAILABLE)
        return WebSocketEnvdProviderRuntime(self._connections, configuration)

    def create_environment(
        self,
        *,
        configuration: BaseModel,
        environment_id: str,
        state: EnvironmentState | None,
        runtime: object | None = None,
    ) -> Environment:
        if not isinstance(configuration, RemoteEnvdProviderConfiguration) or not isinstance(
            runtime, WebSocketEnvdProviderRuntime
        ):
            raise TypeError("WebSocket Envd requires RemoteEnvdProviderConfiguration and WebSocketEnvdProviderRuntime")
        data = decode_state(self.key, state)
        assert state is not None
        return RemoteEnvdEnvironment(
            provider_key=self.key,
            environment_id=environment_id,
            state=state,
            session_context=runtime.connections.open_session(
                expected_device_id=data.device_id,
                working_directory=configuration.working_directory,
                required_methods=REQUIRED_METHODS | frozenset(configuration.required_methods),
                timeout=runtime.configuration.connection_timeout,
            ),
        )
