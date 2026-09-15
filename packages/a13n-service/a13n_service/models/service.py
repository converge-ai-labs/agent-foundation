"""Transactional Model application service."""

from __future__ import annotations

from collections.abc import Awaitable
from typing import Literal, Protocol

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.application_errors import ErrorCategory
from a13n_service.iam.authorization import AuthenticatedActor, WorkspaceAction
from a13n_service.iam.resource_scope import visible_workspace
from a13n_service.storage import is_unique_conflict, transaction
from a13n_service.temporal import Clock, utc_now

from .base_models import BaseModelDirectory, BaseModelResolution
from .catalog import ModelCatalog, merge_declarations
from .connection_test import test_connection
from .cursors import CursorError, decode_model_cursor, encode_model_cursor
from .domain import (
    BaseModelCandidateCollection,
    CreateModelRequest,
    Model,
    ModelCatalogMatch,
    ModelCatalogSuggestion,
    ModelCollection,
    ModelConnectionTestResult,
    ModelDeclarations,
    ModelExecutionSnapshot,
    UpdateModelRequest,
    new_model_id,
    normalize_key,
)
from .keys import require_available_key
from .models import ModelRecord
from .provider_service import require_provider
from .providers import ProviderRegistry
from .service_common import ModelError, audit_record, authorize_models, escape_like, require_etag
from .settings import JsonObject, validate_settings


class ModelConnectionTester(Protocol):
    def __call__(
        self,
        *,
        snapshot: ModelExecutionSnapshot,
        settings: JsonObject,
        organization_id: str,
        workspace_id: str | None,
    ) -> Awaitable[None]: ...


