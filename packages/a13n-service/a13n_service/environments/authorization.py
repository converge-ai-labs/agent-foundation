"""Attempt-cached Environment binding and Provider eligibility."""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.iam.domain import AuthorizationError

from .models import EnvironmentProviderRecord, EnvironmentRecord


@dataclass(frozen=True, slots=True)
class EnvironmentAuthorization:
    environment_id: str
    provider_id: str
    enabled: bool


async def read_environment_authorization(
    session: AsyncSession,
    *,
    environment_id: str,
    organization_id: str,
    workspace_id: str,
    previous: EnvironmentAuthorization | None,
) -> EnvironmentAuthorization:
    """Retain the fixed binding; refresh Provider and Device eligibility."""
    row = await session.get(EnvironmentRecord, environment_id)
    if row is None or (row.organization_id, row.workspace_id) != (organization_id, workspace_id):
        raise AuthorizationError("environment_not_found", concealed=True)
    if previous is None:
        provider_id = row.provider_id
    else:
        if previous.environment_id != environment_id:
            raise ValueError("Attempt Environment binding cannot change")
        provider_id = previous.provider_id
    provider = await session.get(EnvironmentProviderRecord, provider_id)
    enabled = (
        provider is not None
        and provider.enabled
        and row.device_revoked_at is None
        and provider.organization_id == organization_id
        and provider.workspace_id in {None, workspace_id}
    )
    return EnvironmentAuthorization(environment_id, provider_id, enabled)
