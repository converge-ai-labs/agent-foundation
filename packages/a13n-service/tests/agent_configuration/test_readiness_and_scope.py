from dataclasses import replace

import pytest
from a13n_service.agent_configuration.definition import load_definition
from a13n_service.agent_configuration.knowledge import KnowledgeFiles
from a13n_service.agent_configuration.readiness import ConfigurationReadiness
from a13n_service.agent_configuration.system_agent import SystemConfigurationAgent
from a13n_service.agents.models import AgentRevisionRecord
from a13n_service.application_errors import ApplicationError
from a13n_service.gateway.queries import NativeInteractionQueries
from a13n_service.iam import AuthorizationError, PrincipalRef
from a13n_service.iam.models import RoleBindingRecord, UserRecord
from a13n_service.models.models import ModelProviderRecord, ModelRecord
from a13n_service.models.providers import built_in_provider_registry
from a13n_service.run_stream import RunDisplayStore
from a13n_service.storage import short_session, transaction
from sqlalchemy import func, select

from ..agents.conftest import MODEL_ID, NOW, ORG_ID, PROVIDER_ID, USER_ID, WORKSPACE_ID, actor
from .test_drafts import new_draft, services

pytestmark = pytest.mark.anyio


async def test_readiness_never_initializes_resources_and_exact_system_definition_is_hidden(
    agent_sessions, agent_management
):
    definition = load_definition()
    readiness = ConfigurationReadiness(agent_sessions, built_in_provider_registry(), definition)
    assert (await readiness.read(actor=actor())).reason_code == "provider_setup_required"
    async with transaction(agent_sessions) as session:
        provider = await session.get(ModelProviderRecord, PROVIDER_ID)
        provider.credential_configured = True
        model = await session.get(ModelRecord, MODEL_ID)
        model.declarations = {**model.declarations, "supports_tools": True}
    ready = await readiness.read(actor=actor())
    assert ready.ready and ready.selected_model.selection_reason == "compatible_available_model"
    conversations, _, _ = services(agent_sessions)
    draft = await new_draft(conversations)
    system = SystemConfigurationAgent(
        agent_sessions,
        definition,
        clock=lambda: NOW,
    )
    created = await system.ensure(actor=actor(), session_id=draft.session_id)
    replay = await system.ensure(actor=actor(), session_id=draft.session_id)
    assert created == replay
    assert created.system_purpose == "configuration_assistant"
    assert created.source.value == "builtin"
    assert (
        await agent_management.queries.list(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            limit=10,
            cursor=None,
            enabled=None,
            source=None,
            include_archived=False,
        )
    ).items == ()
    with pytest.raises((ApplicationError, AuthorizationError)):
        await agent_management.queries.get(actor=actor(), agent_id=created.id)
    assert created.default_revision_id is None
    assert KnowledgeFiles().validate().is_dir()
    changed = definition.model_copy(update={"instructions": definition.instructions + "\nBe concise."})
    advanced = await SystemConfigurationAgent(agent_sessions, changed).ensure(
        actor=actor(), session_id=draft.session_id
    )
    assert advanced == created
    async with short_session(agent_sessions) as session:
        assert await session.scalar(select(func.count()).select_from(AgentRevisionRecord)) == 0


async def test_readiness_routes_missing_models_to_models_after_provider_setup(agent_sessions):
    readiness = ConfigurationReadiness(agent_sessions, built_in_provider_registry(), load_definition())
    missing_provider = await readiness.read(actor=actor())
    assert "/settings?" in missing_provider.setup_url
    async with transaction(agent_sessions) as session:
        provider = await session.get(ModelProviderRecord, PROVIDER_ID)
        provider.credential_configured = True
        model = await session.get(ModelRecord, MODEL_ID)
        model.enabled = False
    missing_model = await readiness.read(actor=actor())
    assert missing_model.reason_code == "model_setup_required"
    assert missing_model.setup_url.endswith("/models")
    assert missing_model.setup_actions == ("configure_model",)


async def test_empty_configuration_conversation_is_visible_only_to_owner_even_for_admin(agent_sessions, tmp_path):
    from a13n_service.storage.object_store.local import LocalObjectStore

    conversations, _, _ = services(agent_sessions)
    draft = await new_draft(conversations)
    other_id = "usr_otheradmin123456"
    async with transaction(agent_sessions) as session:
        session.add(
            UserRecord(
                id=other_id,
                email="other@example.com",
                normalized_email="other@example.com",
                name="Other",
                status="active",
                email_verified_at=NOW,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await session.flush()
        original = tuple(
            await session.scalars(select(RoleBindingRecord).where(RoleBindingRecord.principal_id == USER_ID))
        )
        for i, binding in enumerate(original):
            session.add(
                RoleBindingRecord(
                    id=f"rb_otheradmin12345{i}",
                    organization_id=ORG_ID,
                    workspace_id=binding.workspace_id,
                    principal_type="user",
                    principal_id=other_id,
                    resource_type=binding.resource_type,
                    resource_id=binding.resource_id,
                    role_key="admin",
                    created_by_user_id=USER_ID,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
    other = replace(actor(), principal=PrincipalRef(principal_type="user", principal_id=other_id))
    queries = NativeInteractionQueries(agent_sessions, RunDisplayStore(LocalObjectStore(tmp_path / "objects")))
    assert (
        await queries.get_thread(
            actor=actor(),
            thread_id=(await conversations.get_session(actor=actor(), session_id=draft.session_id)).root_thread_id,
        )
    ).id == (await conversations.get_session(actor=actor(), session_id=draft.session_id)).root_thread_id
    assert (
        len((await queries.list_sessions(actor=actor(), workspace_id=WORKSPACE_ID, limit=10, cursor=None)).items) == 1
    )
    assert (await queries.list_sessions(actor=other, workspace_id=WORKSPACE_ID, limit=10, cursor=None)).items == ()
    with pytest.raises((ApplicationError, AuthorizationError)):
        await queries.get_thread(
            actor=other,
            thread_id=(await conversations.get_session(actor=actor(), session_id=draft.session_id)).root_thread_id,
        )
    with pytest.raises((ApplicationError, AuthorizationError)):
        await queries.list_threads(actor=other, session_id=draft.session_id, limit=10, cursor=None)
