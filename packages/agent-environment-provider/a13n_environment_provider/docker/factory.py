"""Generic lifecycle and Foundation attach-only Docker provider factory."""

from __future__ import annotations

from pydantic import BaseModel, JsonValue, ValidationError

from ..errors import EnvironmentProviderErrorCategory
from ..management import Environment, EnvironmentProvider
from ..models import EnvironmentState
from ._errors import provider_error
from .configuration import DockerAttachmentConnection, DockerProviderConfiguration
from .runtime import DockerProviderRuntime

_PROVIDER_KEY = "a13n.docker"
_CONFIGURATION_VERSION = "1"


class DockerEnvironmentProvider(EnvironmentProvider):
    """Reusable inert Docker provider with a separate attach-only capability."""

    @property
    def key(self) -> str:
        return _PROVIDER_KEY

    @property
    def configuration_versions(self) -> frozenset[str]:
        return frozenset({_CONFIGURATION_VERSION})

    @property
    def provider_key(self) -> str:
        return self.key

    @property
    def connection_versions(self) -> frozenset[str]:
        return frozenset({_CONFIGURATION_VERSION})

    def validate_configuration(self, *, schema_version: str, value: JsonValue) -> BaseModel:
        if schema_version != _CONFIGURATION_VERSION:
            raise provider_error(
                "Docker configuration version is unsupported.",
                code="provider_schema_unsupported",
                category=EnvironmentProviderErrorCategory.UNSUPPORTED,
                schema_version=schema_version,
            )
        try:
            return DockerProviderConfiguration.model_validate(value)
        except ValidationError as error:
            raise provider_error(
                "Docker configuration is invalid.",
                code="provider_spec_invalid",
                category=EnvironmentProviderErrorCategory.INVALID,
                schema_version=schema_version,
            ) from error

    def create_environment(
        self,
        *,
        configuration: BaseModel,
        state: EnvironmentState | None,
        runtime: object | None = None,
    ) -> Environment:
        from .provider import DockerEnvironment, decode_state

        if not isinstance(configuration, DockerProviderConfiguration):
            raise TypeError("Docker requires DockerProviderConfiguration")
        if not isinstance(runtime, DockerProviderRuntime):
            raise TypeError("Docker requires DockerProviderRuntime")
        state_data = decode_state(state, configuration)
        return DockerEnvironment(configuration, state, state_data=state_data, runtime=runtime)

    def validate_connection(self, *, schema_version: str, parameters: JsonValue) -> BaseModel:
        if schema_version != _CONFIGURATION_VERSION:
            raise provider_error(
                "Docker connection version is unsupported.",
                code="provider_schema_unsupported",
                category=EnvironmentProviderErrorCategory.UNSUPPORTED,
                schema_version=schema_version,
            )
        try:
            return DockerAttachmentConnection.model_validate(parameters)
        except ValidationError as error:
            raise provider_error(
                "Docker connection is invalid.",
                code="provider_connection_invalid",
                category=EnvironmentProviderErrorCategory.INVALID,
                schema_version=schema_version,
            ) from error

    def target_key(self, *, connection: BaseModel) -> str:
        if not isinstance(connection, DockerAttachmentConnection):
            raise TypeError("Docker attachment requires DockerAttachmentConnection")
        return connection.container_id

    def create_attachment_environment(
        self,
        *,
        connection: BaseModel,
        runtime: object,
    ) -> Environment:
        from .attachment import DockerAttachmentEnvironment

        if not isinstance(connection, DockerAttachmentConnection):
            raise TypeError("Docker attachment requires DockerAttachmentConnection")
        if not isinstance(runtime, DockerProviderRuntime):
            raise TypeError("Docker attachment requires DockerProviderRuntime")
        return DockerAttachmentEnvironment(connection, runtime)


__all__ = ["DockerEnvironmentProvider"]
