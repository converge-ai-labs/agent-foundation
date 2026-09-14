"""OSS deployment-owned local backends, published as ordinary Organization Providers."""

from collections.abc import Mapping

from a13n_environment import EnvironmentProviderCatalog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.iam.service_common import lock_bootstrap, singleton_organization
from a13n_service.ids import new_object_id
from a13n_service.storage import transaction
from a13n_service.temporal import utc_now

from .domain import JsonObject, LocalProviderType
from .models import EnvironmentProviderRecord


async def synchronize_local_providers(
    sessions: async_sessionmaker[AsyncSession],
    catalog: EnvironmentProviderCatalog,
    configurations: Mapping[LocalProviderType, JsonObject],
) -> None:
    """Reuse exact configurations; never retarget a Provider referenced by a template.

    Removed or replaced backends are disabled, preserving their references. The
    OSS bootstrap lock serializes startup across control replicas.
    """
    desired = {
        key: catalog.require(key).provider_configuration_model.model_validate(value).model_dump(mode="json")
        for key, value in configurations.items()
    }
    async with transaction(sessions) as session:
        await lock_bootstrap(session)
        organization = await singleton_organization(session)
        rows = tuple(
            await session.scalars(
                select(EnvironmentProviderRecord).where(
                    EnvironmentProviderRecord.organization_id == organization.id,
                    EnvironmentProviderRecord.configuration_source == "deployment",
                )
            )
        )
        now = utc_now()
        for key, configuration in desired.items():
            if any(row.type == key and row.configuration == configuration for row in rows):
                continue
            session.add(
                EnvironmentProviderRecord(
                    id=new_object_id("envp"),
                    organization_id=organization.id,
                    workspace_id=None,
                    type=key,
                    name=catalog.require(key).display_name,
                    configuration=configuration,
                    configuration_source="deployment",
                    enabled=True,
                    credential_generation=0,
                    created_at=now,
                    updated_at=now,
                )
            )
        for row in rows:
            enabled = row.type in desired and row.configuration == desired[row.type]
            if row.enabled != enabled:
                row.enabled = enabled
                row.updated_at = now
