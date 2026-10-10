from __future__ import annotations

from a13n_environment.direct_local.configuration import DirectLocalEnvironmentConfiguration
from a13n_environment.direct_local.provider import DIRECT_LOCAL
from a13n_environment.direct_local.shared import _DirectLocalFilePolicy as DirectLocalFilePolicy
from a13n_environment.execution import EnvironmentConnector

__all__ = ["DirectLocalFilePolicy", "DirectLocalSource", "Source"]


class Source:
    """A Host that has already prepared its connector's target."""

    def __init__(self, connector: EnvironmentConnector):
        self.connector = connector
        self.ready_calls = 0

    @property
    def provider_key(self):
        return self.connector.provider_key

    @property
    def environment_id(self):
        return self.connector.environment_id

    @property
    def descriptor(self):
        return self.connector.descriptor

    @property
    def state(self):
        return self.connector.state

    async def ensure_ready(self):
        self.ready_calls += 1
        return self.connector


class DirectLocalSource(Source):
    def __init__(self, configuration: DirectLocalEnvironmentConfiguration, *, environment_id: str):
        super().__init__(DIRECT_LOCAL.execution_connector(configuration, environment_id=environment_id))
