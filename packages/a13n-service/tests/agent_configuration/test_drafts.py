from __future__ import annotations

import pytest
from a13n_service.agent_configuration.application import ConfigurationApplication
from a13n_service.agent_configuration.conversations import ConfigurationConversations
from a13n_service.agent_configuration.drafts import ConfigurationDrafts
from a13n_service.agent_configuration.requests import (
    ApplyDraftRequest,
    CreateSessionRequest,
    RebaseDraftRequest,
    UpdateConfigurationDraftRequest,
)
from a13n_service.agents.domain import CreateAgentRequest, CreateAgentRevisionRequest
from a13n_service.agents.models import AgentRecord
from a13n_service.agents.resolution import AgentResolver
from a13n_service.application_errors import ApplicationError
from a13n_service.durable_operations.models import IdempotencyEvidenceRecord
from a13n_service.etags import resource_etag
from a13n_service.models.providers import built_in_provider_registry
from a13n_service.models.runtime import AcceptedModelSelector
from a13n_service.storage import short_session, transaction
from sqlalchemy import delete, func, select

from ..agents.conftest import NOW, WORKSPACE_ID, actor, agent_config

pytestmark = pytest.mark.anyio


def services(sessions):
    resolver = AgentResolver(sessions, AcceptedModelSelector(sessions, built_in_provider_registry()))
    return (
        ConfigurationConversations(sessions, clock=lambda: NOW),
        ConfigurationDrafts(sessions, resolver, clock=lambda: NOW),
        ConfigurationApplication(sessions, resolver, clock=lambda: NOW),
    )


async def new_draft(conversations, *, target=None):
    conversation = await conversations.create_session(
        actor=actor(), request=CreateSessionRequest(target_agent_id=target), idempotency_key="new-session"
    )
    thread = await conversations.get_thread(actor=actor(), thread_id=conversation.root_thread_id)
    return thread.latest_draft


async def save(drafts, draft, *, text="Candidate", key="save"):
    request = {
        "expected_version": draft.version,
        "operations": [{"op": "set", "path": [], "value": agent_config(instructions=text).model_dump(mode="json")}],
    }
    if draft.mode == "create":
        request["creation_metadata"] = {"name": "Created Agent", "description": "From a reviewed draft"}
    return await drafts.update(
        actor=actor(),
        draft_id=draft.id,
        request=UpdateConfigurationDraftRequest.model_validate(request),
        idempotency_key=key,
        if_match=resource_etag(draft.id, draft.updated_at),
    )


def apply_request(draft, *, acknowledge=True):
    return ApplyDraftRequest.model_validate(
        {
            "expected_version": draft.version,
            "content_digest": draft.content_digest,
            "expected_target_version": draft.base_agent_version,
            "dependency_digest": draft.latest_validation.dependency_digest,
            "verification_acknowledgement": {"outcome": "unverified", "reason": "Reviewed for a limited trial."}
            if acknowledge
            else None,
        }
    )


