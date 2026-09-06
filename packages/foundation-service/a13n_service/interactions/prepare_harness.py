"""Production reconstruction of the accepted on-demand Harness invocation."""

from __future__ import annotations

from contextlib import AsyncExitStack
from typing import Any

import httpx2
from a13n_harness import AgentIdentityRef, AgentInstanceContext, SafeFailure
from a13n_harness.capabilities import SkillsCapability
from a13n_harness.errors import ModelResolutionError
from a13n_harness.plugin_factories import HarnessPluginFactoryCatalog
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.agents.reconstruction import AgentDefinitionReconstructionError, AgentReconstructor
from a13n_service.application_errors import ApplicationError, ErrorCategory
from a13n_service.connectivity.execution import ExternalToolRuntime
from a13n_service.endpoint_policy import EndpointPolicyError
from a13n_service.environments.access import authorize_environment_resource
from a13n_service.environments.models import EnvironmentRecord
from a13n_service.iam import AuthenticatedActor, WorkspaceAction
from a13n_service.ids import new_object_id
from a13n_service.models.model_factory import NativeModelFactory
from a13n_service.models.provider_runtime import LiveProviderResolver
from a13n_service.models.runtime import SnapshotRunModelResolver
from a13n_service.skills.runtime import SkillRuntimeError, SkillRuntimePreparer
from a13n_service.storage import ObjectNotFound, ObjectStoreUnavailable, short_session

from .attempts import AttemptContext, AttemptPreparationError
from .feedback import WaitingFeedbackMappingError
from .harness_runtime import HarnessCollaborators, HarnessInvocation
from .input import AgentInputError
from .input_runtime import AttemptInputRuntime
from .objects import RunObjectError
from .preparation import AttemptDependencies, AttemptDependencyLoader
from .run_control import RunAttemptControl


class ProductionAttemptPreparer:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        control: RunAttemptControl,
        catalog: HarnessPluginFactoryCatalog,
        inputs: AttemptInputRuntime,
        skills: SkillRuntimePreparer,
        providers: LiveProviderResolver,
        models: NativeModelFactory,
        tools: ExternalToolRuntime,
        stack: AsyncExitStack,
    ) -> None:
        self._sessions = sessions
        self._control = control
        self._catalog = catalog
        self._inputs = inputs
        self._skills = skills
        self._providers = providers
        self._models = models
        self._tools = tools
        self._stack = stack

    async def prepare(self, context: AttemptContext) -> HarnessInvocation[Any]:
        try:
            return await self._prepare(context)
        except (AgentInputError, SkillRuntimeError, ModelResolutionError) as error:
            raise _failure(error.code, retryable=error.code == "skill_materialization_unavailable") from error
        except ApplicationError as error:
            raise _failure(
                error.code,
                retryable=error.category
                in {
                    ErrorCategory.rate_limited,
                    ErrorCategory.dependency_failure,
                    ErrorCategory.unavailable,
                    ErrorCategory.timeout,
                },
            ) from error
        except (ObjectStoreUnavailable, httpx2.TransportError) as error:
            raise _failure("run_dependency_unavailable", retryable=True) from error
        except AgentDefinitionReconstructionError as error:
            raise _failure("agent_definition_invalid") from error
        except (
            ValidationError,
            RunObjectError,
            ObjectNotFound,
            EndpointPolicyError,
            WaitingFeedbackMappingError,
        ) as error:
            raise _failure("run_dependency_invalid") from error

    async def _prepare(self, context: AttemptContext) -> HarnessInvocation[Any]:
        state = self._control.current_state
        dependencies = await AttemptDependencyLoader(self._sessions).load(context, state)
        run = dependencies.run
        config = state.envelope.effective_agent_config
        # Never execute a weaker Agent by silently dropping an accepted dependency.
        if config.resolved_subagents or config.secret_requirements or config.asset_publication is not None:
            raise _failure("worker_dependency_unsupported")
        if config.skills and run.environment_id is None:
            raise _failure("skill_environment_unavailable")
        actor = AuthenticatedActor(
            principal=run.authority_principal,
            auth_method="internal",
            credential_id="attempt",
            boundary_workspace_id=dependencies.workspace_id,
        )
        await self._authorize_environment(dependencies, actor)
        await self._providers.resolve(
            organization_id=run.organization_id,
            workspace_id=dependencies.workspace_id,
            snapshot=config.resolved_model.execution,
        )
        skills = await self._skills.prepare(
            organization_id=run.organization_id,
            workspace_id=dependencies.workspace_id,
            locks=config.skills,
            fence=self._inputs,
        )
        definition = AgentReconstructor(
            self._catalog,
            capability_provider=lambda context: (
                self._inputs,
                *((SkillsCapability(skills.manager),) if skills.manager is not None else ()),
            ),
        ).reconstruct(
            agent_id=run.agent_id,
            agent_revision_id=run.agent_revision_id,
            effective_config=config,
            child_revisions=dependencies.child_revisions,
        )
        source, deferred = await self._inputs.prepare(
            run,
            actor,
            config.input_adapter,
            input_pending=state.envelope.input_disposition == "pending",
        )
        tools = await self._stack.enter_async_context(self._tools.capabilities(lambda: self._control.current_context))
        await self._inputs.require_current()
        return HarnessInvocation(
            definition=definition,
            input=source,
            deferred_resume=deferred,
            collaborators=HarnessCollaborators(
                instance=AgentInstanceContext(
                    identity=AgentIdentityRef(issuer="foundation", subject=run.agent_id),
                    agent_instance_id=new_object_id("agent"),
                    host_refs={
                        "organization_id": run.organization_id,
                        "workspace_id": dependencies.workspace_id,
                        "session_id": run.session_id,
                        "run_id": run.id,
                    },
                ),
                model_resolver=SnapshotRunModelResolver(
                    snapshot=config.resolved_model.execution,
                    organization_id=run.organization_id,
                    workspace_id=dependencies.workspace_id,
                    provider_resolver=self._providers,
                    model_factory=self._models,
                ),
                capabilities=(*tools, *((skills.selection_capability,) if skills.selection_capability else ())),
                external_tools_prepared=True,
            ),
        )

    async def _authorize_environment(self, dependencies: AttemptDependencies, actor: AuthenticatedActor) -> None:
        run = dependencies.run
        if run.environment_id is None:
            return
        async with short_session(self._sessions) as database:
            row = await database.scalar(
                select(EnvironmentRecord).where(
                    EnvironmentRecord.id == run.environment_id,
                    EnvironmentRecord.organization_id == run.organization_id,
                )
            )
            if row is None or row.workspace_id not in {None, dependencies.workspace_id}:
                raise _failure("environment_unavailable")
            await authorize_environment_resource(
                database,
                actor=actor,
                organization_id=row.organization_id,
                workspace_id=row.workspace_id,
                action=WorkspaceAction.environment_use,
            )


def _failure(code: str, *, retryable: bool = False) -> AttemptPreparationError:
    return AttemptPreparationError(
        SafeFailure(code=code, message="The accepted Run dependencies could not be prepared."),
        retryable=retryable,
    )
