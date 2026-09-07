"""Load the frozen recipe or the registered external connection configuration."""

from sqlalchemy.ext.asyncio import AsyncSession

from .domain import EnvironmentConfiguration, TemplateConfiguration
from .models import EnvironmentRecord, EnvironmentTemplateRevisionRecord


async def load_configuration(
    session: AsyncSession, environment: EnvironmentRecord
) -> EnvironmentConfiguration | TemplateConfiguration:
    if environment.ownership == "external":
        return EnvironmentConfiguration.model_validate(environment.external_configuration)
    if environment.template_revision_id is None:
        raise ValueError("Managed Environment recipe is missing")
    revision = await session.get(EnvironmentTemplateRevisionRecord, environment.template_revision_id)
    if revision is None:
        raise ValueError("Managed Environment recipe is unavailable")
    return TemplateConfiguration.model_validate(revision.recipe)
