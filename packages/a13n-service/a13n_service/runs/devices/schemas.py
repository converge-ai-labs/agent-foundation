"""Human-managed reverse monitoring registrations; attachment credentials are issued once."""

from datetime import datetime
from typing import Annotated

from a13n_envd_client.eip.v1.models import Identifier
from pydantic import BaseModel, ConfigDict, Field

from a13n_service.infra.ids import ObjectId

Name = Annotated[str, Field(min_length=1, max_length=160)]


class DeviceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Name
    device_id: Identifier
    metrics_data_source_id: ObjectId | None = None


class DeviceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Name | None = None
    enabled: bool | None = None
    metrics_data_source_id: ObjectId | None = None


class Device(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    organization_id: str
    workspace_id: str
    name: str
    device_id: str
    enabled: bool
    metrics_data_source_id: str | None
    version: int
    created_at: datetime
    updated_at: datetime


class DeviceCredential(BaseModel):
    device: Device
    token: str = Field(repr=False)


class DevicePage(BaseModel):
    items: list[Device]
    next_cursor: str | None
