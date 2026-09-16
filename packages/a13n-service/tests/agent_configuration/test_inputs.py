from unittest.mock import AsyncMock

import pytest
from a13n_service.agent_configuration.definition import load_definition
from a13n_service.agent_configuration.inputs import ConfigurationInputRequest, ConfigurationInputs
from a13n_service.agent_configuration.knowledge import KnowledgeFiles
from a13n_service.agent_configuration.readiness import ConfigurationReadiness
from a13n_service.agent_configuration.runtime import validate_configuration_definition
from a13n_service.agent_configuration.system_agent import SystemConfigurationAgent
from a13n_service.agents.invocation_resolution import AgentInvocationResolver
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

from tests.memory.selection_support import ordinary_memory

from ..agents.conftest import MODEL_ID, NOW, PROVIDER_ID, WORKSPACE_ID, actor
from ..lifecycle_support import test_lifecycle_writer as lifecycle_writer
from .test_drafts import new_draft, services

pytestmark = pytest.mark.anyio


async def inputs_service(sessions, tmp_path, *, definition=None):
    async with transaction(sessions) as session:
        provider = await session.get(ModelProviderRecord, PROVIDER_ID)
        provider.credential_configured = True
        model = await session.get(ModelRecord, MODEL_ID)
        model.declarations = {**model.declarations, "supports_tools": True}
    models = AcceptedModelSelector(sessions, built_in_provider_registry())
    definition = definition or load_definition()
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
            bindings=ordinary_memory(sessions),
            lifecycle=lifecycle_writer(),
            clock=lambda: NOW,
        ),
        states,
        CommandInput(sessions, AsyncMock(), EndpointPolicy()),
        ConfigurationReadiness(sessions, built_in_provider_registry(), definition),
        SystemConfigurationAgent(sessions, definition, clock=lambda: NOW),
        definition,
        KnowledgeFiles(),
        clock=lambda: NOW,
    )
    return inputs, objects


@pytest.mark.parametrize("token_limit", [None, 3_000_000])
async def test_assistant_admission_has_real_identity_exact_scope_and_replay(agent_sessions, tmp_path, token_limit):
    conversations, _, _ = services(agent_sessions)
    draft = await new_draft(conversations)
    inputs, objects = await inputs_service(
        agent_sessions, tmp_path, definition=load_definition(total_tokens_limit=token_limit)
    )
    expected_limit = token_limit or 2_000_000
    request = ConfigurationInputRequest.model_validate(
        {
            "expected_thread_version": 1,
            "input": {"schema_version": "2", "content": [{"type": "text", "text": "Help build a support agent."}]},
        }
    )
    receipt = await inputs.submit(
        actor=actor(),
        thread_id=(await conversations.get_session(actor=actor(), session_id=draft.session_id)).root_thread_id,
        request=request,
        idempotency_key="input",
    )
    assert (
        await inputs.submit(
            actor=actor(),
            thread_id=(await conversations.get_session(actor=actor(), session_id=draft.session_id)).root_thread_id,
            request=request,
            idempotency_key="input",
        )
        == receipt
    )
    async with short_session(agent_sessions) as session:
        run = (await session.get(RunRecord, receipt.run_id)).to_resource()
        assert run.agent_id and run.agent_revision_id is None
        assert run.configuration_context.draft_id == draft.id
        assert run.environment_id is None and run.authority_principal == actor().principal
    # The nullable reference is confined to protected Runs in both the domain and database.
    from pydantic import ValidationError
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(ValidationError, match="Only protected configuration Runs"):
        type(run).model_validate({**run.model_dump(mode="json"), "configuration_context": None})
    with pytest.raises(IntegrityError):
        async with transaction(agent_sessions) as session:
            unprotected = await session.get(RunRecord, run.id)
            unprotected.configuration_context = None
            unprotected.configuration_draft_id = None
            await session.flush()
    state = (await RunStateStore(objects).read_run(run)).envelope
    assert state.usage_limits.total_tokens_limit == expected_limit
    assert state.usage_limits.request_limit == 40
    assert run.execution_budget.max_usage.input_tokens == expected_limit
    assert run.execution_budget.max_usage.output_tokens == expected_limit
    validate_configuration_definition(run=run, config=state.effective_agent_config)
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
            thread_id=(await conversations.get_session(actor=actor(), session_id=draft.session_id)).root_thread_id,
            request=request.model_copy(update={"expected_thread_version": 2}),
            idempotency_key="busy",
        )


