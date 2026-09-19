"""Load the frozen template configuration or the registered external connection configuration."""

from a13n_envd_client.eip.v1.models import AbsoluteEIPPath
from a13n_harness.providers.environment.remote_envd.connections import WEBSOCKET_PROVIDER_KEY
from a13n_harness.providers.environment.remote_envd.http import HTTP_PROVIDER_KEY
from pydantic import TypeAdapter
from sqlalchemy.ext.asyncio import AsyncSession

from .domain import EnvironmentConfiguration, TemplateConfiguration
from .models import EnvironmentRecord, EnvironmentTemplateRevisionRecord


def execution_configuration(
    provider_key: str,
    configuration: EnvironmentConfiguration | TemplateConfiguration,
    working_directory: str | None,
) -> EnvironmentConfiguration | TemplateConfiguration:
    """Apply an accepted binding only to its adapter, never the shared resource."""
    if provider_key not in {HTTP_PROVIDER_KEY, WEBSOCKET_PROVIDER_KEY}:
        if working_directory is not None:
            raise ValueError("Native Environment bindings cannot override their working directory")
        return configuration
    if working_directory is None:
        raise ValueError("Device execution requires an accepted working directory")
    directory = TypeAdapter(AbsoluteEIPPath).validate_python(working_directory)
    return configuration.model_copy(
        update={"configuration": {**configuration.configuration, "working_directory": directory}}
    )


async def load_configuration(
    session: AsyncSession, environment: EnvironmentRecord
) -> EnvironmentConfiguration | TemplateConfiguration:
    if environment.ownership == "external":
        return EnvironmentConfiguration.model_validate(environment.external_configuration)
    if environment.template_revision_id is None:
        raise ValueError("Managed Environment template configuration is missing")
    revision = await session.get(EnvironmentTemplateRevisionRecord, environment.template_revision_id)
    if revision is None:
        raise ValueError("Managed Environment template configuration is unavailable")
    return TemplateConfiguration.model_validate(revision.template_config)
