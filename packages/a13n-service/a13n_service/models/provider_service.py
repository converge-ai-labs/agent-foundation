"""Transactional Model Provider application service."""

from __future__ import annotations

from collections.abc import Awaitable
from typing import Protocol

import httpx2
from a13n_harness.errors import ModelResolutionError
from anyio import fail_after
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.application_errors import ErrorCategory
from a13n_service.credentials import CredentialSnapshot
from a13n_service.endpoint_policy import EndpointPolicy, EndpointPolicyError
from a13n_service.iam.authorization import AuthenticatedActor, WorkspaceAction
from a13n_service.iam.resource_scope import visible_workspace
from a13n_service.secrets.crypto import SecretProtectionError, SecretProtector
from a13n_service.storage import is_unique_conflict, transaction
from a13n_service.temporal import Clock, utc_now

from .connection_test import test_connection
from .credentials import ProviderCredentialError, ProviderSecrets, validate_provider_credential
from .cursors import CursorError, decode_model_cursor, encode_model_cursor
from .descriptions import describe_model
from .domain import (
    CreateModelProviderRequest,
    ModelConnectionTestResult,
    ModelDescription,
    ModelDiscovery,
    ModelProvider,
    ModelProviderCollection,
    UpdateModelProviderRequest,
    new_model_provider_id,
)
from .headers import apply_header_updates
from .models import ModelProviderRecord
from .provider_adapters.base import ProviderOperationError
from .providers import (
    DescribeModelRequest,
    ModelProviderDefinitionCollection,
    ProviderRegistry,
    ValidatedProviderConfiguration,
)
from .service_common import ModelError, audit_record, authorize_models, escape_like, require_etag


class ProviderOperations(Protocol):
    def test(self, *, provider_id: str, organization_id: str, workspace_id: str | None) -> Awaitable[None]: ...

    def discover(
        self, *, provider_id: str, organization_id: str, workspace_id: str | None
    ) -> Awaitable[ModelDiscovery]: ...

    def describe(
        self,
        *,
        provider_id: str,
        organization_id: str,
        workspace_id: str | None,
        provider_type: str,
        upstream_model: str,
        model_api: str | None,
    ) -> Awaitable[ModelDescription]: ...


