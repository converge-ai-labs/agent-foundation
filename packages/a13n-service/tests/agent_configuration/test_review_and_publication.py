from dataclasses import replace
from datetime import timedelta
from typing import Literal
from unittest.mock import patch

import pytest
from a13n_service.agent_configuration.models import ConfigurationApplicationRecord
from a13n_service.agent_configuration.requests import CreateSessionRequest, SourceSelection
from a13n_service.agent_configuration.review import ConfigurationReviews
from a13n_service.agents.domain import CreateAgentRequest, CreateAgentRevisionRequest
from a13n_service.agents.models import AgentRecord, AgentRevisionRecord
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
        request=CreateAgentRevisionRequest(config=agent_config(instructions="Current")),
        if_match=resource_etag(original.agent.id, original.agent.updated_at),
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
    draft = (await conversations.get_thread(actor=actor(), thread_id=conversation.root_thread_id)).draft
    review = await ConfigurationReviews(agent_sessions).get(actor=actor(), draft_id=draft.id)
    assert review.source_to_candidate == review.base_to_candidate == ()
    assert not review.target_conflict and review.base.version == 1 and review.current_target.version == 2
    change = next(item for item in review.current_target_to_candidate if item.path == ("instructions",))
    assert (change.before, change.after) == ("Current", "Historical")


@pytest.mark.parametrize("mode", ["create", "update", "no_change"])
async def test_apply_failure_rolls_back_target_receipt_and_publication(
    agent_sessions, agent_management, mode: Literal["create", "update", "no_change"]
):
    original = None
    if mode != "create":
        original = await agent_management.commands.create(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            idempotency_key="original",
            request=CreateAgentRequest(
                name="Target", config=agent_config(instructions="Candidate" if mode == "no_change" else "Original")
            ),
        )
    conversations, drafts, applications = services(agent_sessions)
    draft = await save(drafts, await new_draft(conversations, target=original.agent.id if original else None))
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
        assert await session.scalar(select(func.count()).select_from(AgentRecord)) == (1 if original else 0)
        assert await session.scalar(select(func.count()).select_from(AgentRevisionRecord)) == (1 if original else 0)
        assert await session.scalar(select(func.count()).select_from(ConfigurationApplicationRecord)) == 0
        assert await session.scalar(select(func.count()).select_from(OutboxRecord)) == 0
        if original is not None:
            target = await session.get(AgentRecord, original.agent.id)
            assert target is not None and target.to_resource() == original.agent
    receipt = await applications.apply(
        actor=actor(),
        draft_id=draft.id,
        request=apply_request(draft),
        idempotency_key="atomic",
        if_match=resource_etag(draft.id, draft.updated_at),
    )
    assert receipt.no_change == (mode == "no_change")
    assert receipt.agent_revision_version == (2 if mode == "update" else 1)
    applied = await drafts.get(actor=actor(), draft_id=draft.id)
    assert applied.version == draft.version + 1 and applied.base_agent_revision_id == receipt.agent_revision_id
    async with short_session(agent_sessions) as session:
        revision = await session.get(AgentRevisionRecord, receipt.agent_revision_id)
        assert revision is not None
        assert await session.scalar(select(func.count()).select_from(AgentRevisionRecord)) == (
            2 if mode == "update" else 1
        )
        if original is not None:
            target = await session.get(AgentRecord, original.agent.id)
            assert target is not None and target.default_revision_id == receipt.agent_revision_id
            if mode == "no_change":
                assert target.to_resource() == original.agent
            else:
                assert target.updated_at > original.agent.updated_at
                assert revision.source_revision_id == original.revision.id
    notifications = NotificationService(agent_sessions)
    (subscription,) = await notifications.authorize(
        actor=actor(),
        subscriptions=(
            NotificationSubscription(
                subscription_id="draft",
                scope="thread",
                resource_id=(
                    await conversations.get_session(actor=actor(), session_id=draft.session_id)
                ).root_thread_id,
                topics=("thread.updated",),
            ),
        ),
    )
    subscription = replace(subscription, after_application=(NOW - timedelta(seconds=1), ""))
    hints = await notifications.read_applications(subscription, limit=10)
    assert (
        len(hints) == 1
        and hints[0].thread_id
        == (await conversations.get_session(actor=actor(), session_id=draft.session_id)).root_thread_id
        and hints[0].run_id is None
    )
    await notifications.acknowledge_application(hints[0])
    async with short_session(agent_sessions) as session:
        intent = await session.scalar(select(OutboxRecord))
        assert intent.status == "published" and intent.source_id.startswith("capply_")
    # Publication is a best-effort hint; another subscriber can still observe it.
    assert await notifications.read_applications(subscription, limit=10) == hints
