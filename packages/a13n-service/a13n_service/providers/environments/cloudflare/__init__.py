"""Cloudflare Containers management composition."""

from a13n_harness.providers.environment.cloudflare.provider import CLOUDFLARE as EXECUTION
from a13n_harness.providers.environment.cloudflare.provider import runtime
from a13n_harness.providers.environment.cloudflare.shared import CloudflareEnvironmentConfiguration

from a13n_service.providers.environment._backend_factory import ManagementBackendFactory
from a13n_service.providers.environment.definition import management_definition

from .management import CloudflareManagement

_factory = ManagementBackendFactory(
    key="cloudflare",
    environment_model=CloudflareEnvironmentConfiguration,
    management=CloudflareManagement,
    runtime_factory=runtime,
)
CLOUDFLARE = management_definition(
    EXECUTION,
    environment_model=CloudflareEnvironmentConfiguration,
    provider_factory=_factory.provider,
    supports_stop=True,
    supports_destroy=True,
    requires_keepalive=True,
)
