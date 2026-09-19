"""Shared remote Provider codecs and configured capability projection."""

from pydantic import BaseModel

from ..eip.binding import configured_descriptor
from ..management import EnvironmentProvider
from ..models import EnvironmentDescriptor, EnvironmentState
from .configuration import RemoteEnvdProviderConfiguration
from .environment import decode_state


class RemoteEnvdProvider(EnvironmentProvider):
    supports_managed = False

    @property
    def configuration_models(self) -> dict[str, type[BaseModel]]:
        return {"1": RemoteEnvdProviderConfiguration}

    def describe_configuration(self, configuration: BaseModel) -> EnvironmentDescriptor:
        if not isinstance(configuration, RemoteEnvdProviderConfiguration):
            raise TypeError("Remote Envd requires RemoteEnvdProviderConfiguration")
        return configured_descriptor()

    def target_identity(self, *, configuration: BaseModel, state: EnvironmentState | None) -> str:
        self.describe_configuration(configuration)
        return decode_state(self.key, state).device_id
