from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class DeploymentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    request_id: UUID
    name: str = Field(min_length=1, max_length=128)
    account_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    api_token: SecretStr = Field(min_length=1, max_length=512, repr=False, json_schema_extra={"writeOnly": True})


class DeploymentRetry(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    api_token: SecretStr | None = Field(
        default=None, min_length=1, max_length=512, repr=False, json_schema_extra={"writeOnly": True}
    )


class Deployment(BaseModel):
    id: str
    version: int
    provider_id: str | None
    name: str
    account_id: str
    worker_name: str
    source_image: str
    status: Literal["deploying", "ready", "retiring", "needs_attention", "retired"]
    operation: Literal["deploy", "retire"]
    stage: str
    endpoint: str | None
    error: str | None
    cleanup: dict[str, Literal["removed", "pending", "not_created"]]


class DeploymentList(BaseModel):
    available: bool
    items: list[Deployment]
