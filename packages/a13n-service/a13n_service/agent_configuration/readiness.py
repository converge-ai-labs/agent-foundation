"""Non-inference readiness and deterministic authorized Model selection."""

from __future__ import annotations

from typing import Literal
from urllib.parse import quote, urlencode

from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.iam import AuthenticatedActor, AuthorizationError, WorkspaceAction, authorize_workspace
from a13n_service.iam.models import WorkspaceRecord
from a13n_service.iam.resource_scope import visible_workspace
from a13n_service.models.domain import Model
from a13n_service.models.models import ModelProviderRecord, ModelRecord
from a13n_service.models.providers import ProviderRegistry
from a13n_service.models.service_common import ModelError
from a13n_service.models.settings import effective_settings
from a13n_service.storage import short_session

from .authorization import authorize_target
from .context import StrictModel
from .definition import AssistantDefinition
from .persistence import not_found


class SelectedAssistantModel(StrictModel):
    model_id: str
    model_key: str
    settings: dict[str, JsonValue]
    selection_reason: str


class AssistantReadiness(StrictModel):
    ready: bool
    reason_code: Literal[
        "ready", "provider_setup_required", "model_setup_required", "model_access_denied", "compatible_model_required"
    ]
    setup_actions: tuple[Literal["configure_provider", "configure_model", "contact_administrator"], ...]
    setup_url: str
    selected_model: SelectedAssistantModel | None = None


class ConfigurationReadiness:
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], registry: ProviderRegistry, definition: AssistantDefinition
    ) -> None:
        self._sessions, self._registry, self._definition = sessions, registry, definition

    async def read(self, *, actor: AuthenticatedActor, target_agent_id: str | None = None) -> AssistantReadiness:
        async with short_session(self._sessions) as session:
            try:
                await authorize_target(session, actor=actor, target_agent_id=target_agent_id)
            except AuthorizationError as error:
                raise not_found() from error
            workspace = await session.get(WorkspaceRecord, actor.workspace_id)
            assert workspace is not None
            setup_url = f"/workspace/{quote(workspace.key, safe='')}/settings?" + urlencode(
                {"section": "providers", "category": "models"}
            )
            can_manage = True
            try:
                await authorize_workspace(
                    session, actor=actor, workspace_id=workspace.id, action=WorkspaceAction.models_manage
                )
            except AuthorizationError:
                can_manage = False
            actions = ("configure_provider", "configure_model") if can_manage else ("contact_administrator",)
            try:
                await authorize_workspace(
                    session, actor=actor, workspace_id=workspace.id, action=WorkspaceAction.models_read
                )
            except AuthorizationError:
                return AssistantReadiness(
                    ready=False,
                    reason_code="model_access_denied",
                    setup_actions=("contact_administrator",),
                    setup_url=setup_url,
                )
            providers = tuple(
                await session.scalars(
                    select(ModelProviderRecord).where(
                        ModelProviderRecord.organization_id == workspace.organization_id,
                        visible_workspace(ModelProviderRecord.workspace_id, workspace.id),
                    )
                )
            )
            configured = {
                provider.id: provider for provider in providers if provider_configured(self._registry, provider)
            }
            if not configured:
                return AssistantReadiness(
                    ready=False, reason_code="provider_setup_required", setup_actions=actions, setup_url=setup_url
                )
            setup_url = f"/workspace/{quote(workspace.key, safe='')}/models"
            actions = ("configure_model",) if can_manage else ("contact_administrator",)
            records = tuple(
                await session.scalars(
                    select(ModelRecord).where(
                        ModelRecord.organization_id == workspace.organization_id,
                        visible_workspace(ModelRecord.workspace_id, workspace.id),
                        ModelRecord.provider_id.in_(configured),
                        ModelRecord.enabled.is_(True),
                    )
                )
            )
            if not records:
                return AssistantReadiness(
                    ready=False, reason_code="model_setup_required", setup_actions=actions, setup_url=setup_url
                )
            candidates = []
            for record in records:
                model = record.to_resource()
                selected = select_candidate(model, definition=self._definition)
                if selected is None:
                    continue
                priority, settings, reason = selected
                try:
                    self._registry.validate_model_api(configured[record.provider_id].type, model.model_api)
                    effective_settings(model.model_api, model.settings, settings)
                except (ModelError, ValueError):
                    continue
                candidates.append((priority, model.key, model.id, settings, reason))
            if not candidates:
                return AssistantReadiness(
                    ready=False, reason_code="compatible_model_required", setup_actions=actions, setup_url=setup_url
                )
            _, key, model_id, settings, reason = min(candidates, key=lambda item: item[:3])
            return AssistantReadiness(
                ready=True,
                reason_code="ready",
                setup_actions=(),
                setup_url=setup_url,
                selected_model=SelectedAssistantModel(
                    model_id=model_id, model_key=key, settings=settings, selection_reason=reason
                ),
            )


def provider_configured(registry: ProviderRegistry, provider: ModelProviderRecord) -> bool:
    if not provider.enabled:
        return False
    try:
        registry.validate_provider(
            provider.type,
            provider.configuration,
            credential_configured=provider.credential_configured,
            header_names=provider.header_names or (),
        )
    except (ModelError, ValueError):
        return False
    return True


def select_candidate(model: Model, *, definition: AssistantDefinition) -> tuple[int, dict[str, JsonValue], str] | None:
    if model.declarations.supports_tools is False:
        return None
    for index, preference in enumerate(definition.preferences):
        if model.upstream_model not in preference.upstream_models:
            continue
        if model.model_api not in preference.model_apis:
            return None
        if (
            preference.required_thinking_effort is not None
            and preference.required_thinking_effort not in model.declarations.thinking_efforts
        ):
            return None
        return index, preference.settings, preference.family
    if model.declarations.supports_tools is True:
        return len(definition.preferences), {}, "compatible_available_model"
    return None
