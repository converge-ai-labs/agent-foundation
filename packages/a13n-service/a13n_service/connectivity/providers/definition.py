"""One native provider's configuration, ingress, and tool capabilities."""

from collections.abc import Callable
from dataclasses import dataclass

import httpx2
from a13n_harness.providers.endpoint_policy import EndpointPolicy

from ..adapters import IngressAdapter
from ..domain import JsonObject
from ..native_actions import NativeAction
from ..subscriptions import EventSubscriptions
from .tool_contracts import AccountTools


@dataclass(frozen=True, slots=True)
class InboundActionContext:
    """The admitted conversation, its Agent policy and the account's own credentials."""

    provider_context: JsonObject
    action_policy: JsonObject
    configuration: JsonObject
    credentials: JsonObject
    http: httpx2.AsyncClient
    endpoints: EndpointPolicy


@dataclass(frozen=True, slots=True)
class NativeProvider:
    key: str
    config_versions: frozenset[str]
    ingress: Callable[[tuple[str, ...]], IngressAdapter]
    context_version: str
    account_tools: AccountTools
    inbound_actions: Callable[[InboundActionContext], dict[str, NativeAction]]
    event_subscriptions: EventSubscriptions | None = None
