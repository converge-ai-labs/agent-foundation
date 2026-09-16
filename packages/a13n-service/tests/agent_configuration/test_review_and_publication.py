from dataclasses import replace
from datetime import timedelta
from unittest.mock import patch

import pytest
from a13n_service.agent_configuration.requests import CreateSessionRequest, SourceSelection
from a13n_service.agent_configuration.review import ConfigurationReviews
from a13n_service.agents.domain import CreateAgentRequest, CreateAgentRevisionRequest
from a13n_service.agents.models import AgentRecord
from a13n_service.durable_operations.models import OutboxRecord
from a13n_service.etags import resource_etag
from a13n_service.gateway.notifications import NotificationService, NotificationSubscription
from a13n_service.storage import short_session
from sqlalchemy import func, select

from ..agents.conftest import NOW, WORKSPACE_ID, actor, agent_config
from .test_drafts import apply_request, new_draft, save, services

pytestmark = pytest.mark.anyio


async def test_historical_source_review_uses_current_target_for_application(agent_sessions, agent_management):
    original = await agent_management.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="original",
        request=CreateAgentRequest(name="Target", config=agent_config(instructions="Historical")),
    )
    await agent_management.revisions.create_revision(
        actor=actor(),
        agent_id=original.agent.id,
        idempotency_key="advanced",
        request=CreateAgentRevisionRequest(expected_version=1, config=agent_config(instructions="Current")),
    )
    conversations, _, _ = services(agent_sessions)
    conversation = await conversations.create_session(
        actor=actor(),
        idempotency_key="historical",
        request=CreateSessionRequest(
            target_agent_id=original.agent.id,
            source=SourceSelection(selector="explicit", revision_id=original.revision.id),
        ),
    )
    draft = (await conversations.get_thread(actor=actor(), thread_id=conversation.root_thread_id)).latest_draft
    review = await ConfigurationReviews(agent_sessions).get(actor=actor(), draft_id=draft.id)
    assert review.source_to_candidate == review.base_to_candidate == ()
    assert not review.target_conflict and review.base_agent_version == 2
    change = next(item for item in review.current_target_to_candidate if item.path == ("instructions",))
    assert (change.before, change.after) == ("Current", "Historical")


async def test_apply_failure_rolls_back_target_receipt_and_publication(agent_sessions):
    conversations, drafts, applications = services(agent_sessions)
    draft = await save(drafts, await new_draft(conversations))
    with patch(
        "a13n_service.agent_configuration.application.audit_draft", side_effect=RuntimeError("injected failure")
    ):
        with pytest.raises(RuntimeError, match="injected failure"):
            await applications.apply(
                actor=actor(),
                draft_id=draft.id,
                request=apply_request(draft),
                idempotency_key="atomic",
                if_match=resource_etag(draft.id, draft.updated_at),
            )
    assert await drafts.get(actor=actor(), draft_id=draft.id) == draft
    async with short_session(agent_sessions) as session:
        assert await session.scalar(select(func.count()).select_from(AgentRecord)) == 0
        assert await session.scalar(select(func.count()).select_from(OutboxRecord)) == 0
    await applications.apply(
        actor=actor(),
        draft_id=draft.id,
        request=apply_request(draft),
        idempotency_key="atomic",
        if_match=resource_etag(draft.id, draft.updated_at),
    )
    notifications = NotificationService(agent_sessions)
    (subscription,) = await notifications.authorize(
        actor=actor(),
        subscriptions=(
            NotificationSubscription(
                subscription_id="draft", scope="thread", resource_id=draft.thread_id, topics=("thread.updated",)
            ),
        ),
    )
    subscription = replace(subscription, after_application=(NOW - timedelta(seconds=1), ""))
    hints = await notifications.read_applications(subscription, limit=10)
    assert len(hints) == 1 and hints[0].thread_id == draft.thread_id and hints[0].run_id is None
    await notifications.acknowledge_application(hints[0])
    async with short_session(agent_sessions) as session:
        intent = await session.scalar(select(OutboxRecord))
        assert intent.status == "published" and intent.source_id == draft.id
    # Publication is a best-effort hint; another subscriber can still observe it.
    assert await notifications.read_applications(subscription, limit=10) == hints
