"""Transactional Model application service."""

from __future__ import annotations

from collections.abc import Awaitable, Sequence
from time import monotonic
from typing import Protocol

from anyio import fail_after
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.iam.authorization import AuthenticatedActor, WorkspaceAction
from a13n_service.storage import transaction
from a13n_service.temporal import Clock, utc_now

from .cursors import CursorError, decode_model_cursor, encode_model_cursor
from .domain import (
    CreateModelRequest,
    Model,
    ModelApiConfig,
    ModelCollection,
    ModelConnectionTestResult,
    ModelExecutionSnapshot,
    UpdateModelRequest,
    new_model_id,
    normalize_key,
)
from .models import ModelRecord
from .provider_service import require_provider
from .providers import ProviderRegistry
from .service_common import ModelError, audit_record, authorize_models, require_etag


class ModelConnectionTester(Protocol):
    def __call__(
        self,
        *,
        snapshot: ModelExecutionSnapshot,
        organization_id: str,
        workspace_id: str,
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
    ) -> None:
        self._sessions = sessions
        self._registry = registry
        self._clock = clock or utc_now
        self._connection_tester = connection_tester
        self._connection_test_timeout_seconds = connection_test_timeout_seconds

    async def create(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        request: CreateModelRequest,
    ) -> Model:
        now = self._clock()
        try:
            async with transaction(self._sessions) as session:
                workspace = await authorize_models(
                    session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.models_manage
                )
                provider = await require_provider(
                    session,
                    organization_id=workspace.organization_id,
                    workspace_id=workspace.workspace_id,
                    provider_id=request.provider_id,
                )
                if not provider.enabled:
                    raise ModelError("model_provider_disabled", "The Model Provider is disabled.", status_code=409)
                self._validate_apis(provider.type, request.model_apis)
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
                    model_apis=[item.model_dump(mode="json") for item in request.model_apis],
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
            raise ModelError(
                "model_key_conflict",
                "A Model with this key already exists in the Workspace.",
                status_code=409,
            ) from error

    async def get(self, *, actor: AuthenticatedActor, workspace_id: str, model_id: str) -> Model:
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
        workspace_id: str,
        limit: int = 50,
        cursor: str | None = None,
        query_text: str | None = None,
        provider_id: str | None = None,
        enabled: bool | None = None,
    ) -> ModelCollection:
        if not 1 <= limit <= 100:
            raise ModelError("invalid_request", "limit must be between 1 and 100.", status_code=400)
        if query_text is not None and (not query_text.strip() or len(query_text) > 128):
            raise ModelError("invalid_request", "query search is invalid.", status_code=400)
        scope = {
            "workspace_id": workspace_id,
            "principal_type": actor.principal.principal_type.value,
            "principal_id": actor.principal.principal_id,
            "query": query_text,
            "provider_id": provider_id,
            "enabled": enabled,
            "resource": "model",
        }
        try:
            position = decode_model_cursor(cursor, scope=scope) if cursor is not None else None
        except CursorError as error:
            raise ModelError("invalid_cursor", "The collection cursor is invalid.", status_code=400) from error
        async with transaction(self._sessions) as session:
            workspace = await authorize_models(
                session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.models_read
            )
            query = select(ModelRecord).where(
                ModelRecord.organization_id == workspace.organization_id,
                ModelRecord.workspace_id == workspace.workspace_id,
            )
            if query_text is not None:
                escaped = _escape_like(query_text.strip())
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
            next_cursor = encode_model_cursor(updated_at=page[-1].updated_at, model_id=page[-1].id, scope=scope)
        return ModelCollection(items=tuple(record.to_resource() for record in page), next_cursor=next_cursor)

    async def update(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
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
            model_apis = request.model_apis if request.model_apis is not None else record.to_resource().model_apis
            self._validate_apis(provider.type, model_apis)
            if "name" in request.model_fields_set:
                assert request.name is not None
                record.name = request.name
            if "description" in request.model_fields_set:
                record.description = request.description
            if "upstream_model" in request.model_fields_set:
                assert request.upstream_model is not None
                record.upstream_model = request.upstream_model
            if "model_apis" in request.model_fields_set:
                assert request.model_apis is not None
                record.model_apis = [item.model_dump(mode="json") for item in request.model_apis]
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
        workspace_id: str,
        model_id: str,
        model_api: str,
    ) -> ModelConnectionTestResult:
        if self._connection_tester is None:
            raise ModelError("model_connection_tester_unavailable", "Model testing is unavailable.", status_code=503)
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
        try:
            snapshot = ModelExecutionSnapshot.freeze(model, model_api)
        except ValueError as error:
            raise ModelError("model_api_not_configured", "The Model API is not configured.", status_code=400) from error
        started = monotonic()
        success, code, message = True, "connection_succeeded", "The Model API connection succeeded."
        try:
            with fail_after(self._connection_test_timeout_seconds):
                await self._connection_tester(
                    snapshot=snapshot,
                    organization_id=model.organization_id,
                    workspace_id=model.workspace_id,
                )
        except TimeoutError:
            success, code, message = False, "connection_timeout", "The Model API connection timed out."
        except Exception:
            success, code, message = False, "connection_failed", "The Model API connection failed."
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
                    outcome="success" if success else "failure",
                )
            )
        return ModelConnectionTestResult(
            success=success,
            elapsed_ms=max(0, round((monotonic() - started) * 1000)),
            code=code,
            message=message,
        )

    def _validate_apis(self, provider_type: str, model_apis: Sequence[ModelApiConfig]) -> None:
        try:
            self._registry.validate_model_apis(provider_type, model_apis)
        except ValueError as error:
            raise ModelError("invalid_model_apis", "The Model APIs are invalid.", status_code=400) from error


async def require_model(
    session: AsyncSession,
    *,
    organization_id: str,
    workspace_id: str,
    model_id: str,
    lock: bool = False,
) -> ModelRecord:
    query = select(ModelRecord).where(
        ModelRecord.organization_id == organization_id,
        ModelRecord.workspace_id == workspace_id,
        ModelRecord.id == model_id,
    )
    if lock:
        query = query.with_for_update()
    record = await session.scalar(query)
    if record is None:
        raise ModelError("model_not_found", "The Model was not found.", status_code=404)
    return record


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
