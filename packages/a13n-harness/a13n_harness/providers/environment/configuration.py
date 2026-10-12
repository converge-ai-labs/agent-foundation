import socket

from pydantic import BaseModel, ConfigDict, Field


class EnvironmentProviderConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class HostLocalProviderConfiguration(EnvironmentProviderConfiguration):
    host_id: str = Field(default_factory=socket.gethostname, min_length=1, max_length=256)
