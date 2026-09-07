"""Run-time Model snapshotting and Harness model resolution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from a13n_harness import AgentContext
from a13n_harness.errors import ModelResolutionError
from pydantic_ai.models import Model as PydanticModel
from pydantic_ai.models import ModelResolutionContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.application_errors import ErrorCategory
from a13n_service.iam.resource_scope import visible_workspace
from a13n_service.storage import short_session

from .domain import Model as ModelResource
from .domain import ModelExecutionSnapshot
from .model_factory import NativeModelFactory
from .models import ModelProviderRecord, ModelRecord
from .provider_runtime import LiveProviderResolver
from .providers import ProviderRegistry
from .requests import LiveProviderModel
from .service_common import ModelError
from .settings import JsonObject, effective_settings


@dataclass(frozen=True, slots=True)
class PreparedModelExecution:
    organization_id: str
    workspace_id: str
    resource: ModelResource
    settings: JsonObject


class AcceptedModelSelector:
    """Resolve the latest Model for Agent validation or Run acceptance."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession], registry: ProviderRegistry) -> None:
        self._sessions = sessions
        self._registry = registry

    async def prepare(
        self,
        *,
        organization_id: str,
        workspace_id: str,
        model_key: str | None = None,
        model_id: str | None = None,
        settings: JsonObject,
    ) -> PreparedModelExecution:
        if (model_key is None) == (model_id is None):
            raise ValueError("exactly one Model selector is required")
        async with short_session(self._sessions) as session:
            query = select(ModelRecord, ModelProviderRecord).join(
                ModelProviderRecord,
                ModelProviderRecord.id == ModelRecord.provider_id,
            )
            selector = (
                ModelRecord.normalized_key == model_key.casefold()
                if model_key is not None
                else ModelRecord.id == model_id
            )
            query = query.where(
                ModelRecord.organization_id == organization_id,
                visible_workspace(ModelRecord.workspace_id, workspace_id),
                selector,
            )
            row = (await session.execute(query)).one_or_none()
            if row is None:
                raise ModelError("model_not_found", "The Model was not found.", category=ErrorCategory.not_found)
            model_record, provider_record = row
            model = model_record.to_resource()
            _require_enabled(model_record, provider_record)
            self._registry.validate_model_api(provider_record.type, model.model_api)
            effective_settings(model.model_api, model.settings, settings)
            return PreparedModelExecution(
                organization_id=organization_id,
                workspace_id=workspace_id,
                resource=model,
                settings=settings,
            )

    async def freeze_in_transaction(
        self,
        session: AsyncSession,
        *,
        prepared: PreparedModelExecution,
    ) -> ModelExecutionSnapshot:
        row = (
            await session.execute(
                select(ModelRecord, ModelProviderRecord)
                .join(ModelProviderRecord, ModelProviderRecord.id == ModelRecord.provider_id)
                .where(
                    ModelRecord.organization_id == prepared.organization_id,
                    visible_workspace(ModelRecord.workspace_id, prepared.workspace_id),
                    ModelRecord.id == prepared.resource.id,
                )
                .with_for_update()
            )
        ).one_or_none()
        if row is None:
            raise ModelError("model_not_found", "The Model was not found.", category=ErrorCategory.not_found)
        model_record, provider_record = row
        _require_enabled(model_record, provider_record)
        model = model_record.to_resource()
        if model.updated_at != prepared.resource.updated_at:
            raise ModelError(
                "model_configuration_changed",
                "The Model changed during acceptance. Retry the request.",
                category=ErrorCategory.conflict,
            )
        effective_settings(model.model_api, model.settings, prepared.settings)
        return ModelExecutionSnapshot.freeze(model)


class SnapshotRunModelResolver:
    def __init__(
        self,
        *,
        snapshot: ModelExecutionSnapshot,
        organization_id: str,
        workspace_id: str,
        provider_resolver: LiveProviderResolver,
        model_factory: NativeModelFactory,
    ) -> None:
        self._snapshot = snapshot
        self._organization_id = organization_id
        self._workspace_id = workspace_id
        self._provider_resolver = provider_resolver
        self._model_factory = model_factory

    async def __call__(
        self,
        context: ModelResolutionContext[AgentContext],
        model_id: str,
    ) -> PydanticModel[Any]:
        if model_id != self._snapshot.model_id:
            raise ModelResolutionError(
                "The requested Model does not match the accepted Run snapshot.",
                code="accepted_model_mismatch",
                details={"model_id": model_id},
            )
        return await LiveProviderModel.create(
            snapshot=self._snapshot,
            organization_id=self._organization_id,
            workspace_id=self._workspace_id,
            provider_resolver=self._provider_resolver,
            model_factory=self._model_factory,
            harness_thread_id=context.deps.thread_id,
        )


def _require_enabled(model: ModelRecord, provider: ModelProviderRecord) -> None:
    if not model.enabled:
        raise ModelError("model_disabled", "The selected Model is disabled.", category=ErrorCategory.conflict)
    if not provider.enabled:
        raise ModelError(
            "model_provider_disabled", "The selected Model Provider is disabled.", category=ErrorCategory.conflict
        )
