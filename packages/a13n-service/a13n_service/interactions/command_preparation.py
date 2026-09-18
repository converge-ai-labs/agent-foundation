"""Input preparation and final invocation validation for Run commands."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.agents.domain import AgentRunOverride, EffectiveAgentConfig
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
from a13n_service.storage import short_session, transaction

from .environment_preview import has_input_environment
from .errors import InteractionCommandError
from .input import AcceptedAgentInput


@dataclass(frozen=True, slots=True)
class PreparedCommandInput:
    invocation: PreparedAgentInvocation
    frozen: FrozenAgentInvocation
    input: AcceptedAgentInput


class CommandInput:
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], assets: AssetCatalog, endpoint_policy: EndpointPolicy
    ) -> None:
        self._sessions = sessions
        self._assets = assets
        self._endpoint_policy = endpoint_policy

    async def prepare(
        self,
        invocations: AgentInvocationResolver,
        *,
        actor: AuthenticatedActor,
        agent_id: str,
        agent_revision_id: str | None,
        expected_default_revision_id: str | None,
        config_override: AgentRunOverride | None,
        submitted: AgentInput,
        environment: EnvironmentSelection | Omitted | None = Omitted.UNSET,
        inherited_environment_id: str | Omitted | None = Omitted.UNSET,
        prepared_assets: Mapping[str, Asset] | None = None,
    ) -> PreparedCommandInput:
        """Freeze a selected invocation, then accept its input outside the transaction.

        Callers select the authority and inheritance source and revalidate the
        returned invocation and frozen configuration in the acceptance transaction.
        """
        prepared = await invocations.preparation.prepare(
            actor=actor,
            agent_id=agent_id,
            agent_revision_id=agent_revision_id,
            expected_default_revision_id=expected_default_revision_id,
            config_override=config_override,
        )
        async with transaction(self._sessions) as database:
            frozen = await invocations.freezing.freeze_in_transaction(database, prepared=prepared)
        accepted = await self.accept(
            actor=actor,
            workspace_id=actor.workspace_id,
            submitted=submitted,
            frozen=frozen,
            environment=environment,
            inherited_environment_id=inherited_environment_id,
            prepared_assets=prepared_assets,
        )
        return PreparedCommandInput(prepared, frozen, accepted)

    async def accept_with_skill_refresh[T](
        self,
        invocations: AgentInvocationResolver,
        prepared: PreparedCommandInput,
        operation: Callable[[PreparedCommandInput], Awaitable[T]],
    ) -> T:
        """Retry only Skill publication changes, retaining prepared identities and input.

        Each operation builds a fresh unaccepted Run and state outside SQL. The
        original AgentRevision, non-Skill settings, and pinned identities never
        change; abandoned objects remain eligible for ordinary orphan collection.
        """
        for _ in range(2):
            try:
                return await operation(prepared)
            except SkillPublicationChanged:
                async with transaction(self._sessions) as database:
                    frozen = await invocations.freezing.freeze_in_transaction(database, prepared=prepared.invocation)
                if not _same_without_skills(frozen, prepared.frozen):
                    raise InteractionCommandError(
                        "run_invocation_changed",
                        "The selected Agent invocation changed before Run acceptance.",
                        category=ErrorCategory.conflict,
                    ) from None
                prepared = replace(prepared, frozen=frozen)
        return await operation(prepared)

    async def accept(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        submitted: AgentInput,
        frozen: FrozenAgentInvocation,
        environment: EnvironmentSelection | Omitted | None = Omitted.UNSET,
        inherited_environment_id: str | Omitted | None = Omitted.UNSET,
        prepared_assets: Mapping[str, Asset] | None = None,
    ) -> AcceptedAgentInput:
        async with short_session(self._sessions) as database:
            available = await has_input_environment(
                database,
                actor=actor,
                agent_id=frozen.agent_id,
                agent_revision_id=frozen.agent_revision_id,
                choice=environment,
                inherited_id=inherited_environment_id,
            )
        return await self.accept_effective(
            actor=actor,
            workspace_id=workspace_id,
            submitted=submitted,
            effective=frozen.effective_config,
            environment_available=available,
            prepared_assets=prepared_assets,
        )

    async def accept_effective(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        submitted: AgentInput,
        effective: EffectiveAgentConfig,
        environment_available: bool = False,
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
                    environment_writable=environment_available,
                    environment_bindings=(frozenset({"workspace"}) if environment_available else frozenset()),
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
        if _same_without_skills(final, frozen) and _skills_differ(final.effective_config, frozen.effective_config):
            raise SkillPublicationChanged()
        raise InteractionCommandError(
            "run_invocation_changed",
            "The selected Agent invocation changed before Run acceptance.",
            category=ErrorCategory.conflict,
        )


class SkillPublicationChanged(InteractionCommandError):
    """Only current Skill locks changed before the acceptance transaction committed."""

    def __init__(self) -> None:
        super().__init__(
            "run_invocation_changed",
            "Skill publication changed the invocation before Run acceptance.",
            category=ErrorCategory.conflict,
        )


def _without_skills(config: EffectiveAgentConfig) -> EffectiveAgentConfig:
    return config.model_copy(
        update={
            "skills": (),
            "content_digest": "0" * 64,
            "child_configs": {
                key: child.model_copy(update={"effective_config": _without_skills(child.effective_config)})
                for key, child in config.child_configs.items()
            },
        }
    )


def _same_without_skills(final: FrozenAgentInvocation, frozen: FrozenAgentInvocation) -> bool:
    return (
        final.agent_id == frozen.agent_id
        and final.agent_revision_id == frozen.agent_revision_id
        and final.selector_kind == frozen.selector_kind
        and final.connection_selections == frozen.connection_selections
        and _without_skills(final.effective_config) == _without_skills(frozen.effective_config)
    )


def _skills_differ(first: EffectiveAgentConfig, second: EffectiveAgentConfig) -> bool:
    return first.skills != second.skills or any(
        _skills_differ(child.effective_config, second.child_configs[key].effective_config)
        for key, child in first.child_configs.items()
    )