@pytest.mark.parametrize("continuation", ["retry", "feedback", "waiting_continue"])
async def test_continuation_retains_accepted_definition_after_deployment_and_application(
    agent_sessions, tmp_path, continuation
):
    from a13n_service.etags import resource_etag
    from a13n_service.interactions.command_values import RetryRunCommand, WaitingContinueRunCommand
    from a13n_service.interactions.control_domain import InterruptRequest, WaitingRunFeedbackRequest

    from ..gateway.test_commands import _commands, _wait_run
    from .test_drafts import apply_request, save

    conversations, drafts, applications = services(agent_sessions)
    draft = await new_draft(conversations)
    thread_id = (await conversations.get_session(actor=actor(), session_id=draft.session_id)).root_thread_id
    inputs, objects = await inputs_service(agent_sessions, tmp_path)
    receipt = await inputs.submit(
        actor=actor(),
        thread_id=thread_id,
        idempotency_key="source",
        request=ConfigurationInputRequest.model_validate(
            {
                "expected_thread_version": 1,
                "input": {"schema_version": "2", "content": [{"type": "text", "text": "Build an agent"}]},
            }
        ),
    )
    # Continuations must never ask the ordinary invocation resolver to select a new definition.
    resolver = AsyncMock(side_effect=AssertionError("Unexpected definition resolution"))
    commands = _commands(agent_sessions, objects, resolver, resolver, clock=lambda: NOW)
    thread = (await conversations.get_thread(actor=actor(), thread_id=thread_id)).thread
    if continuation == "retry":
        await commands.active.interrupt(
            actor=actor(),
            run_id=receipt.run_id,
            idempotency_key="interrupt",
            request=InterruptRequest(expected_run_version=1, expected_thread_version=thread.version),
        )
    else:
        await _wait_run(agent_sessions, objects, run_id=receipt.run_id)
    saved = await save(drafts, draft)
    await applications.apply(
        actor=actor(),
        draft_id=draft.id,
        request=apply_request(saved),
        idempotency_key="apply",
        if_match=resource_etag(saved.id, saved.updated_at),
    )
    await inputs_service(
        agent_sessions,
        tmp_path,
        definition=load_definition(total_tokens_limit=3_000_000).model_copy(
            update={"instructions": "Changed deployment"}
        ),
    )
    thread = (await conversations.get_thread(actor=actor(), thread_id=thread_id)).thread
    if continuation == "retry":
        retried = await commands.continuations.retry(
            actor=actor(),
            run_id=receipt.run_id,
            idempotency_key="retry",
            request=RetryRunCommand(expected_thread_version=thread.version),
        )
    else:
        async with short_session(agent_sessions) as session:
            waiting = (await session.get(RunRecord, receipt.run_id)).to_resource()
        if continuation == "feedback":
            retried = await commands.continuations.feedback(
                actor=actor(),
                run_id=receipt.run_id,
                idempotency_key="feedback",
                request=WaitingRunFeedbackRequest(
                    expected_thread_version=thread.version,
                    sealed_state_digest_sha256=waiting.sealed_state.digest_sha256,
                ),
            )
        else:
            retried = await commands.continuations.continue_waiting(
                actor=actor(),
                run_id=receipt.run_id,
                idempotency_key="waiting",
                request=WaitingContinueRunCommand(
                    expected_thread_version=thread.version,
                    sealed_state_digest_sha256=waiting.sealed_state.digest_sha256,
                    input=ConfigurationInputRequest.model_validate(
                        {
                            "expected_thread_version": thread.version,
                            "input": {"schema_version": "2", "content": [{"type": "text", "text": "Please continue"}]},
                        }
                    ).input,
                ),
            )
    async with short_session(agent_sessions) as session:
        source = (await session.get(RunRecord, receipt.run_id)).to_resource()
        retry = (await session.get(RunRecord, retried.run_id)).to_resource()
    assert retry.agent_revision_id is None and retry.agent_id == source.agent_id
    assert retry.configuration_context == source.configuration_context
    assert retry.configuration_context.initial_draft_version == 1
    assert (await drafts.get(actor=actor(), draft_id=draft.id)).version == 3
    source_state = (await RunStateStore(objects).read_run(source)).envelope
    retry_state = (await RunStateStore(objects).read_run(retry)).envelope
    assert retry_state.usage_limits == source_state.usage_limits
    assert retry_state.usage_limits.total_tokens_limit == 2_000_000
    assert retry.execution_budget == source.execution_budget
    assert retry_state.effective_agent_config == source_state.effective_agent_config
    assert retry_state.effective_agent_config.instructions == load_definition().instructions


async def test_create_scope_snapshot_cannot_authorize_newly_bound_target(agent_sessions, tmp_path):
    from dataclasses import replace

    from a13n_service.agent_configuration.authorization import authorize_execution
    from a13n_service.etags import resource_etag
    from a13n_service.iam import AuthorizationError
    from a13n_service.iam.authorization import PrincipalPermissions, WorkspaceAction

    from ..agents.conftest import ORG_ID
    from .test_drafts import apply_request, save

    conversations, drafts, applications = services(agent_sessions)
    draft = await new_draft(conversations)
    thread_id = (await conversations.get_session(actor=actor(), session_id=draft.session_id)).root_thread_id
    inputs, _ = await inputs_service(agent_sessions, tmp_path)
    receipt = await inputs.submit(
        actor=actor(),
        thread_id=thread_id,
        idempotency_key="input",
        request=ConfigurationInputRequest.model_validate(
            {
                "expected_thread_version": 1,
                "input": {"schema_version": "2", "content": [{"type": "text", "text": "Create an agent"}]},
            }
        ),
    )
    async with short_session(agent_sessions) as session:
        run = (await session.get(RunRecord, receipt.run_id)).to_resource()
    snapshot = PrincipalPermissions(
        principal=actor().principal,
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        workspace_actions=frozenset({WorkspaceAction.agent_create}),
        agent_actions=(),
    )

    async def authorize(snapshot):
        async with short_session(agent_sessions) as session:
            await authorize_execution(
                session,
                principal=actor().principal,
                organization_id=ORG_ID,
                workspace_id=WORKSPACE_ID,
                agent_id=run.agent_id,
                context=run.configuration_context,
                snapshot=snapshot,
            )

    await authorize(snapshot)
    saved = await save(drafts, draft)
    await applications.apply(
        actor=actor(),
        draft_id=draft.id,
        request=apply_request(saved),
        idempotency_key="apply",
        if_match=resource_etag(saved.id, saved.updated_at),
    )
    with pytest.raises(AuthorizationError):
        await authorize(snapshot)
    await authorize(
        replace(
            snapshot, workspace_actions=frozenset({WorkspaceAction.agent_read, WorkspaceAction.agent_revision_create})
        )
    )


