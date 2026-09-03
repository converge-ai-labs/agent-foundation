"""Transactional Model Provider application service."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from time import monotonic
from typing import Protocol

from anyio import fail_after
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.iam.authorization import AuthenticatedActor, WorkspaceAction
from a13n_service.secrets.crypto import SecretProtector
from a13n_service.storage import transaction

from .credentials import ProviderCredentialError, replace_provider_credential, validate_provider_credential
from .cursors import CursorError, decode_model_cursor, encode_model_cursor
from .domain import (
    CreateModelProviderRequest,
    ModelConnectionTestResult,
    ModelProvider,
    ModelProviderCollection,
    UpdateModelProviderRequest,
    new_model_provider_id,
)
from .endpoint_policy import EndpointPolicy, EndpointPolicyError
from .models import ModelProviderRecord
from .providers import (
    DiscoveredModelCollection,
    ModelProviderTypeDefinitionCollection,
    ProviderRegistry,
    ValidatedProviderConfig,
)
from .service_common import ModelError, audit_record, authorize_models, require_etag


class ProviderOperations(Protocol):
    def test(self, *, provider_id: str, organization_id: str, workspace_id: str) -> Awaitable[None]: ...

    def discover(
        self, *, provider_id: str, organization_id: str, workspace_id: str
    ) -> Awaitable[DiscoveredModelCollection]: ...


class ModelProviderService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        registry: ProviderRegistry,
        endpoint_policy: EndpointPolicy,
        protector: SecretProtector,
        *,
        clock: Callable[[], datetime] | None = None,
        resolve_dns_on_save: bool = True,
        operations: ProviderOperations | None = None,
        command_timeout_seconds: float = 15,
    ) -> None:
        self._sessions = sessions
        self._registry = registry
        self._endpoint_policy = endpoint_policy
        self._protector = protector
        self._clock = clock or (lambda: datetime.now(UTC))
        self._resolve_dns_on_save = resolve_dns_on_save
        self._operations = operations
        self._command_timeout_seconds = command_timeout_seconds

    async def type_definitions(self, *, actor: AuthenticatedActor) -> ModelProviderTypeDefinitionCollection:
        async with transaction(self._sessions) as session:
            await authorize_models(
                session,
                actor=actor,
                workspace_id=actor.boundary_workspace_id,
                action=WorkspaceAction.models_read,
            )
        return ModelProviderTypeDefinitionCollection(items=self._registry.definitions())

    async def create(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        request: CreateModelProviderRequest,
    ) -> ModelProvider:
        credential = request.credential.get_secret_value() if request.credential is not None else None
        validated = await self._validate(request.type, request.config, credential_configured=credential is not None)
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
                    config=validated.config,
                    credential_version=0,
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
                replace_provider_credential(record, credential, self._protector)
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
            raise ModelError(
                "model_provider_name_conflict",
                "A Model Provider with this name already exists in the Workspace.",
                status_code=409,
            ) from error
        except ProviderCredentialError as error:
            raise ModelError("invalid_provider_credential", str(error), status_code=400) from error

    async def get(self, *, actor: AuthenticatedActor, workspace_id: str, provider_id: str) -> ModelProvider:
        async with transaction(self._sessions) as session:
            workspace = await authorize_models(
                session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.models_read
            )
            return (
                await require_provider(
                    session,
                    organization_id=workspace.organization_id,
                    workspace_id=workspace.workspace_id,
                    provider_id=provider_id,
                )
            ).to_resource()

    async def list(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        limit: int = 50,
        cursor: str | None = None,
        name: str | None = None,
        provider_type: str | None = None,
        enabled: bool | None = None,
    ) -> ModelProviderCollection:
        if not 1 <= limit <= 100:
            raise ModelError("invalid_request", "limit must be between 1 and 100.", status_code=400)
        if name is not None and (not name.strip() or len(name) > 128):
            raise ModelError("invalid_request", "name search is invalid.", status_code=400)
        if provider_type is not None:
            try:
                self._registry.definition(provider_type)
            except ValueError as error:
                raise ModelError("invalid_request", "provider_type is not trusted.", status_code=400) from error
        scope = {
            "workspace_id": workspace_id,
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
            raise ModelError("invalid_cursor", "The collection cursor is invalid.", status_code=400) from error
        async with transaction(self._sessions) as session:
            workspace = await authorize_models(
                session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.models_read
            )
            query = select(ModelProviderRecord).where(
                ModelProviderRecord.organization_id == workspace.organization_id,
                ModelProviderRecord.workspace_id == workspace.workspace_id,
            )
            if name is not None:
                query = query.where(ModelProviderRecord.name.ilike(f"%{_escape_like(name.strip())}%", escape="\\"))
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
            next_cursor = encode_model_cursor(updated_at=page[-1].updated_at, model_id=page[-1].id, scope=scope)
        return ModelProviderCollection(items=tuple(item.to_resource() for item in page), next_cursor=next_cursor)

    async def update(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        provider_id: str,
        if_match: str,
        request: UpdateModelProviderRequest,
    ) -> ModelProvider:
        current = await self.get(actor=actor, workspace_id=workspace_id, provider_id=provider_id)
        require_etag(current.id, current.updated_at, if_match)
        credential = request.credential.get_secret_value() if request.credential is not None else None
        if "credential" in request.model_fields_set:
            self._validate_credential(current.type, credential)
        credential_configured = (
            credential is not None if "credential" in request.model_fields_set else current.credential_configured
        )
        config = request.config if request.config is not None else current.config
        validated = await self._validate(current.type, config, credential_configured=credential_configured)
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
            record.config = validated.config
            if "credential" in request.model_fields_set:
                try:
                    replace_provider_credential(record, credential, self._protector)
                except ProviderCredentialError as error:
                    raise ModelError("invalid_provider_credential", str(error), status_code=400) from error
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
                raise ModelError(
                    "model_provider_name_conflict",
                    "A Model Provider with this name already exists in the Workspace.",
                    status_code=409,
                ) from error
            return record.to_resource()

    async def discover_models(
        self, *, actor: AuthenticatedActor, workspace_id: str, provider_id: str
    ) -> DiscoveredModelCollection:
        provider = await self._prepare_command(actor=actor, workspace_id=workspace_id, provider_id=provider_id)
        if not provider.enabled:
            raise ModelError("model_provider_disabled", "The Model Provider is disabled.", status_code=409)
        if not self._registry.definition(provider.type).supports_model_discovery:
            raise ModelError(
                "provider_discovery_unsupported",
                "The Model Provider type does not support model discovery.",
                status_code=409,
            )
        if self._operations is None:
            raise ModelError(
                "provider_discovery_unavailable", "Provider model discovery is unavailable.", status_code=503
            )
        failure: ModelError | None = None
        result: DiscoveredModelCollection | None = None
        try:
            with fail_after(self._command_timeout_seconds):
                result = await self._operations.discover(
                    provider_id=provider.id,
                    organization_id=provider.organization_id,
                    workspace_id=provider.workspace_id,
                )
        except TimeoutError:
            failure = ModelError("provider_discovery_timeout", "Provider model discovery timed out.", status_code=504)
        except Exception:
            failure = ModelError("provider_discovery_failed", "Provider model discovery failed.", status_code=502)
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

    async def test(
        self, *, actor: AuthenticatedActor, workspace_id: str, provider_id: str
    ) -> ModelConnectionTestResult:
        if self._operations is None:
            raise ModelError(
                "provider_connection_tester_unavailable", "Provider testing is unavailable.", status_code=503
            )
        provider = await self._prepare_command(actor=actor, workspace_id=workspace_id, provider_id=provider_id)
        started = monotonic()
        success, code, message = True, "connection_succeeded", "The Provider connection succeeded."
        try:
            with fail_after(self._command_timeout_seconds):
                await self._operations.test(
                    provider_id=provider.id,
                    organization_id=provider.organization_id,
                    workspace_id=provider.workspace_id,
                )
        except TimeoutError:
            success, code, message = False, "connection_timeout", "The Provider connection timed out."
        except Exception:
            success, code, message = False, "connection_failed", "The Provider connection failed."
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
                    outcome="success" if success else "failure",
                )
            )
        return ModelConnectionTestResult(
            success=success,
            elapsed_ms=max(0, round((monotonic() - started) * 1000)),
            code=code,
            message=message,
        )

    async def _prepare_command(
        self, *, actor: AuthenticatedActor, workspace_id: str, provider_id: str
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
        self, provider_type: str, config: dict[str, object], *, credential_configured: bool
    ) -> ValidatedProviderConfig:
        try:
            validated = self._registry.validate_provider(
                provider_type, config, credential_configured=credential_configured
            )
            if validated.endpoint is None:
                return validated
            endpoint = await self._endpoint_policy.validate(
                validated.endpoint,
                resolve_dns=self._resolve_dns_on_save,
            )
        except (ValueError, EndpointPolicyError) as error:
            raise ModelError("invalid_model_provider", "The Model Provider is invalid.", status_code=400) from error
        return self._registry.with_validated_endpoint(provider_type, validated, endpoint)

    def _validate_credential(self, provider_type: str, credential: str | None) -> None:
        try:
            validate_provider_credential(self._registry.credential_format(provider_type), credential)
        except (ValueError, ProviderCredentialError) as error:
            raise ModelError("invalid_provider_credential", str(error), status_code=400) from error


async def require_provider(
    session: AsyncSession,
    *,
    organization_id: str,
    workspace_id: str,
    provider_id: str,
    lock: bool = False,
) -> ModelProviderRecord:
    query = select(ModelProviderRecord).where(
        ModelProviderRecord.organization_id == organization_id,
        ModelProviderRecord.workspace_id == workspace_id,
        ModelProviderRecord.id == provider_id,
    )
    if lock:
        query = query.with_for_update()
    record = await session.scalar(query)
    if record is None:
        raise ModelError("model_provider_not_found", "The Model Provider was not found.", status_code=404)
    return record


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
