"""Github Account and Ingress capabilities."""

from ..definition import NativeProvider
from .account_tools import ACCOUNT_TOOLS
from .adapter import GitHubIngressAdapter
from .inbound_tools import inbound_actions
from .subscriptions import GitHubSubscriptions
from .wire import CONTEXT_VERSION

PROVIDER = NativeProvider(
    GitHubIngressAdapter.provider_key,
    GitHubIngressAdapter.config_versions,
    lambda origins: GitHubIngressAdapter(allowed_provider_origins=origins),
    CONTEXT_VERSION,
    ACCOUNT_TOOLS,
    inbound_actions,
    event_subscriptions=GitHubSubscriptions(),
)
