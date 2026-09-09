"""Input preparation and final invocation validation for Run commands."""

from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.agents.domain import EffectiveAgentConfig
from a13n_service.agents.invocation_resolution import (
    AgentInvocationResolver,
    FrozenAgentInvocation,
    PreparedAgentInvocation,
)
from a13n_service.application_errors import ErrorCategory
from a13n_service.assets import Asset, UploadedAssetSource
from a13n_service.assets.catalog import AssetCatalog
from a13n_service.endpoint_policy import EndpointPolicy
from a13n_service.environments.domain import EnvironmentSelection
from a13n_service.environments.selection import Omitted
from a13n_service.iam import (
    AuthenticatedActor,
)
from a13n_service.interactions.input import (
    AgentInput,
    AgentInputAcceptance,
    AgentInputAcceptanceContext,
    AgentInputError,
)
from a13n_service.secrets.agent_inputs import graph_secret_requirements, require_secret, validate_secret_bindings
from a13n_service.secrets.domain import AgentSecretBinding
from a13n_service.storage import short_session

from .environment_preview import input_environment_access
from .errors import InteractionCommandError
from .input import AcceptedAgentInput


class CommandInput:
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], assets: AssetCatalog, endpoint_policy: EndpointPolicy
    ) -> None:
        self._sessions = sessions
        self._assets = assets
        self._endpoint_policy = endpoint_policy

    async def accept(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        submitted: AgentInput,
        frozen: FrozenAgentInvocation,
        environment: EnvironmentSelection | Omitted | None = Omitted.UNSET,
        inherited_environment_id: str | Omitted | None = Omitted.UNSET,
        environment_access_ceiling: str | None = None,
        prepared_assets: Mapping[str, Asset] | None = None,
    ) -> AcceptedAgentInput:
        async with short_session(self._sessions) as database:
            access = await input_environment_access(
                database,
                actor=actor,
                agent_id=frozen.agent_id,
                choice=environment,
                inherited_id=inherited_environment_id,
                access_ceiling=environment_access_ceiling,
            )
        return await self.accept_effective(
            actor=actor,
            workspace_id=workspace_id,
            submitted=submitted,
            effective=frozen.effective_config,
            environment_access=access,
            prepared_assets=prepared_assets,
        )

    async def accept_effective(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        submitted: AgentInput,
        effective: EffectiveAgentConfig,
        environment_access: str | None = None,
        prepared_assets: Mapping[str, Asset] | None = None,
        retained_secret_bindings: tuple[AgentSecretBinding, ...] | None = None,
    ) -> AcceptedAgentInput:
        if retained_secret_bindings is not None:
            if submitted.secret_bindings and submitted.secret_bindings != retained_secret_bindings:
                raise InteractionCommandError(
                    "input_secret_bindings_frozen",
                    "Steering cannot change the Run's Secret bindings.",
                    category=ErrorCategory.invalid_request,
                )
        else:
            validate_secret_bindings(submitted.secret_bindings, graph_secret_requirements(effective))
        if submitted.secret_bindings and retained_secret_bindings is None:
            async with short_session(self._sessions) as database:
                for binding in submitted.secret_bindings:
                    await require_secret(database, actor=actor, binding=binding, accepting=True)

        async def authorize_asset(asset_id: str):
            if prepared_assets is not None and (prepared := prepared_assets.get(asset_id)) is not None:
                if (
                    prepared.workspace_id != workspace_id
                    or prepared.deleted_at is not None
                    or not isinstance(prepared.source, UploadedAssetSource)
                    or prepared.source.principal != actor.principal
                ):
                    raise AgentInputError("input_asset_unavailable", "Asset is not available for Agent input")
                return prepared
            return await self._assets.require_for_use(actor=actor, asset_id=asset_id)

        acceptance = AgentInputAcceptance(self._endpoint_policy, authorize_asset)
        try:
            return await acceptance.accept(
                submitted,
                AgentInputAcceptanceContext(
                    workspace_id=workspace_id,
                    model_characteristics=effective.resolved_model.characteristics,
                    max_input_bytes=effective.protocol.limits.max_input_bytes,
                    structured_content_schema=effective.protocol.input_data_schema,
                    environment_writable=environment_access is not None and environment_access != "read_only",
                    environment_bindings=(frozenset({"workspace"}) if environment_access is not None else frozenset()),
                ),
            )
        except AgentInputError as error:
            raise InteractionCommandError(error.code, str(error), category=ErrorCategory.invalid_request) from error


async def validate_invocation(
    database: AsyncSession,
    invocations: AgentInvocationResolver,
    *,
    prepared: PreparedAgentInvocation,
    frozen: FrozenAgentInvocation,
) -> None:
    final = await invocations.freezing.freeze_in_transaction(database, prepared=prepared)
    if final != frozen:
        raise InteractionCommandError(
            "run_invocation_changed",
            "The selected Agent invocation changed before Run acceptance.",
            category=ErrorCategory.conflict,
        )
