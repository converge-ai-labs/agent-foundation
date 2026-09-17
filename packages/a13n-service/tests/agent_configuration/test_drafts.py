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
from a13n_service.agents.domain import CreateAgentRequest, CreateAgentRevisionRequest, SetDefaultAgentRevisionRequest
from a13n_service.agents.models import AgentRecord, AgentRevisionRecord
from a13n_service.agents.resolution import AgentResolver
from a13n_service.application_errors import ApplicationError
from a13n_service.durable_operations.models import IdempotencyEvidenceRecord
from a13n_service.etags import resource_etag
from a13n_service.iam.models import SecurityAuditRecord
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
    return thread.draft


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
    continued = await drafts.get(actor=actor(), draft_id=saved.id)
    assert continued.status == "open" and continued.version == saved.version + 1
    assert continued.mode == "update" and continued.target_agent_id == receipt.agent_id
    assert continued.base_agent_revision_id == receipt.agent_revision_id
    assert continued.source_agent_revision_id is None
    assert continued.creation_metadata == saved.creation_metadata
    assert continued.latest_validation is None
    conversation = await conversations.get_session(actor=actor(), session_id=draft.session_id)
    assert conversation.configuration_draft_id == draft.id
    assert (await conversations.get_thread(actor=actor(), thread_id=conversation.root_thread_id)).draft == continued
    edited = await save(drafts, continued, text="Continue editing", key="continue")
    assert edited.version == continued.version + 1
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
    history = await applications.list_applications(actor=actor(), draft_id=draft.id, limit=10, cursor=None)
    assert history.items == (receipt,) and history.next_cursor is None
    assert await drafts.get(actor=actor(), draft_id=draft.id) == edited


