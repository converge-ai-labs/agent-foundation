"""External connectivity resources and adapter composition."""

from .adapters import ConnectorAdapter, IngressAdapter
from .composition import AdapterDefinition, AdapterRegistry
from .outbound_policy import EndpointPolicy, EndpointPolicyError

__all__ = [
    "AdapterDefinition",
    "AdapterRegistry",
    "ConnectorAdapter",
    "EndpointPolicy",
    "EndpointPolicyError",
    "IngressAdapter",
]