class ModelProviderService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        registry: ProviderRegistry,
        endpoint_policy: EndpointPolicy,
        protector: SecretProtector,
        *,
        clock: Clock | None = None,
        resolve_dns_on_save: bool = True,
        operations: ProviderOperations | None = None,
        command_timeout_seconds: float = 15,
    ) -> None:
        self._sessions = sessions
        self._registry = registry
        self._endpoint_policy = endpoint_policy
        self._protector = protector
        self._clock = clock or utc_now
        self._resolve_dns_on_save = resolve_dns_on_save
        self._operations = operations
        self._command_timeout_seconds = command_timeout_seconds

    async def type_definitions(self, *, actor: AuthenticatedActor) -> ModelProviderDefinitionCollection:
        async with transaction(self._sessions) as session:
            await authorize_models(
                session,
                actor=actor,
                workspace_id=actor.boundary_workspace_id,
                action=WorkspaceAction.models_read,
            )
        return ModelProviderDefinitionCollection(items=self._registry.definitions())

    async def create(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str | None,
        request: CreateModelProviderRequest,
    ) -> ModelProvider:
        credential = request.credential.get_secret_value() if request.credential is not None else None
        secrets = ProviderSecrets(
            credential=credential,
            extra_headers={
                name: value.get_secret_value() for name, value in request.extra_headers.items() if value is not None
            },
        )
        validated = await self._validate(
            request.type,
            request.configuration,
            credential_configured=credential is not None,
            header_names=tuple(secrets.extra_headers),
        )
        self._validate_credential(request.type, credential)
        now = self._clock()
        try:
            async with transaction(self._sessions) as session:
                workspace = await authorize_models(
                    session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.models_manage
                )
                record = ModelProviderRecord(
                    id=new_model_provider_id(),
                    organization_id=workspace.organization_id,
                    workspace_id=workspace.workspace_id,
                    type=request.type,
                    name=request.name,
                    normalized_name=request.name.casefold(),
                    configuration=validated.configuration,
                    credential_generation=0,
                    ciphertext=None,
                    nonce=None,
                    encryption_key_id=None,
                    enabled=request.enabled,
                    created_by_type=actor.principal.principal_type.value,
                    created_by_id=actor.principal.principal_id,
                    updated_by_type=actor.principal.principal_type.value,
                    updated_by_id=actor.principal.principal_id,
                    created_at=now,
                    updated_at=now,
                )
                record.replace_secrets(secrets, self._protector)
                session.add(record)
                session.add(
                    audit_record(
                        actor=actor,
                        organization_id=workspace.organization_id,
                        workspace_id=workspace.workspace_id,
                        resource_type="model_provider",
                        resource_id=record.id,
                        action="model_provider.create",
                        now=now,
                    )
                )
                await session.flush()
                return record.to_resource()
        except IntegrityError as error:
            if not (
                is_unique_conflict(error, constraint="uq_model_providers_workspace_name")
                or is_unique_conflict(error, constraint="uq_model_providers_organization_normalized_name")
            ):
                raise
            raise ModelError(
                "model_provider_name_conflict",
                "A Model Provider with this name already exists in the Workspace.",
                category=ErrorCategory.conflict,
            ) from error
        except SecretProtectionError as error:
            raise ModelError(
                "invalid_provider_credential", str(error), category=ErrorCategory.invalid_request
            ) from error

    async def get(self, *, actor: AuthenticatedActor, workspace_id: str | None, provider_id: str) -> ModelProvider:
        resource, _ = await self._load(actor=actor, workspace_id=workspace_id, provider_id=provider_id)
        return resource

    async def _load(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str | None,
        provider_id: str,
        action: WorkspaceAction = WorkspaceAction.models_read,
    ) -> tuple[ModelProvider, CredentialSnapshot | None]:
        async with transaction(self._sessions) as session:
            workspace = await authorize_models(session, actor=actor, workspace_id=workspace_id, action=action)
            record = await require_provider(
                session,
                organization_id=workspace.organization_id,
                workspace_id=workspace.workspace_id,
                provider_id=provider_id,
                # Mutation snapshots verify ownership before any credential decryption.
                lock=action is WorkspaceAction.models_manage,
            )
            return record.to_resource(), (record.credential_snapshot() if record.ciphertext is not None else None)

    async def list(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str | None,
        limit: int = 50,
        cursor: str | None = None,
        name: str | None = None,
        provider_type: str | None = None,
        enabled: bool | None = None,
    ) -> ModelProviderCollection:
        if not 1 <= limit <= 100:
            raise ModelError(
                "invalid_request", "limit must be between 1 and 100.", category=ErrorCategory.invalid_request
            )
        if name is not None and (not name.strip() or len(name) > 128):
            raise ModelError("invalid_request", "name search is invalid.", category=ErrorCategory.invalid_request)
        if provider_type is not None:
            try:
                self._registry.definition(provider_type)
            except ValueError as error:
                raise ModelError(
                    "invalid_request", "provider_type is not trusted.", category=ErrorCategory.invalid_request
                ) from error
        scope = {
            "workspace_id": workspace_id,
            "organization_boundary": actor.boundary_organization_id,
            "principal_type": actor.principal.principal_type.value,
            "principal_id": actor.principal.principal_id,
            "name": name,
            "provider_type": provider_type,
            "enabled": enabled,
            "resource": "model_provider",
        }
        try:
            position = decode_model_cursor(cursor, scope=scope) if cursor is not None else None
        except CursorError as error:
            raise ModelError(
                "invalid_cursor", "The collection cursor is invalid.", category=ErrorCategory.invalid_request
            ) from error
        async with transaction(self._sessions) as session:
            workspace = await authorize_models(
                session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.models_read
            )
            query = select(ModelProviderRecord).where(
                ModelProviderRecord.organization_id == workspace.organization_id,
                visible_workspace(ModelProviderRecord.workspace_id, workspace.workspace_id),
            )
            if name is not None:
                query = query.where(ModelProviderRecord.name.ilike(f"%{escape_like(name.strip())}%", escape="\\"))
            if provider_type is not None:
                query = query.where(ModelProviderRecord.type == provider_type)
            if enabled is not None:
                query = query.where(ModelProviderRecord.enabled == enabled)
            if position is not None:
                updated_at, item_id = position
                query = query.where(
                    or_(
                        ModelProviderRecord.updated_at < updated_at,
                        and_(ModelProviderRecord.updated_at == updated_at, ModelProviderRecord.id < item_id),
                    )
                )
            records = tuple(
                (
                    await session.scalars(
                        query.order_by(ModelProviderRecord.updated_at.desc(), ModelProviderRecord.id.desc()).limit(
                            limit + 1
                        )
                    )
                ).all()
            )
        page = records[:limit]
        next_cursor = None
        if len(records) > limit and page:
            next_cursor = encode_model_cursor(updated_at=page[-1].updated_at, item_id=page[-1].id, scope=scope)
        return ModelProviderCollection(items=tuple(item.to_resource() for item in page), next_cursor=next_cursor)

    async def update(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str | None,
        provider_id: str,
        if_match: str,
        request: UpdateModelProviderRequest,
    ) -> ModelProvider:
        current, encrypted = await self._load(
            actor=actor, workspace_id=workspace_id, provider_id=provider_id, action=WorkspaceAction.models_manage
        )
        require_etag(current.id, current.updated_at, if_match)
        credential = request.credential.get_secret_value() if request.credential is not None else None
        if "credential" in request.model_fields_set:
            self._validate_credential(current.type, credential)
        credential_configured = (
            credential is not None if "credential" in request.model_fields_set else current.credential_configured
        )
        configuration = request.configuration if request.configuration is not None else current.configuration
        header_names = apply_header_updates(
            dict.fromkeys(current.header_names, True),
            {name: True if value is not None else None for name, value in request.extra_headers.items()},
        )
        validated = await self._validate(
            current.type,
            configuration,
            credential_configured=credential_configured,
            header_names=tuple(header_names),
        )
        secrets: ProviderSecrets | None = None
        if "credential" in request.model_fields_set or request.extra_headers:
            try:
                previous = (
                    ProviderSecrets.model_validate_json(encrypted.decrypt(self._protector))
                    if encrypted
                    else ProviderSecrets()
                )
                secrets = ProviderSecrets(
                    credential=(credential if "credential" in request.model_fields_set else previous.credential),
                    extra_headers=apply_header_updates(
                        previous.extra_headers,
                        {
                            name: (value.get_secret_value() if value is not None else None)
                            for name, value in request.extra_headers.items()
                        },
                    ),
                )
            except (ValueError, SecretProtectionError) as error:
                raise ModelError(
                    "invalid_provider_credential",
                    "The Provider credentials are unavailable.",
                    category=ErrorCategory.invalid_request,
                ) from error
        async with transaction(self._sessions) as session:
            workspace = await authorize_models(
                session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.models_manage
            )
            record = await require_provider(
                session,
                organization_id=workspace.organization_id,
                workspace_id=workspace.workspace_id,
                provider_id=provider_id,
                lock=True,
            )
            require_etag(record.id, record.updated_at, if_match)
            if "name" in request.model_fields_set:
                assert request.name is not None
                record.name = request.name
                record.normalized_name = request.name.casefold()
            record.configuration = validated.configuration
            if secrets is not None:
                try:
                    record.replace_secrets(secrets, self._protector)
                except SecretProtectionError as error:
                    raise ModelError(
                        "invalid_provider_credential", str(error), category=ErrorCategory.invalid_request
                    ) from error
            if "enabled" in request.model_fields_set:
                assert request.enabled is not None
                record.enabled = request.enabled
            record.updated_by_type = actor.principal.principal_type.value
            record.updated_by_id = actor.principal.principal_id
            record.updated_at = self._clock()
            session.add(
                audit_record(
                    actor=actor,
                    organization_id=workspace.organization_id,
                    workspace_id=workspace.workspace_id,
                    resource_type="model_provider",
                    resource_id=record.id,
                    action="model_provider.update",
                    now=record.updated_at,
                )
            )
            try:
                await session.flush()
            except IntegrityError as error:
                if not (
                    is_unique_conflict(error, constraint="uq_model_providers_workspace_name")
                    or is_unique_conflict(error, constraint="uq_model_providers_organization_normalized_name")
                ):
                    raise
                raise ModelError(
                    "model_provider_name_conflict",
                    "A Model Provider with this name already exists in the Workspace.",
                    category=ErrorCategory.conflict,
                ) from error
            return record.to_resource()

    async def discover_models(
        self, *, actor: AuthenticatedActor, workspace_id: str | None, provider_id: str
    ) -> ModelDiscovery:
        provider = await self._prepare_command(actor=actor, workspace_id=workspace_id, provider_id=provider_id)
        if not provider.enabled:
            raise ModelError(
                "model_provider_disabled", "The Model Provider is disabled.", category=ErrorCategory.conflict
            )
        if not self._registry.definition(provider.type).supports_model_discovery:
            raise ModelError(
                "model_discovery_unsupported",
                "The Model Provider type does not support model discovery.",
                category=ErrorCategory.conflict,
            )
        if self._operations is None:
            raise ModelError(
                "provider_discovery_unavailable",
                "Provider model discovery is unavailable.",
                category=ErrorCategory.unavailable,
            )
        failure: ModelError | None = None
        result: ModelDiscovery | None = None
        try:
            with fail_after(self._command_timeout_seconds):
                result = await self._operations.discover(
                    provider_id=provider.id,
                    organization_id=provider.organization_id,
                    workspace_id=provider.workspace_id,
                )
        except ModelError as error:
            failure = error
        except TimeoutError:
            failure = ModelError(
                "provider_discovery_timeout", "Provider model discovery timed out.", category=ErrorCategory.timeout
            )
        except (ModelResolutionError, ProviderOperationError, httpx2.HTTPError):
            failure = ModelError(
                "provider_discovery_failed",
                "Provider model discovery failed.",
                category=ErrorCategory.dependency_failure,
            )
        async with transaction(self._sessions) as session:
            session.add(
                audit_record(
                    actor=actor,
                    organization_id=provider.organization_id,
                    workspace_id=provider.workspace_id,
                    resource_type="model_provider",
                    resource_id=provider.id,
                    action="model_provider.discover_models",
                    now=self._clock(),
                    outcome="failure" if failure is not None else "success",
                )
            )
        if failure is not None:
            raise failure
        assert result is not None
        return result

    async def describe_model(
        self, *, actor: AuthenticatedActor, workspace_id: str | None, provider_id: str, request: DescribeModelRequest
    ) -> ModelDescription:
        provider = await self.get(actor=actor, workspace_id=workspace_id, provider_id=provider_id)
        result = describe_model(self._registry, provider.type, request.upstream_model, model_api=request.model_api)
        if self._operations is not None:
            try:
                with fail_after(self._command_timeout_seconds):
                    result = await self._operations.describe(
                        provider_id=provider.id,
                        organization_id=provider.organization_id,
                        workspace_id=provider.workspace_id,
                        provider_type=provider.type,
                        upstream_model=request.upstream_model,
                        model_api=request.model_api,
                    )
            except TimeoutError:
                pass
        async with transaction(self._sessions) as session:
            session.add(
                audit_record(
                    actor=actor,
                    organization_id=provider.organization_id,
                    workspace_id=provider.workspace_id,
                    resource_type="model_provider",
                    resource_id=provider.id,
                    action="model_provider.describe_model",
                    now=self._clock(),
                )
            )
        return result

    async def test(
        self, *, actor: AuthenticatedActor, workspace_id: str | None, provider_id: str
    ) -> ModelConnectionTestResult:
        if self._operations is None:
            raise ModelError(
                "provider_connection_tester_unavailable",
                "Provider testing is unavailable.",
                category=ErrorCategory.unavailable,
            )
        provider = await self._prepare_command(actor=actor, workspace_id=workspace_id, provider_id=provider_id)
        result = await test_connection(
            self._operations.test(
                provider_id=provider.id, organization_id=provider.organization_id, workspace_id=provider.workspace_id
            ),
            timeout_seconds=self._command_timeout_seconds,
            subject="Provider",
        )
        async with transaction(self._sessions) as session:
            session.add(
                audit_record(
                    actor=actor,
                    organization_id=provider.organization_id,
                    workspace_id=provider.workspace_id,
                    resource_type="model_provider",
                    resource_id=provider.id,
                    action="model_provider.test",
                    now=self._clock(),
                    outcome="success" if result.success else "failure",
                )
            )
        return result

    async def _prepare_command(
        self, *, actor: AuthenticatedActor, workspace_id: str | None, provider_id: str
    ) -> ModelProvider:
        async with transaction(self._sessions) as session:
            workspace = await authorize_models(
                session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.models_manage
            )
            return (
                await require_provider(
                    session,
                    organization_id=workspace.organization_id,
                    workspace_id=workspace.workspace_id,
                    provider_id=provider_id,
                )
            ).to_resource()

    async def _validate(
        self,
        provider_type: str,
        configuration: dict[str, object],
        *,
        credential_configured: bool,
        header_names: tuple[str, ...] = (),
    ) -> ValidatedProviderConfiguration:
        try:
            validated = self._registry.validate_provider(
                provider_type,
                configuration,
                credential_configured=credential_configured,
                header_names=header_names,
            )
            for field in self._registry.integration(provider_type).additional_endpoint_fields:
                override = validated.configuration.get(field)
                if isinstance(override, str):
                    validated.configuration[field] = await self._endpoint_policy.validate(
                        override, resolve_dns=self._resolve_dns_on_save
                    )
            if validated.endpoint is None:
                return validated
            endpoint = await self._endpoint_policy.validate(
                validated.endpoint,
                resolve_dns=self._resolve_dns_on_save,
            )
        except (ValueError, EndpointPolicyError) as error:
            raise ModelError(
                "invalid_model_provider", "The Model Provider is invalid.", category=ErrorCategory.invalid_request
            ) from error
        return self._registry.with_validated_endpoint(provider_type, validated, endpoint)

    def _validate_credential(self, provider_type: str, credential: str | None) -> None:
        try:
            validate_provider_credential(self._registry.credential_format(provider_type), credential)
        except (ValueError, ProviderCredentialError) as error:
            raise ModelError(
                "invalid_provider_credential", str(error), category=ErrorCategory.invalid_request
            ) from error


async def require_provider(
    session: AsyncSession,
    *,
    organization_id: str,
    workspace_id: str | None,
    provider_id: str,
    lock: bool = False,
) -> ModelProviderRecord:
    query = select(ModelProviderRecord).where(
        ModelProviderRecord.organization_id == organization_id,
        visible_workspace(ModelProviderRecord.workspace_id, workspace_id),
        ModelProviderRecord.id == provider_id,
    )
    if lock:
        query = query.where(ModelProviderRecord.workspace_id == workspace_id).with_for_update()
    record = await session.scalar(query)
    if record is None:
        raise ModelError(
            "model_provider_not_found", "The Model Provider was not found.", category=ErrorCategory.not_found
        )
    return record
