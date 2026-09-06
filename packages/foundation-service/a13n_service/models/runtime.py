"""Run-time Model snapshotting and Harness model resolution."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, cast

from a13n_harness import AgentContext
from a13n_harness.errors import ModelResolutionError
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import Model as PydanticModel
from pydantic_ai.models import ModelRequestParameters, ModelResolutionContext, StreamedResponse
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings
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
from .service_common import ModelError
from .settings import JsonObject, effective_settings, validate_settings


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


class LiveProviderModel(WrapperModel):
    """Preserve Model identity while refreshing its Provider for every outbound call."""

    def __init__(
        self,
        *,
        initial: PydanticModel[Any],
        snapshot: ModelExecutionSnapshot,
        organization_id: str,
        workspace_id: str,
        provider_resolver: LiveProviderResolver,
        model_factory: NativeModelFactory,
        thread_id: str,
    ) -> None:
        super().__init__(initial)
        self._snapshot = snapshot
        self._organization_id = organization_id
        self._workspace_id = workspace_id
        self._provider_resolver = provider_resolver
        self._model_factory = model_factory
        self._thread_id = thread_id

    async def __aenter__(self) -> LiveProviderModel:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def _fresh(self) -> PydanticModel[Any]:
        provider = await self._provider_resolver.resolve(
            organization_id=self._organization_id,
            workspace_id=self._workspace_id,
            snapshot=self._snapshot,
        )
        return self._model_factory.build(self._snapshot, provider)

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        _validate_harness_settings(self._snapshot.model_api, model_settings, self._thread_id)
        model = await self._fresh()
        async with model:
            return await model.request(messages, model_settings, model_request_parameters)

    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: Any = None,
    ) -> AsyncIterator[StreamedResponse]:
        _validate_harness_settings(self._snapshot.model_api, model_settings, self._thread_id)
        model = await self._fresh()
        async with model:
            async with model.request_stream(messages, model_settings, model_request_parameters, run_context) as stream:
                yield stream


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
        provider = await self._provider_resolver.resolve(
            organization_id=self._organization_id,
            workspace_id=self._workspace_id,
            snapshot=self._snapshot,
        )
        initial = self._model_factory.build(self._snapshot, provider)
        return LiveProviderModel(
            initial=initial,
            snapshot=self._snapshot,
            organization_id=self._organization_id,
            workspace_id=self._workspace_id,
            provider_resolver=self._provider_resolver,
            model_factory=self._model_factory,
            thread_id=context.deps.thread_id,
        )


def _validate_harness_settings(model_api: str, settings: ModelSettings | None, thread_id: str) -> None:
    value = cast(JsonObject, dict(settings or {}))
    headers = value.get("extra_headers")
    if isinstance(headers, dict):
        # Only the exact Harness-owned Thread affinity value bypasses the authored header allowlist.
        value["extra_headers"] = {
            key: item for key, item in headers.items() if not (key.lower() == "x-session-id" and item == thread_id)
        }
    if value.get("openai_prompt_cache_key") == thread_id:
        # Harness supplies this default for every adapter; non-OpenAI adapters ignore it.
        value.pop("openai_prompt_cache_key")
    validate_settings(model_api, value)


def _require_enabled(model: ModelRecord, provider: ModelProviderRecord) -> None:
    if not model.enabled:
        raise ModelError("model_disabled", "The selected Model is disabled.", category=ErrorCategory.conflict)
    if not provider.enabled:
        raise ModelError(
            "model_provider_disabled", "The selected Model Provider is disabled.", category=ErrorCategory.conflict
        )
