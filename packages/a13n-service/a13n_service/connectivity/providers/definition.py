"""One native provider's configuration, ingress, and tool capabilities."""

from collections.abc import Callable
from dataclasses import dataclass

import httpx2
from a13n_harness.providers.endpoint_policy import EndpointPolicy

from ..adapters import IngressAdapter
from ..domain import JsonObject
from ..native_actions import NativeAction
from .tool_contracts import AccountTools


@dataclass(frozen=True, slots=True)
class NativeProvider:
    key: str
    config_versions: frozenset[str]
    ingress: Callable[[tuple[str, ...]], IngressAdapter]
    context_version: str
    account_tools: AccountTools
    inbound_actions: Callable[
        [JsonObject, JsonObject, JsonObject, JsonObject, httpx2.AsyncClient, EndpointPolicy], dict[str, NativeAction]
    ]
