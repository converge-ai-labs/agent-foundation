"""Lark Account and Ingress capabilities."""

from ..definition import NativeProvider
from .account_tools import ACCOUNT_TOOLS
from .adapter import LarkIngressAdapter
from .inbound_tools import inbound_actions
from .wire import CONTEXT_VERSION

PROVIDER = NativeProvider(
    LarkIngressAdapter.provider_key,
    LarkIngressAdapter.config_versions,
    lambda origins: LarkIngressAdapter(allowed_provider_origins=origins),
    CONTEXT_VERSION,
    ACCOUNT_TOOLS,
    inbound_actions,
)
