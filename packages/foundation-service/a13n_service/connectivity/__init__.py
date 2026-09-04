"""External connectivity resources and adapter composition."""

from a13n_service.endpoint_policy import EndpointPolicy, EndpointPolicyError

from .adapters import ConnectorAdapter, IngressAdapter
from .composition import AdapterDefinition, AdapterRegistry

__all__ = [
    "AdapterDefinition",
    "AdapterRegistry",
    "ConnectorAdapter",
    "EndpointPolicy",
    "EndpointPolicyError",
    "IngressAdapter",
]
