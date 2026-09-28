"""The resource pair a provisioning component prepares before a short database transaction."""

from dataclasses import dataclass

from a13n_service.resources.environment_templates.schemas import TemplateConfig
from a13n_service.resources.providers.schemas import ProviderCreate


@dataclass(frozen=True)
class Defaults:
    provider: ProviderCreate
    template_name: str
    template_config: TemplateConfig