async def test_save_is_not_publication_and_apply_is_atomic_replayable(agent_sessions) -> None:
    conversations, drafts, applications = services(agent_sessions)
    draft = await new_draft(conversations)
    assert draft.config is None and draft.version == 1
    saved = await save(drafts, draft)
    assert saved.version == 2
    async with short_session(agent_sessions) as session:
        assert await session.scalar(select(func.count()).select_from(AgentRecord)) == 0
    with pytest.raises(ApplicationError, match="acknowledge"):
        await applications.apply(
            actor=actor(),
            draft_id=saved.id,
            request=apply_request(saved, acknowledge=False),
            idempotency_key="not-approved",
            if_match=resource_etag(saved.id, saved.updated_at),
        )
    receipt = await applications.apply(
        actor=actor(),
        draft_id=saved.id,
        request=apply_request(saved),
        idempotency_key="apply",
        if_match=resource_etag(saved.id, saved.updated_at),
    )
    terminal = await drafts.get(actor=actor(), draft_id=saved.id)
    assert terminal.status == "applied" and terminal.version == saved.version
    assert terminal.target_agent_id is None and terminal.application_receipt == receipt
    conversation = await conversations.get_session(actor=actor(), session_id=draft.session_id)
    assert conversation.target_agent_id == receipt.agent_id
    assert (await conversations.get_thread(actor=actor(), thread_id=draft.thread_id)).active_draft_id is None
    async with transaction(agent_sessions) as session:
        await session.execute(delete(IdempotencyEvidenceRecord))
    assert (
        await applications.apply(
            actor=actor(),
            draft_id=saved.id,
            request=apply_request(saved),
            idempotency_key="apply",
            if_match=resource_etag(saved.id, saved.updated_at),
        )
        == receipt
    )
    async with short_session(agent_sessions) as session:
        assert await session.scalar(select(func.count()).select_from(AgentRecord)) == 1
    with pytest.raises(ApplicationError, match="no longer editable"):
        await save(drafts, terminal, text="Late write", key="late")


async def test_conflict_preserves_candidate_and_rebase_preserves_original_source(
    agent_sessions, agent_management
) -> None:
    created = await agent_management.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="business",
        request=CreateAgentRequest(name="Business", config=agent_config()),
    )
    conversations, drafts, applications = services(agent_sessions)
    draft = await new_draft(conversations, target=created.agent.id)
    saved = await save(drafts, draft)
    advanced = await agent_management.revisions.create_revision(
        actor=actor(),
        agent_id=created.agent.id,
        idempotency_key="external-edit",
        request=CreateAgentRevisionRequest(expected_version=1, config=agent_config(instructions="External edit")),
    )
    with pytest.raises(ApplicationError, match="target has changed"):
        await applications.apply(
            actor=actor(),
            draft_id=saved.id,
            request=apply_request(saved),
            idempotency_key="conflict",
            if_match=resource_etag(saved.id, saved.updated_at),
        )
    assert await drafts.get(actor=actor(), draft_id=saved.id) == saved
    rebased = await drafts.rebase(
        actor=actor(),
        draft_id=saved.id,
        request=RebaseDraftRequest(
            expected_version=saved.version,
            expected_target_version=advanced.agent.version,
            config=agent_config(instructions="Merged edit"),
        ),
        idempotency_key="rebase",
        if_match=resource_etag(saved.id, saved.updated_at),
    )
    assert rebased.source_agent_revision_id == draft.source_agent_revision_id
    assert rebased.base_agent_revision_id == advanced.revision.id
    assert rebased.version == saved.version + 1
    receipt = await applications.apply(
        actor=actor(),
        draft_id=rebased.id,
        request=apply_request(rebased),
        idempotency_key="apply-merged",
        if_match=resource_etag(rebased.id, rebased.updated_at),
    )
    assert receipt.agent_version == 3


async def test_failed_dependency_resolution_preserves_saved_validation(agent_sessions) -> None:
    conversations, drafts, _ = services(agent_sessions)
    saved = await save(drafts, await new_draft(conversations))
    with pytest.raises(ApplicationError):
        await drafts.update(
            actor=actor(),
            draft_id=saved.id,
            request=UpdateConfigurationDraftRequest.model_validate(
                {
                    "expected_version": saved.version,
                    "operations": [{"op": "set", "path": ["model", "model_key"], "value": "does-not-exist"}],
                }
            ),
            idempotency_key="invalid-model",
            if_match=resource_etag(saved.id, saved.updated_at),
        )
    assert await drafts.get(actor=actor(), draft_id=saved.id) == saved


async def test_noop_save_keeps_candidate_version(agent_sessions) -> None:
    conversations, drafts, _ = services(agent_sessions)
    saved = await save(drafts, await new_draft(conversations))
    again = await save(drafts, saved, key="no-op")
    assert again.version == saved.version and again.content_digest == saved.content_digest