async def test_reviewed_summary_overrides_assistant_suggestion(agent_sessions) -> None:
    conversations, drafts, applications = services(agent_sessions)
    saved = await save(drafts, await new_draft(conversations))
    suggested = await drafts.update(
        actor=actor(),
        draft_id=saved.id,
        idempotency_key="suggest-summary",
        request=UpdateConfigurationDraftRequest(
            expected_version=saved.version, suggested_change_summary="Assistant suggestion"
        ),
        if_match=resource_etag(saved.id, saved.updated_at),
    )
    assert suggested.suggested_change_summary == "Assistant suggestion"
    from a13n_service.agent_configuration.runtime import model_draft

    assert model_draft(suggested)["suggested_change_summary"] == "Assistant suggestion"
    request = apply_request(suggested).model_copy(update={"change_summary": "Human wording"})
    receipt = await applications.apply(
        actor=actor(),
        draft_id=suggested.id,
        request=request,
        idempotency_key="apply-summary",
        if_match=resource_etag(suggested.id, suggested.updated_at),
    )
    async with short_session(agent_sessions) as session:
        revision = await session.get(AgentRevisionRecord, receipt.agent_revision_id)
    assert revision.change_summary == "Human wording"


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
        request=CreateAgentRevisionRequest(config=agent_config(instructions="External edit")),
        if_match=resource_etag(created.agent.id, created.agent.updated_at),
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
    with pytest.raises(ApplicationError, match="target changed after review"):
        await drafts.rebase(
            actor=actor(),
            draft_id=saved.id,
            request=RebaseDraftRequest(
                expected_version=saved.version,
                expected_target_etag=resource_etag(created.agent.id, created.agent.updated_at),
                config=agent_config(instructions="Stale merged edit"),
            ),
            idempotency_key="stale-rebase",
            if_match=resource_etag(saved.id, saved.updated_at),
        )
    assert await drafts.get(actor=actor(), draft_id=saved.id) == saved
    rebased = await drafts.rebase(
        actor=actor(),
        draft_id=saved.id,
        request=RebaseDraftRequest(
            expected_version=saved.version,
            expected_target_etag=resource_etag(advanced.agent.id, advanced.agent.updated_at),
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
    assert receipt.agent_revision_version == 3
    async with short_session(agent_sessions) as session:
        audit = await session.scalar(
            select(SecurityAuditRecord).where(
                SecurityAuditRecord.action == "agent.configuration.apply",
                SecurityAuditRecord.resource_id == created.agent.id,
            )
        )
    assert audit.details == {
        "from_revision_id": advanced.revision.id,
        "to_revision_id": receipt.agent_revision_id,
    }


async def test_default_switch_aba_invalidates_assistant_baseline(agent_sessions, agent_management) -> None:
    created = await agent_management.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="aba-target",
        request=CreateAgentRequest(name="ABA target", config=agent_config()),
    )
    advanced = await agent_management.revisions.create_revision(
        actor=actor(),
        agent_id=created.agent.id,
        idempotency_key="aba-v2",
        request=CreateAgentRevisionRequest(config=agent_config(instructions="v2")),
        if_match=resource_etag(created.agent.id, created.agent.updated_at),
    )
    conversations, drafts, applications = services(agent_sessions)
    saved = await save(drafts, await new_draft(conversations, target=created.agent.id))
    older = await agent_management.revisions.set_default_revision(
        actor=actor(),
        agent_id=created.agent.id,
        revision_id=created.revision.id,
        idempotency_key="aba-older",
        request=SetDefaultAgentRevisionRequest(),
        if_match=resource_etag(advanced.agent.id, advanced.agent.updated_at),
    )
    restored = await agent_management.revisions.set_default_revision(
        actor=actor(),
        agent_id=created.agent.id,
        revision_id=advanced.revision.id,
        idempotency_key="aba-newer",
        request=SetDefaultAgentRevisionRequest(),
        if_match=resource_etag(older.agent.id, older.agent.updated_at),
    )
    assert restored.agent.default_revision_id == saved.base_agent_revision_id
    with pytest.raises(ApplicationError, match="target has changed"):
        await applications.apply(
            actor=actor(),
            draft_id=saved.id,
            request=apply_request(saved),
            idempotency_key="aba-apply",
            if_match=resource_etag(saved.id, saved.updated_at),
        )


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


async def test_application_history_survives_later_versions_and_no_change_application(agent_sessions):
    from a13n_service.agent_configuration.review import ConfigurationReviews

    conversations, drafts, applications = services(agent_sessions)
    original = await save(drafts, await new_draft(conversations))
    receipts = []
    saved = original
    for index in range(3):
        receipt = await applications.apply(
            actor=actor(),
            draft_id=saved.id,
            request=apply_request(saved),
            idempotency_key=f"apply-{index}",
            if_match=resource_etag(saved.id, saved.updated_at),
        )
        receipts.append(receipt)
        continued = await drafts.get(actor=actor(), draft_id=saved.id)
        assert continued.id == original.id and continued.version == saved.version + 1
        assert continued.status == "open" and continued.source_agent_revision_id is None
        assert continued.creation_metadata == original.creation_metadata
        assert continued.base_agent_revision_id == receipt.agent_revision_id
        assert continued.latest_validation is None
        saved = await save(drafts, continued, text="Candidate", key=f"validate-{index}")
    assert [item.no_change for item in receipts] == [False, True, True]
    assert len({item.agent_revision_id for item in receipts}) == 1
    assert receipts[0].reviewed_mode == "create" and receipts[0].reviewed_target_agent_id is None
    assert receipts[1].reviewed_mode == "update" and receipts[1].reviewed_target_agent_id == receipts[0].agent_id
    page = await applications.list_applications(actor=actor(), draft_id=original.id, limit=2, cursor=None)
    assert page.items == tuple(reversed(receipts[1:])) and page.next_cursor is not None
    tail = await applications.list_applications(actor=actor(), draft_id=original.id, limit=2, cursor=page.next_cursor)
    assert tail.items == (receipts[0],) and tail.next_cursor is None
    review = await ConfigurationReviews(agent_sessions).get(actor=actor(), draft_id=original.id)
    assert review.latest_application_receipt == receipts[-1]
    async with transaction(agent_sessions) as session:
        await session.execute(delete(IdempotencyEvidenceRecord))
    assert (
        await applications.apply(
            actor=actor(),
            draft_id=original.id,
            request=apply_request(original),
            idempotency_key="apply-0",
            if_match=resource_etag(original.id, original.updated_at),
        )
        == receipts[0]
    )
    # An old reviewed version must not mask a key used by a different application.
    with pytest.raises(ApplicationError, match="Idempotency-Key"):
        await applications.apply(
            actor=actor(),
            draft_id=original.id,
            request=apply_request(original),
            idempotency_key="apply-1",
            if_match=resource_etag(original.id, original.updated_at),
        )
    assert await drafts.get(actor=actor(), draft_id=original.id) == saved


async def test_semantic_replay_binds_its_new_idempotency_key(agent_sessions):
    conversations, drafts, applications = services(agent_sessions)
    saved = await save(drafts, await new_draft(conversations))
    request = apply_request(saved)

    async def apply(request, key):
        return await applications.apply(
            actor=actor(),
            draft_id=saved.id,
            request=request,
            idempotency_key=key,
            if_match=resource_etag(saved.id, saved.updated_at),
        )

    receipt = await apply(request, "original")
    assert await apply(request, "replay-alias") == receipt
    current = await drafts.get(actor=actor(), draft_id=saved.id)
    validated = await save(drafts, current, key="validate")
    with pytest.raises(ApplicationError, match="Idempotency-Key"):
        await apply(apply_request(validated), "replay-alias")


@pytest.mark.parametrize("mutation", ["ordinary_revision", "update_baseline", "source_version"])
async def test_database_rejects_missing_required_revision_and_baseline_fields(
    agent_sessions, agent_management, mutation
):
    from a13n_service.agent_configuration.models import ConfigurationDraftRecord
    from sqlalchemy.exc import IntegrityError

    created = await agent_management.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="business",
        request=CreateAgentRequest(name="Business", config=agent_config()),
    )
    conversations, _, _ = services(agent_sessions)
    draft = await new_draft(conversations, target=created.agent.id)
    with pytest.raises(IntegrityError):
        async with transaction(agent_sessions) as session:
            if mutation == "ordinary_revision":
                record = await session.get(AgentRecord, created.agent.id)
                record.default_revision_id = None
            else:
                record = await session.get(ConfigurationDraftRecord, draft.id)
                if mutation == "update_baseline":
                    record.base_agent_etag = None
                else:
                    record.source_agent_revision_version = None
            await session.flush()
