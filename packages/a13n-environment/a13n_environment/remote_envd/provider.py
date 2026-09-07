"""Shared remote Provider codecs and configured capability projection."""

from pydantic import BaseModel, JsonValue

from ..eip.binding import configured_descriptor
from ..errors import EnvironmentProviderErrorCategory as Category
from ..management import EnvironmentProvider
from ..models import EnvironmentDescriptor, EnvironmentState
from .configuration import RemoteEnvdProviderConfiguration
from .environment import decode_state, provider_error


class RemoteEnvdProvider(EnvironmentProvider):
    supports_managed = False

    @property
    def configuration_versions(self) -> frozenset[str]:
        return frozenset({"1"})

    def validate_configuration(self, *, schema_version: str, value: JsonValue) -> RemoteEnvdProviderConfiguration:
        if schema_version != "1":
            raise provider_error(self.key, "provider_schema_unsupported", Category.UNSUPPORTED)
        try:
            return RemoteEnvdProviderConfiguration.model_validate(value)
        except ValueError:
            raise provider_error(self.key, "provider_spec_invalid", Category.INVALID) from None

    def describe_configuration(self, configuration: BaseModel) -> EnvironmentDescriptor:
        if not isinstance(configuration, RemoteEnvdProviderConfiguration):
            raise TypeError("Remote Envd requires RemoteEnvdProviderConfiguration")
        return configured_descriptor()

    def target_identity(self, *, configuration: BaseModel, state: EnvironmentState | None) -> str:
        self.describe_configuration(configuration)
        return decode_state(self.key, state).daemon_environment_id
