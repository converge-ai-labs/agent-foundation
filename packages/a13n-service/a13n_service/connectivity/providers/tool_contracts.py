"""Account provider tool construction and bounded external identifiers."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

import httpx2
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from pydantic import BaseModel, StringConstraints

from a13n_service.connectivity.domain import JsonObject
from a13n_service.connectivity.native_actions import NativeAction

ProviderId = Annotated[str, StringConstraints(min_length=1, max_length=256)]


@dataclass(frozen=True, slots=True)
class AccountTools:
    scope: type[BaseModel]
    tools: frozenset[str]
    actions: Callable[[JsonObject, JsonObject, JsonObject, httpx2.AsyncClient, EndpointPolicy], dict[str, NativeAction]]
