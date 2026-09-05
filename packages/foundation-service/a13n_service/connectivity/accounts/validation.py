"""Configuration-time authority for the four inbound override categories."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.agents.skill_resolution import SkillSelectionInvalid, prepare_skill_bindings
from a13n_service.application_errors import ErrorCategory
from a13n_service.connectivity.errors import NativeError
from a13n_service.connectivity.selection_resolution import ConnectivitySelectionError, ConnectivitySelectionResolver
from a13n_service.iam import (
    AuthenticatedActor,
    PrincipalRef,
    PrincipalType,
    WorkspaceAction,
    authorize_agent,
    authorize_workspace,
)
from a13n_service.iam.resource_scope import visible_workspace
from a13n_service.models.models import ModelProviderRecord, ModelRecord

from .reception import InputBatchingPolicy, InputOverride, Reception


async def validate_override(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    organization_id: str,
    workspace_id: str,
    override: InputOverride | None,
) -> None:
    if override is None:
        return
    try:
        if override.skills:
            await authorize_workspace(
                session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.skill_bind
            )
            await prepare_skill_bindings(
                session, organization_id=organization_id, workspace_id=workspace_id, selections=override.skills
            )
        if override.model is not None:
            await authorize_workspace(
                session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.models_read
            )
            if override.model.model_key is not None:
                model = await session.scalar(
                    select(ModelRecord)
                    .join(ModelProviderRecord)
                    .where(
                        visible_workspace(ModelRecord.workspace_id, workspace_id),
                        ModelRecord.organization_id == organization_id,
                        ModelRecord.normalized_key == override.model.model_key.casefold(),
                        ModelRecord.enabled.is_(True),
                        ModelProviderRecord.enabled.is_(True),
                    )
                )
                if model is None:
                    raise NativeError(
                        "invalid_model_selection",
                        "The selected Model is unavailable.",
                        category=ErrorCategory.invalid_request,
                    )
        await ConnectivitySelectionResolver.resolve_in_session(
            session,
            actor=actor,
            organization_id=organization_id,
            workspace_id=workspace_id,
            connector_tools=override.connector_tools or (),
            mcp_tools=override.mcp_tools or (),
        )
    except (SkillSelectionInvalid, ConnectivitySelectionError) as error:
        raise NativeError(
            "invalid_capability_selection",
            "An inbound capability is unavailable.",
            category=ErrorCategory.invalid_request,
        ) from error


def validate_batching(value: InputBatchingPolicy | None, *, max_events: int, max_interval_ms: int) -> None:
    if value and (value.max_batch_events > max_events or value.min_interval_ms > max_interval_ms):
        raise NativeError(
            "invalid_batching_policy", "Batching exceeds deployment bounds.", category=ErrorCategory.invalid_request
        )


async def validate_reception(
    session: AsyncSession,
    actor: AuthenticatedActor,
    workspace_id: str,
    reception: Reception,
) -> AuthenticatedActor | None:
    if reception.default_agent_id is not None:
        await authorize_agent(
            session,
            actor=actor,
            workspace_id=workspace_id,
            agent_id=reception.default_agent_id,
            action=WorkspaceAction.agent_invoke,
        )
    if reception.execution_service_account_id is None:
        return None
    execution_actor = AuthenticatedActor(
        principal=PrincipalRef(
            principal_type=PrincipalType.service_account, principal_id=reception.execution_service_account_id
        ),
        auth_method="internal",
        credential_id="account-reception",
        boundary_workspace_id=workspace_id,
    )
    await authorize_workspace(
        session,
        actor=execution_actor,
        workspace_id=workspace_id,
        action=WorkspaceAction.application_account_use,
    )
    if reception.default_agent_id is not None:
        await authorize_agent(
            session,
            actor=execution_actor,
            workspace_id=workspace_id,
            agent_id=reception.default_agent_id,
            action=WorkspaceAction.agent_invoke,
        )
    return execution_actor
