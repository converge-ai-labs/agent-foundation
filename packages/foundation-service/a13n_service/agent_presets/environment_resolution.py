"""Two-phase Environment selection for AgentPreset Revision creation and invocation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from a13n_environment_provider import EnvironmentProviderError, EnvironmentProviderSpec
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.environments.catalog import FoundationEnvironmentProviderCatalog
from a13n_service.environments.domain import (
    EnvironmentAccess,
    EnvironmentCredentialBinding,
    EnvironmentProviderLock,
    environment_logical_digest,
)
from a13n_service.environments.errors import (
    EnvironmentManagementError,
    environment_provider_disabled,
    environment_revision_not_found,
)
from a13n_service.environments.models import EnvironmentRecord, EnvironmentRevisionRecord
from a13n_service.environments.service import _authorize, _require_credential_bindings, _require_selection
from a13n_service.iam import AuthenticatedActor
from a13n_service.iam.authorization import WorkspaceAction
from a13n_service.secrets.domain import InvokingUserSecretCredential
from a13n_service.secrets.models import SecretRecord
from a13n_service.storage import short_session

from .domain import (
    EnvironmentCredentialBinding as AgentEnvironmentCredentialBinding,
)
from .domain import (
    EnvironmentExecutionConfig,
    EnvironmentOverride,
    EnvironmentSelection,
    InlineEnvironmentSelection,
)


class EnvironmentResolutionPurpose(StrEnum):
    create_revision = "create_revision"
    invoke = "invoke"


@dataclass(frozen=True, slots=True)
class PreparedEnvironmentSelection:
    actor: AuthenticatedActor
    organization_id: str
    workspace_id: str
    purpose: EnvironmentResolutionPurpose
    source_environment_id: str | None
    source_environment_revision_id: str | None
    source_revision_digest: str | None
    selection_version: int
    resolved: EnvironmentExecutionConfig


class AgentEnvironmentSelectionResolver:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        catalog: FoundationEnvironmentProviderCatalog,
    ) -> None:
        self._sessions = sessions
        self._catalog = catalog

    async def prepare_revision_creation(
        self,
        *,
        actor: AuthenticatedActor,
        organization_id: str,
        workspace_id: str,
        selection: EnvironmentSelection,
    ) -> PreparedEnvironmentSelection:
        return await self._prepare_named(
            actor=actor,
            organization_id=organization_id,
            workspace_id=workspace_id,
            purpose=EnvironmentResolutionPurpose.create_revision,
            selection=selection,
        )

    async def prepare_invocation(
        self,
        *,
        actor: AuthenticatedActor,
        organization_id: str,
        workspace_id: str,
        selection: EnvironmentOverride,
        retained: EnvironmentExecutionConfig | None,
    ) -> PreparedEnvironmentSelection:
        if isinstance(selection, EnvironmentSelection):
            prepared = await self._prepare_named(
                actor=actor,
                organization_id=organization_id,
                workspace_id=workspace_id,
                purpose=EnvironmentResolutionPurpose.invoke,
                selection=selection,
            )
        else:
            prepared = await self._prepare_inline(
                actor=actor,
                organization_id=organization_id,
                workspace_id=workspace_id,
                selection=selection,
            )
        if retained is not None and retained.source_environment_revision_id == prepared.source_environment_revision_id:
            if retained != prepared.resolved:
                raise EnvironmentManagementError(
                    "environment_revision_changed",
                    "The retained Environment Revision no longer matches its frozen execution configuration.",
                    status_code=409,
                )
        return prepared

    async def freeze_in_transaction(
        self,
        session: AsyncSession,
        *,
        prepared: PreparedEnvironmentSelection,
    ) -> EnvironmentExecutionConfig:
        await _authorize(
            session,
            actor=prepared.actor,
            workspace_id=prepared.workspace_id,
            action=WorkspaceAction.environment_use,
        )
        lock = EnvironmentProviderLock.model_validate(prepared.resolved.provider_lock)
        entry = self._catalog.entry(prepared.resolved.provider.provider_key)
        if entry.provider_lock != lock:
            raise EnvironmentManagementError(
                "environment_provider_lock_changed",
                "The Environment Provider lock changed during acceptance.",
                status_code=409,
            )
        selection = await _require_selection(
            session,
            organization_id=prepared.organization_id,
            workspace_id=prepared.workspace_id,
            provider_key=prepared.resolved.provider.provider_key,
            expected_lock=lock.model_dump(mode="json"),
            for_update=True,
        )
        if selection.version != prepared.selection_version:
            raise EnvironmentManagementError(
                "environment_provider_selection_changed",
                "The Environment Provider selection changed during acceptance.",
                status_code=409,
            )
        if selection.provider_package_revision_id != prepared.resolved.provider_package_revision_id:
            raise EnvironmentManagementError(
                "environment_provider_lock_changed",
                "The Environment Provider package changed during acceptance.",
                status_code=409,
            )
        await _require_credential_bindings(
            session,
            actor=prepared.actor,
            organization_id=prepared.organization_id,
            workspace_id=prepared.workspace_id,
            bindings=tuple(
                EnvironmentCredentialBinding.model_validate(item.model_dump(mode="json"))
                for item in prepared.resolved.credential_bindings
            ),
            require_bind_authority=False,
        )
        if prepared.purpose is EnvironmentResolutionPurpose.invoke:
            await _require_invoking_user_credentials(
                session,
                actor=prepared.actor,
                organization_id=prepared.organization_id,
                workspace_id=prepared.workspace_id,
                bindings=prepared.resolved.credential_bindings,
            )
        if prepared.source_environment_revision_id is not None:
            environment = await session.scalar(
                select(EnvironmentRecord)
                .where(
                    EnvironmentRecord.id == prepared.source_environment_id,
                    EnvironmentRecord.organization_id == prepared.organization_id,
                    EnvironmentRecord.workspace_id == prepared.workspace_id,
                )
                .with_for_update()
            )
            revision = await session.scalar(
                select(EnvironmentRevisionRecord)
                .where(
                    EnvironmentRevisionRecord.id == prepared.source_environment_revision_id,
                    EnvironmentRevisionRecord.environment_id == prepared.source_environment_id,
                    EnvironmentRevisionRecord.organization_id == prepared.organization_id,
                    EnvironmentRevisionRecord.workspace_id == prepared.workspace_id,
                )
                .with_for_update()
            )
            if (
                environment is None
                or environment.archived_at is not None
                or revision is None
                or revision.logical_digest_sha256 != prepared.source_revision_digest
                or revision.to_resource().provider_lock != lock
            ):
                raise EnvironmentManagementError(
                    "environment_revision_changed",
                    "The Environment Revision changed during acceptance.",
                    status_code=409,
                )
        self._validate_provider(prepared.resolved.provider)
        return prepared.resolved

    async def _prepare_named(
        self,
        *,
        actor: AuthenticatedActor,
        organization_id: str,
        workspace_id: str,
        purpose: EnvironmentResolutionPurpose,
        selection: EnvironmentSelection,
    ) -> PreparedEnvironmentSelection:
        async with short_session(self._sessions) as session:
            await _authorize(
                session,
                actor=actor,
                workspace_id=workspace_id,
                action=WorkspaceAction.environment_use,
            )
            revision = await session.scalar(
                select(EnvironmentRevisionRecord).where(
                    EnvironmentRevisionRecord.id == selection.environment_revision_id,
                    EnvironmentRevisionRecord.organization_id == organization_id,
                    EnvironmentRevisionRecord.workspace_id == workspace_id,
                )
            )
            if revision is None:
                raise environment_revision_not_found()
            environment = await session.scalar(
                select(EnvironmentRecord).where(
                    EnvironmentRecord.id == revision.environment_id,
                    EnvironmentRecord.organization_id == organization_id,
                    EnvironmentRecord.workspace_id == workspace_id,
                )
            )
            if environment is None or environment.archived_at is not None:
                raise EnvironmentManagementError(
                    "environment_archived",
                    "The selected Environment is archived.",
                    status_code=409,
                )
            resource = revision.to_resource()
            lock = resource.provider_lock
            catalog_entry = self._catalog.entry(resource.provider.provider_key)
            if catalog_entry.provider_lock != lock:
                raise EnvironmentManagementError(
                    "environment_provider_lock_changed",
                    "The selected Environment Provider lock is unavailable.",
                    status_code=409,
                )
            provider_selection = await _require_selection(
                session,
                organization_id=organization_id,
                workspace_id=workspace_id,
                provider_key=resource.provider.provider_key,
                expected_lock=lock.model_dump(mode="json"),
                for_update=False,
            )
            if provider_selection.provider_package_revision_id != resource.provider_package_revision_id:
                raise environment_provider_disabled()
            await _require_credential_bindings(
                session,
                actor=actor,
                organization_id=organization_id,
                workspace_id=workspace_id,
                bindings=resource.credential_bindings,
                require_bind_authority=False,
            )
            if purpose is EnvironmentResolutionPurpose.invoke:
                await _require_invoking_user_credentials(
                    session,
                    actor=actor,
                    organization_id=organization_id,
                    workspace_id=workspace_id,
                    bindings=resource.credential_bindings,
                )
        provider = self._validate_provider(resource.provider)
        resolved = EnvironmentExecutionConfig(
            source_environment_revision_id=resource.id,
            provider=provider,
            provider_package_revision_id=resource.provider_package_revision_id,
            provider_lock=lock.model_dump(mode="json"),
            credential_bindings=tuple(
                AgentEnvironmentCredentialBinding.model_validate(item.model_dump(mode="json"))
                for item in resource.credential_bindings
            ),
            access=resource.access.value,
            logical_digest_sha256=resource.logical_digest_sha256,
        )
        return PreparedEnvironmentSelection(
            actor=actor,
            organization_id=organization_id,
            workspace_id=workspace_id,
            purpose=purpose,
            source_environment_id=resource.environment_id,
            source_environment_revision_id=resource.id,
            source_revision_digest=resource.logical_digest_sha256,
            selection_version=provider_selection.version,
            resolved=resolved,
        )

    async def _prepare_inline(
        self,
        *,
        actor: AuthenticatedActor,
        organization_id: str,
        workspace_id: str,
        selection: InlineEnvironmentSelection,
    ) -> PreparedEnvironmentSelection:
        entry = self._catalog.entry(selection.provider.provider_key)
        provider = self._validate_provider(selection.provider)
        bindings = tuple(
            EnvironmentCredentialBinding.model_validate(item.model_dump(mode="json"))
            for item in selection.credential_bindings
        )
        async with short_session(self._sessions) as session:
            await _authorize(
                session,
                actor=actor,
                workspace_id=workspace_id,
                action=WorkspaceAction.environment_use,
            )
            provider_selection = await _require_selection(
                session,
                organization_id=organization_id,
                workspace_id=workspace_id,
                provider_key=provider.provider_key,
                expected_lock=entry.provider_lock.model_dump(mode="json"),
                for_update=False,
            )
            await _require_credential_bindings(
                session,
                actor=actor,
                organization_id=organization_id,
                workspace_id=workspace_id,
                bindings=bindings,
                require_bind_authority=False,
            )
            await _require_invoking_user_credentials(
                session,
                actor=actor,
                organization_id=organization_id,
                workspace_id=workspace_id,
                bindings=selection.credential_bindings,
            )
        access = EnvironmentAccess(selection.access)
        digest = environment_logical_digest(
            provider=provider,
            provider_package_revision_id=provider_selection.provider_package_revision_id,
            provider_lock=entry.provider_lock,
            credential_bindings=bindings,
            access=access,
        )
        resolved = EnvironmentExecutionConfig(
            source_environment_revision_id=None,
            provider=provider,
            provider_package_revision_id=provider_selection.provider_package_revision_id,
            provider_lock=entry.provider_lock.model_dump(mode="json"),
            credential_bindings=tuple(
                AgentEnvironmentCredentialBinding.model_validate(item.model_dump(mode="json")) for item in bindings
            ),
            access=access.value,
            logical_digest_sha256=digest,
        )
        return PreparedEnvironmentSelection(
            actor=actor,
            organization_id=organization_id,
            workspace_id=workspace_id,
            purpose=EnvironmentResolutionPurpose.invoke,
            source_environment_id=None,
            source_environment_revision_id=None,
            source_revision_digest=None,
            selection_version=provider_selection.version,
            resolved=resolved,
        )

    def _validate_provider(self, spec: EnvironmentProviderSpec) -> EnvironmentProviderSpec:
        entry = self._catalog.entry(spec.provider_key)
        if spec.schema_version not in entry.configuration_versions:
            raise EnvironmentManagementError(
                "provider_schema_unsupported",
                "The Environment Provider configuration version is unsupported.",
                status_code=400,
            )
        try:
            configuration = self._catalog.providers[spec.provider_key].validate_configuration(
                schema_version=spec.schema_version,
                value=spec.configuration,
            )
        except EnvironmentProviderError as error:
            safe = error.safe_projection()
            raise EnvironmentManagementError(safe.code, safe.message, status_code=400) from error
        return EnvironmentProviderSpec(
            provider_key=spec.provider_key,
            schema_version=spec.schema_version,
            configuration=configuration.model_dump(mode="json"),
        )


async def _require_invoking_user_credentials(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    organization_id: str,
    workspace_id: str,
    bindings,
) -> None:
    invoking = tuple(item.credential for item in bindings if isinstance(item.credential, InvokingUserSecretCredential))
    if not invoking:
        return
    if actor.principal.principal_type.value != "user":
        raise EnvironmentManagementError(
            "environment_credential_unavailable",
            "An invoking User Environment credential is unavailable.",
            status_code=409,
        )
    for credential in invoking:
        available = await session.scalar(
            select(SecretRecord.id).where(
                SecretRecord.organization_id == organization_id,
                SecretRecord.workspace_id == workspace_id,
                SecretRecord.owner_type == "user",
                SecretRecord.owner_id == actor.principal.principal_id,
                SecretRecord.key == credential.secret_key,
                SecretRecord.deleted_at.is_(None),
                SecretRecord.ciphertext.is_not(None),
            )
        )
        if available is None:
            raise EnvironmentManagementError(
                "environment_credential_unavailable",
                "An invoking User Environment credential is unavailable.",
                status_code=409,
            )
