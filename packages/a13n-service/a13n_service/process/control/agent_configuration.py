"""Compose configuration authoring over the shared Agent and Run authorities."""

from a13n_service.agent_configuration.application import ConfigurationApplication
from a13n_service.agent_configuration.conversations import ConfigurationConversations
from a13n_service.agent_configuration.definition import load_definition
from a13n_service.agent_configuration.drafts import ConfigurationDrafts
from a13n_service.agent_configuration.inputs import ConfigurationInputs
from a13n_service.agent_configuration.knowledge import KnowledgeFiles
from a13n_service.agent_configuration.readiness import ConfigurationReadiness
from a13n_service.agent_configuration.review import ConfigurationReviews
from a13n_service.agent_configuration.service import ConfigurationService
from a13n_service.agent_configuration.system_agent import SystemConfigurationAgent
from a13n_service.agents.resolution import AgentResolver
from a13n_service.assets.catalog import AssetCatalog
from a13n_service.endpoint_policy import EndpointPolicy
from a13n_service.hooks import InlineHookValidator
from a13n_service.interactions.acceptance import RunAcceptanceService
from a13n_service.interactions.command_preparation import CommandInput
from a13n_service.interactions.objects import RunPayloadStore, RunStateStore
from a13n_service.process.agents import AgentResources
from a13n_service.process.resources import ExecutionResources
from a13n_service.process.runtime import SharedRuntime
from a13n_service.settings import Settings


def build_configuration_service(
    settings: Settings,
    shared: SharedRuntime,
    resources: AgentResources,
    execution: ExecutionResources,
    resolver: AgentResolver,
    assets: AssetCatalog,
    hooks: InlineHookValidator,
) -> ConfigurationService:
    sessions = shared.storage.sessions
    definition = load_definition()
    readiness = ConfigurationReadiness(sessions, execution.model_provider_registry, definition)
    states = RunStateStore(shared.storage.objects)
    return ConfigurationService(
        conversations=ConfigurationConversations(sessions),
        drafts=ConfigurationDrafts(sessions, resolver),
        application=ConfigurationApplication(sessions, resolver),
        readiness=readiness,
        reviews=ConfigurationReviews(sessions),
        inputs=ConfigurationInputs(
            sessions,
            resources.invocations,
            RunAcceptanceService(
                sessions, states, RunPayloadStore(shared.storage.objects), hooks, lifecycle=shared.lifecycle
            ),
            states,
            CommandInput(sessions, assets, EndpointPolicy()),
            readiness,
            SystemConfigurationAgent(sessions, definition),
            definition,
            KnowledgeFiles(),
            execution_max_attempts=settings.gateway.run_execution_max_attempts,
            max_handoffs=settings.gateway.run_max_handoffs,
            queue_name=settings.gateway.run_queue_name,
            priority=settings.gateway.run_priority,
        ),
    )
