"""Slack Account and Ingress capabilities."""

from ..definition import NativeProvider
from .account_tools import ACCOUNT_TOOLS
from .adapter import CONTEXT_VERSION, SlackIngressAdapter
from .inbound_tools import inbound_actions

PROVIDER = NativeProvider(
    SlackIngressAdapter.provider_key,
    SlackIngressAdapter.config_versions,
    lambda origins: SlackIngressAdapter(),
    CONTEXT_VERSION,
    ACCOUNT_TOOLS,
    inbound_actions,
)
