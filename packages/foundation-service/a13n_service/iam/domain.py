"""Shared identity values owned by Foundation IAM."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

ObjectId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9]{1,7}_[a-z0-9]{16,64}$")]


class PrincipalType(StrEnum):
    user = "user"
    service_account = "service_account"


class PrincipalRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    principal_type: PrincipalType
    principal_id: ObjectId