class ModelService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        registry: ProviderRegistry,
        *,
        clock: Clock | None = None,
        connection_tester: ModelConnectionTester | None = None,
        connection_test_timeout_seconds: float = 15,
        catalog: ModelCatalog | None = None,
        base_models: BaseModelDirectory | None = None,
    ) -> None:
        self._sessions = sessions
        self._registry = registry
        self._clock = clock or utc_now
        self._connection_tester = connection_tester
        self._connection_test_timeout_seconds = connection_test_timeout_seconds
        self._catalog = catalog
        self._base_models = base_models or BaseModelDirectory()

    async def create(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str | None,
        request: CreateModelRequest,
    ) -> Model:
        match = await self._authoring_suggestions(
            actor=actor,
            workspace_id=workspace_id,
            provider_id=request.provider_id,
            upstream_model=request.upstream_model,
            base_model=request.base_model,
            base_model_supplied="base_model" in request.model_fields_set,
            model_api=request.model_api,
            action=WorkspaceAction.models_manage,
        )
        selection = match.items[0] if len(match.items) == 1 else None
        base_model = selection.base_model if selection is not None else None
        model_api = request.model_api or (selection.model_api if selection is not None else None)
        if model_api is None:
            raise ModelError(
                "model_api_required",
                "Model API is required when no unique base model supplies one.",
                category=ErrorCategory.invalid_request,
            )
        declarations = request.declarations
        if selection is not None:
            declarations = merge_declarations(selection.declarations, declarations)
        now = self._clock()
        try:
            async with transaction(self._sessions) as session:
                workspace = await authorize_models(
                    session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.models_manage
                )
                await require_available_key(
                    session, organization_id=workspace.organization_id, workspace_id=workspace_id, key=request.key
                )
                provider = await require_provider(
                    session,
                    organization_id=workspace.organization_id,
                    workspace_id=workspace.workspace_id,
                    provider_id=request.provider_id,
                )
                if not provider.enabled:
                    raise ModelError(
                        "model_provider_disabled", "The Model Provider is disabled.", category=ErrorCategory.conflict
                    )
                self._registry.validate_model_api(provider.type, model_api)
                validate_settings(model_api, request.settings)
                record = ModelRecord(
                    id=new_model_id(),
                    organization_id=workspace.organization_id,
                    workspace_id=workspace.workspace_id,
                    key=request.key,
                    normalized_key=normalize_key(request.key),
                    provider_id=provider.id,
                    name=request.name,
                    description=request.description,
                    upstream_model=request.upstream_model,
                    base_model=base_model,
                    model_api=model_api,
                    settings=request.settings,
                    declarations=declarations.model_dump(mode="json"),
                    enabled=request.enabled,
                    created_by_type=actor.principal.principal_type.value,
                    created_by_id=actor.principal.principal_id,
                    updated_by_type=actor.principal.principal_type.value,
                    updated_by_id=actor.principal.principal_id,
                    created_at=now,
                    updated_at=now,
                )
                session.add(record)
                session.add(
                    audit_record(
                        actor=actor,
                        organization_id=workspace.organization_id,
                        workspace_id=workspace.workspace_id,
                        resource_type="model",
                        resource_id=record.id,
                        action="model.create",
                        now=now,
                    )
                )
                await session.flush()
                return record.to_resource()
        except IntegrityError as error:
            if not is_unique_conflict(error, constraint="uq_models_workspace_key"):
                raise
            raise ModelError(
                "model_key_conflict",
                "A Model with this key is already visible in the Workspace.",
                category=ErrorCategory.conflict,
            ) from error

    async def catalog_suggestions(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str | None,
        provider_id: str,
        upstream_model: str,
        base_model: str | None,
        base_model_supplied: bool,
        model_api: str | None,
    ) -> ModelCatalogMatch:
        return await self._authoring_suggestions(
            actor=actor,
            workspace_id=workspace_id,
            provider_id=provider_id,
            upstream_model=upstream_model,
            base_model=base_model,
            base_model_supplied=base_model_supplied,
            model_api=model_api,
            action=WorkspaceAction.models_read,
        )

    def base_model_candidates(self) -> BaseModelCandidateCollection:
        return self._base_models.candidates()

    async def _authoring_suggestions(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str | None,
        provider_id: str,
        upstream_model: str,
        base_model: str | None,
        base_model_supplied: bool,
        model_api: str | None,
        action: WorkspaceAction,
    ) -> ModelCatalogMatch:
        async with transaction(self._sessions) as session:
            workspace = await authorize_models(session, actor=actor, workspace_id=workspace_id, action=action)
            provider = await require_provider(
                session,
                organization_id=workspace.organization_id,
                workspace_id=workspace.workspace_id,
                provider_id=provider_id,
            )
            if model_api is not None:
                self._registry.validate_model_api(provider.type, model_api)
            supported_model_apis = self._registry.definition(provider.type).supported_model_apis
        try:
            resolution = self._base_models.resolve(
                provider_type=provider.type,
                provider_configuration=provider.configuration,
                supported_model_apis=supported_model_apis,
                upstream_model=upstream_model,
                base_model=base_model,
                base_model_supplied=base_model_supplied,
                model_api=model_api,
            )
        except ValueError as error:
            raise ModelError("invalid_base_model", str(error), category=ErrorCategory.invalid_request) from error
        return await self._catalog_suggestions(provider.type, provider.configuration, resolution)

    async def _catalog_suggestions(
        self,
        provider_type: str,
        provider_configuration: dict[str, object],
        resolution: BaseModelResolution,
    ) -> ModelCatalogMatch:
        items: list[ModelCatalogSuggestion] = []
        labels = self._registry.definition(provider_type).model_api_labels
        for selection in resolution.items:
            declarations = (
                await self._catalog.declarations(provider_type, provider_configuration, selection.reference)
                if self._catalog is not None
                else ModelDeclarations()
            )
            model_api = selection.model_api
            items.append(
                ModelCatalogSuggestion(
                    base_model=selection.reference.base_model,
                    model_api=model_api,
                    model_api_label=labels[model_api] if model_api is not None else None,
                    declarations=declarations,
                )
            )
        return ModelCatalogMatch(source=resolution.source, items=tuple(items))

    async def get(self, *, actor: AuthenticatedActor, workspace_id: str | None, model_id: str) -> Model:
        async with transaction(self._sessions) as session:
            workspace = await authorize_models(
                session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.models_read
            )
            return (
                await require_model(
                    session,
                    organization_id=workspace.organization_id,
                    workspace_id=workspace.workspace_id,
                    model_id=model_id,
                )
            ).to_resource()

    async def list(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str | None,
        limit: int = 50,
        cursor: str | None = None,
        query_text: str | None = None,
        provider_id: str | None = None,
        enabled: bool | None = None,
        owner_scope: Literal["organization", "workspace"] | None = None,
    ) -> ModelCollection:
        if not 1 <= limit <= 100:
            raise ModelError(
                "invalid_request", "limit must be between 1 and 100.", category=ErrorCategory.invalid_request
            )
        if query_text is not None and (not query_text.strip() or len(query_text) > 128):
            raise ModelError("invalid_request", "query search is invalid.", category=ErrorCategory.invalid_request)
        if owner_scope == "workspace" and workspace_id is None:
            raise ModelError(
                "invalid_request",
                "workspace scope requires a Workspace collection.",
                category=ErrorCategory.invalid_request,
            )
        cursor_scope = {
            "workspace_id": workspace_id,
            "organization_boundary": actor.boundary_organization_id,
            "principal_type": actor.principal.principal_type.value,
            "principal_id": actor.principal.principal_id,
            "query": query_text,
            "provider_id": provider_id,
            "enabled": enabled,
            "owner_scope": owner_scope,
            "resource": "model",
        }
        try:
            position = decode_model_cursor(cursor, scope=cursor_scope) if cursor is not None else None
        except CursorError as error:
            raise ModelError(
                "invalid_cursor", "The collection cursor is invalid.", category=ErrorCategory.invalid_request
            ) from error
        async with transaction(self._sessions) as session:
            workspace = await authorize_models(
                session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.models_read
            )
            query = select(ModelRecord).where(
                ModelRecord.organization_id == workspace.organization_id,
                visible_workspace(ModelRecord.workspace_id, workspace.workspace_id),
            )
            if owner_scope == "organization":
                query = query.where(ModelRecord.workspace_id.is_(None))
            elif owner_scope == "workspace":
                query = query.where(ModelRecord.workspace_id == workspace.workspace_id)
            if query_text is not None:
                escaped = escape_like(query_text.strip())
                query = query.where(
                    or_(
                        ModelRecord.name.ilike(f"%{escaped}%", escape="\\"),
                        ModelRecord.key.ilike(f"%{escaped}%", escape="\\"),
                    )
                )
            if provider_id is not None:
                query = query.where(ModelRecord.provider_id == provider_id)
            if enabled is not None:
                query = query.where(ModelRecord.enabled == enabled)
            if position is not None:
                updated_at, item_id = position
                query = query.where(
                    or_(
                        ModelRecord.updated_at < updated_at,
                        and_(ModelRecord.updated_at == updated_at, ModelRecord.id < item_id),
                    )
                )
            records = tuple(
                (
                    await session.scalars(
                        query.order_by(ModelRecord.updated_at.desc(), ModelRecord.id.desc()).limit(limit + 1)
                    )
                ).all()
            )
        page = records[:limit]
        next_cursor = None
        if len(records) > limit and page:
            next_cursor = encode_model_cursor(
                updated_at=page[-1].updated_at,
                item_id=page[-1].id,
                scope=cursor_scope,
            )
        return ModelCollection(items=tuple(record.to_resource() for record in page), next_cursor=next_cursor)

    async def update(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str | None,
        model_id: str,
        if_match: str,
        request: UpdateModelRequest,
    ) -> Model:
        async with transaction(self._sessions) as session:
            workspace = await authorize_models(
                session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.models_manage
            )
            record = await require_model(
                session,
                organization_id=workspace.organization_id,
                workspace_id=workspace.workspace_id,
                model_id=model_id,
                lock=True,
            )
            require_etag(record.id, record.updated_at, if_match)
            provider = await require_provider(
                session,
                organization_id=workspace.organization_id,
                workspace_id=workspace.workspace_id,
                provider_id=record.provider_id,
            )
            model_api = request.model_api if request.model_api is not None else record.model_api
            settings = request.settings if request.settings is not None else record.settings
            self._registry.validate_model_api(provider.type, model_api)
            validate_settings(model_api, settings)
            if "name" in request.model_fields_set:
                assert request.name is not None
                record.name = request.name
            if "description" in request.model_fields_set:
                record.description = request.description
            if "upstream_model" in request.model_fields_set:
                assert request.upstream_model is not None
                record.upstream_model = request.upstream_model
            if "base_model" in request.model_fields_set:
                if request.base_model is not None:
                    try:
                        self._base_models.require(request.base_model)
                    except ValueError as error:
                        raise ModelError(
                            "invalid_base_model", str(error), category=ErrorCategory.invalid_request
                        ) from error
                record.base_model = request.base_model
            record.model_api = model_api
            record.settings = settings
            if "declarations" in request.model_fields_set:
                assert request.declarations is not None
                record.declarations = request.declarations.model_dump(mode="json")
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
                    resource_type="model",
                    resource_id=record.id,
                    action="model.update",
                    now=record.updated_at,
                )
            )
            await session.flush()
            return record.to_resource()

    async def test(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str | None,
        model_id: str,
    ) -> ModelConnectionTestResult:
        if self._connection_tester is None:
            raise ModelError(
                "model_connection_tester_unavailable",
                "Model testing is unavailable.",
                category=ErrorCategory.unavailable,
            )
        async with transaction(self._sessions) as session:
            workspace = await authorize_models(
                session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.models_manage
            )
            model = (
                await require_model(
                    session,
                    organization_id=workspace.organization_id,
                    workspace_id=workspace.workspace_id,
                    model_id=model_id,
                )
            ).to_resource()
        snapshot = ModelExecutionSnapshot.freeze(model)
        result = await test_connection(
            self._connection_tester(
                snapshot=snapshot,
                settings=model.settings,
                organization_id=model.organization_id,
                workspace_id=model.workspace_id,
            ),
            timeout_seconds=self._connection_test_timeout_seconds,
            subject="Model API",
        )
        async with transaction(self._sessions) as session:
            session.add(
                audit_record(
                    actor=actor,
                    organization_id=model.organization_id,
                    workspace_id=model.workspace_id,
                    resource_type="model",
                    resource_id=model.id,
                    action="model.test",
                    now=self._clock(),
                    outcome="success" if result.success else "failure",
                )
            )
        return result


async def require_model(
    session: AsyncSession,
    *,
    organization_id: str,
    workspace_id: str | None,
    model_id: str,
    lock: bool = False,
) -> ModelRecord:
    query = select(ModelRecord).where(
        ModelRecord.organization_id == organization_id,
        visible_workspace(ModelRecord.workspace_id, workspace_id),
        ModelRecord.id == model_id,
    )
    if lock:
        query = query.where(ModelRecord.workspace_id == workspace_id).with_for_update()
    record = await session.scalar(query)
    if record is None:
        raise ModelError("model_not_found", "The Model was not found.", category=ErrorCategory.not_found)
    return record
