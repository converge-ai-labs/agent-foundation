from unittest.mock import AsyncMock

import pytest
from a13n_service.agent_configuration.definition import load_definition
from a13n_service.agent_configuration.inputs import ConfigurationInputRequest, ConfigurationInputs
from a13n_service.agent_configuration.knowledge import KnowledgeBundles
from a13n_service.agent_configuration.readiness import ConfigurationReadiness
from a13n_service.agent_configuration.runtime import validate_configuration_definition
from a13n_service.agent_configuration.system_agent import SystemConfigurationAgent
from a13n_service.agents.invocation_resolution import AgentInvocationResolver
from a13n_service.agents.resolution import AgentResolver
from a13n_service.application_errors import ApplicationError
from a13n_service.endpoint_policy import EndpointPolicy
from a13n_service.gateway.queries import NativeInteractionQueries
from a13n_service.hooks import InlineHookValidator
from a13n_service.interactions.acceptance import RunAcceptanceService
from a13n_service.interactions.command_preparation import CommandInput
from a13n_service.interactions.models import RunRecord
from a13n_service.interactions.objects import RunPayloadStore, RunStateStore
from a13n_service.models.models import ModelProviderRecord, ModelRecord
from a13n_service.models.providers import built_in_provider_registry
from a13n_service.models.runtime import AcceptedModelSelector
from a13n_service.run_stream import RunReplayStore
from a13n_service.storage import short_session, transaction
from a13n_service.storage.object_store import LocalObjectStore

from ..agents.conftest import MODEL_ID, NOW, PROVIDER_ID, WORKSPACE_ID, actor
from ..lifecycle_support import test_lifecycle_writer as lifecycle_writer
from .test_drafts import new_draft, services

pytestmark = pytest.mark.anyio


async def inputs_service(sessions, tmp_path):
    async with transaction(sessions) as session:
        provider = await session.get(ModelProviderRecord, PROVIDER_ID)
        provider.credential_configured = True
        model = await session.get(ModelRecord, MODEL_ID)
        model.declarations = {**model.declarations, "supports_tools": True}
    models = AcceptedModelSelector(sessions, built_in_provider_registry())
    resolver = AgentResolver(sessions, models)
    definition = load_definition()
    objects = await LocalObjectStore.create(tmp_path / "objects")
    states = RunStateStore(objects)
    inputs = ConfigurationInputs(
        sessions,
        AgentInvocationResolver(sessions, models),
        RunAcceptanceService(
            sessions,
            states,
            RunPayloadStore(objects),
            InlineHookValidator(EndpointPolicy()),
            lifecycle=lifecycle_writer(),
            clock=lambda: NOW,
        ),
        states,
        CommandInput(sessions, AsyncMock(), EndpointPolicy()),
        ConfigurationReadiness(sessions, built_in_provider_registry(), definition),
        SystemConfigurationAgent(sessions, resolver, definition, clock=lambda: NOW),
        definition,
        KnowledgeBundles(),
        clock=lambda: NOW,
    )
    return inputs, objects


async def test_assistant_admission_has_real_identity_exact_scope_and_replay(agent_sessions, tmp_path):
    conversations, _, _ = services(agent_sessions)
    draft = await new_draft(conversations)
    inputs, objects = await inputs_service(agent_sessions, tmp_path)
    request = ConfigurationInputRequest.model_validate(
        {
            "expected_thread_version": 1,
            "input": {"schema_version": "2", "content": [{"type": "text", "text": "Help build a support agent."}]},
        }
    )
    receipt = await inputs.submit(actor=actor(), thread_id=draft.thread_id, request=request, idempotency_key="input")
    assert (
        await inputs.submit(actor=actor(), thread_id=draft.thread_id, request=request, idempotency_key="input")
        == receipt
    )
    async with short_session(agent_sessions) as session:
        run = (await session.get(RunRecord, receipt.run_id)).to_resource()
        assert run.agent_id and run.agent_revision_id
        assert run.configuration_context.draft_id == draft.id
        assert run.environment_id is None and run.authority_principal == actor().principal
    state = (await RunStateStore(objects).read_run(run)).envelope
    async with short_session(agent_sessions) as session:
        await validate_configuration_definition(session, run=run, config=state.effective_agent_config)
    queries = NativeInteractionQueries(agent_sessions, RunReplayStore(objects))
    visible = await queries.get_run(actor=actor(), run_id=run.id)
    assert visible.agent_id == run.agent_id
    assert [
        item.id
        for item in (
            await queries.list_runs(actor=actor(), workspace_id=WORKSPACE_ID, thread_id=None, limit=10, cursor=None)
        ).items
    ] == [run.id]
    assert (await queries.list_sessions(actor=actor(), workspace_id=WORKSPACE_ID, limit=10, cursor=None)).items[
        0
    ].run_count == 1
    with pytest.raises(ApplicationError):
        await inputs.submit(
            actor=actor(),
            thread_id=draft.thread_id,
            request=request.model_copy(update={"expected_thread_version": 2}),
            idempotency_key="busy",
        )
