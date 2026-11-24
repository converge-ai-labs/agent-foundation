"""Local defaults; the Local adapter creates each environment directory at its first dispatch."""

from a13n_service.provisioning.defaults import Defaults
from a13n_service.resources.environment_templates.schemas import TemplateConfig
from a13n_service.resources.providers.schemas import ProviderCreate
from a13n_service.settings import LocalProvisioning


async def prepare(config: LocalProvisioning) -> Defaults:
    assert config.root is not None  # Required by settings when this component is enabled.
    return Defaults(
        ProviderCreate(type="local", name="Local", config={}),
        "Local Workspace",
        TemplateConfig(
            recipe={
                "root": {"path": str(config.root)},
                "shell_profiles": [{"profile_id": "sh", "executable": "/bin/sh"}],
            }
        ),
    )