async def test_session_history_distinguishes_empty_drafts_and_summarizes_first_input(agent_sessions, tmp_path):
    from a13n_service.agent_configuration.requests import CreateSessionRequest

    conversations, _, _ = services(agent_sessions)
    empty = await conversations.create_session(
        actor=actor(), request=CreateSessionRequest(), idempotency_key="empty-history"
    )
    draft = await new_draft(conversations)
    active = await conversations.get_session(actor=actor(), session_id=draft.session_id)
    assert active.title is None and not active.has_runs
    inputs, _ = await inputs_service(agent_sessions, tmp_path)
    prompt = "Build a support agent.\n" + "Details " * 30
    await inputs.submit(
        actor=actor(),
        thread_id=active.root_thread_id,
        idempotency_key="history-input",
        request=ConfigurationInputRequest.model_validate(
            {
                "expected_thread_version": 1,
                "input": {"schema_version": "2", "content": [{"type": "text", "text": prompt}]},
            }
        ),
    )
    history = await conversations.list_sessions(actor=actor(), limit=10, cursor=None)
    entries = {item.id: item for item in history.items}
    assert len(entries) == 2
    assert entries[empty.id].title is None and not entries[empty.id].has_runs
    assert entries[active.id].title == " ".join(prompt[:160].split())
    assert entries[active.id].has_runs
    assert await conversations.get_session(actor=actor(), session_id=active.id) == entries[active.id]


@pytest.mark.parametrize("fork", [False, True])
@pytest.mark.parametrize("previous_limit,current_limit", [(200_000, 2_000_000), (2_000_000, 200_000)])
async def test_new_configuration_message_uses_current_budget_with_retained_history(
    agent_sessions, tmp_path, fork, previous_limit, current_limit
):
    from a13n_service.agent_configuration.requests import CreateConfigurationThreadRequest

    from ..gateway.test_commands import _complete_run

    conversations, _, _ = services(agent_sessions)
    draft = await new_draft(conversations)
    thread_id = (await conversations.get_session(actor=actor(), session_id=draft.session_id)).root_thread_id
    inputs, objects = await inputs_service(
        agent_sessions, tmp_path, definition=load_definition(total_tokens_limit=previous_limit)
    )
    request = ConfigurationInputRequest.model_validate(
        {
            "expected_thread_version": 1,
            "input": {"schema_version": "2", "content": [{"type": "text", "text": "Build an agent"}]},
        }
    )
    original = await inputs.submit(actor=actor(), thread_id=thread_id, request=request, idempotency_key="old-budget")
    await _complete_run(agent_sessions, objects, run_id=original.run_id)
    if fork:
        branch = await conversations.create_thread(
            actor=actor(),
            session_id=draft.session_id,
            request=CreateConfigurationThreadRequest(fork_from_run_id=original.run_id),
            idempotency_key="budget-fork",
        )
        thread_id = branch.thread.id
    thread = (await conversations.get_thread(actor=actor(), thread_id=thread_id)).thread
    inputs, _ = await inputs_service(
        agent_sessions, tmp_path, definition=load_definition(total_tokens_limit=current_limit)
    )
    receipt = await inputs.submit(
        actor=actor(),
        thread_id=thread_id,
        request=request.model_copy(update={"expected_thread_version": thread.version}),
        idempotency_key="new-budget",
    )
    async with short_session(agent_sessions) as session:
        source = (await session.get(RunRecord, original.run_id)).to_resource()
        run = (await session.get(RunRecord, receipt.run_id)).to_resource()
    states = RunStateStore(objects)
    parent = (await states.read_run(source)).envelope
    state = (await states.read_run(run)).envelope
    assert parent.usage_limits.total_tokens_limit == previous_limit
    assert state.usage_limits.total_tokens_limit == current_limit
    assert state.usage_limits.request_limit == 40
    assert run.execution_budget.max_usage.input_tokens == current_limit
    assert run.execution_budget.max_usage.output_tokens == current_limit
    assert state.harness.message_history == parent.harness.message_history
    assert run.parent_run_id == source.id
