"""Resolve current Model Provider state for management and execution paths."""

from __future__ import annotations

from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Protocol

from a13n_harness.errors import ModelResolutionError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.credentials import CredentialSnapshot
from a13n_service.endpoint_policy import EndpointPolicyError
from a13n_service.iam.resource_scope import visible_workspace
from a13n_service.secrets.crypto import SecretProtectionError, SecretProtector
from a13n_service.storage import short_session

from .credentials import ProviderSecrets
from .domain import ModelExecutionSnapshot
from .models import ModelProviderRecord, ModelRecord
from .provider_adapters.types import RuntimeProvider
from .providers import ProviderRegistry


@dataclass(frozen=True, slots=True)
class _StoredProvider:
    id: str
    type: str
    configuration: dict[str, object]
    enabled: bool
    credential: CredentialSnapshot | None
    credential_configured: bool
    header_names: tuple[str, ...]


class EndpointValidator(Protocol):
    def validate(self, endpoint: str, *, resolve_dns: bool) -> Awaitable[str]: ...


class LiveProviderResolver:
    """Read and decrypt current Provider state without holding a session across I/O."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        registry: ProviderRegistry,
        endpoint_policy: EndpointValidator,
        protector: SecretProtector,
    ) -> None:
        self._sessions = sessions
        self._registry = registry
        self._endpoint_policy = endpoint_policy
        self._protector = protector

    async def resolve(
        self,
        *,
        organization_id: str,
        workspace_id: str | None,
        snapshot: ModelExecutionSnapshot,
    ) -> RuntimeProvider:
        async with short_session(self._sessions) as session:
            row = (
                await session.execute(
                    select(ModelRecord, ModelProviderRecord)
                    .join(ModelProviderRecord, ModelProviderRecord.id == ModelRecord.provider_id)
                    .where(
                        ModelRecord.organization_id == organization_id,
                        visible_workspace(ModelRecord.workspace_id, workspace_id),
                        ModelRecord.id == snapshot.model_id,
                    )
                )
            ).one_or_none()
            if row is None:
                raise ModelResolutionError("The accepted Model is unavailable.", code="model_unavailable")
            model, provider = row
            if not model.enabled:
                raise ModelResolutionError("The accepted Model is disabled.", code="model_unavailable")
            stored = _stored_provider(provider)
        return await self._materialize(stored, model_api=snapshot.model_api)

    async def resolve_provider(
        self,
        *,
        organization_id: str,
        workspace_id: str | None,
        provider_id: str,
    ) -> RuntimeProvider:
        async with short_session(self._sessions) as session:
            provider = await session.scalar(
                select(ModelProviderRecord).where(
                    ModelProviderRecord.organization_id == organization_id,
                    visible_workspace(ModelProviderRecord.workspace_id, workspace_id),
                    ModelProviderRecord.id == provider_id,
                )
            )
            if provider is None:
                raise ModelResolutionError("The Model Provider is unavailable.", code="model_provider_unavailable")
            stored = _stored_provider(provider)
        return await self._materialize(stored)

    async def _materialize(self, provider: _StoredProvider, *, model_api: str | None = None) -> RuntimeProvider:
        try:
            if not provider.enabled:
                raise ValueError("the Model Provider is disabled")
            if model_api is not None:
                self._registry.validate_model_api(provider.type, model_api)
            validated = self._registry.validate_provider(
                provider.type,
                provider.configuration,
                credential_configured=provider.credential_configured,
                header_names=provider.header_names,
            )
            if validated.endpoint is not None:
                await self._endpoint_policy.validate(validated.endpoint, resolve_dns=True)
            for field in self._registry.integration(provider.type).additional_endpoint_fields:
                endpoint = validated.configuration.get(field)
                if isinstance(endpoint, str):
                    await self._endpoint_policy.validate(endpoint, resolve_dns=True)
            secrets = (
                ProviderSecrets.model_validate_json(provider.credential.decrypt(self._protector))
                if provider.credential is not None
                else ProviderSecrets()
            )
        except (ValueError, EndpointPolicyError, SecretProtectionError) as error:
            raise ModelResolutionError(
                "The current Model Provider configuration is unavailable.",
                code="model_provider_unavailable",
                details={"provider_id": provider.id},
            ) from error
        return RuntimeProvider(
            type=provider.type,
            configuration=validated.configuration,
            endpoint=validated.endpoint,
            credential=secrets.credential,
            extra_headers=secrets.extra_headers,
        )


def _stored_provider(provider: ModelProviderRecord) -> _StoredProvider:
    return _StoredProvider(
        id=provider.id,
        type=provider.type,
        configuration=dict(provider.configuration),
        enabled=provider.enabled,
        credential=provider.credential_snapshot() if provider.ciphertext is not None else None,
        credential_configured=provider.credential_configured,
        header_names=tuple(provider.header_names),
    )
