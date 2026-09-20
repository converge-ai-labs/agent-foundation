"""Invocation freezing excludes changes while allowing retained Run references."""

import pytest
from a13n_service.agents.invocation_resolution.queries import load_agent_record, load_revision_record
from a13n_service.agents.models import AgentRecord, AgentRevisionRecord
from a13n_service.storage import transaction
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError

from .conftest import WORKSPACE_ID
from .test_attempt_execution import _accept_root

pytestmark = pytest.mark.anyio


async def test_freezing_allows_retained_run_references_but_excludes_metadata_changes(
    interaction_sessions, interaction_object_store
):
    sessions = interaction_sessions
    _, run, _ = await _accept_root(sessions, interaction_object_store)
    selections = ((AgentRecord, run.agent_id), (AgentRevisionRecord, run.agent_revision_id))
    async with transaction(sessions) as reference:
        for model, identity in selections:
            await reference.scalar(select(model).where(model.id == identity).with_for_update(read=True, key_share=True))
        async with transaction(sessions) as freezing:
            await freezing.execute(text("SET LOCAL lock_timeout = '1s'"))
            agent = await load_agent_record(
                freezing,
                organization_id=run.organization_id,
                workspace_id=WORKSPACE_ID,
                agent_id=run.agent_id,
                for_update=True,
            )
            revision = await load_revision_record(
                freezing,
                organization_id=run.organization_id,
                workspace_id=WORKSPACE_ID,
                agent_id=run.agent_id,
                revision_id=run.agent_revision_id,
                for_update=True,
            )
            assert agent.default_revision_id == revision.id == run.agent_revision_id
            for model, identity in selections:
                with pytest.raises(OperationalError) as conflict:
                    async with transaction(sessions) as updating:
                        await updating.execute(text("SET LOCAL lock_timeout = '100ms'"))
                        await updating.scalar(select(model).where(model.id == identity).with_for_update())
                assert conflict.value.orig.sqlstate == "55P03"


async def test_independent_invocation_freezes_share_agent_and_revision_locks(
    interaction_sessions, interaction_object_store
):
    sessions = interaction_sessions
    _, run, _ = await _accept_root(sessions, interaction_object_store)

    async def freeze(session):
        await load_agent_record(
            session,
            organization_id=run.organization_id,
            workspace_id=WORKSPACE_ID,
            agent_id=run.agent_id,
            for_update=True,
        )
        await load_revision_record(
            session,
            organization_id=run.organization_id,
            workspace_id=WORKSPACE_ID,
            agent_id=run.agent_id,
            revision_id=run.agent_revision_id,
            for_update=True,
        )

    async with transaction(sessions) as first:
        await freeze(first)
        async with transaction(sessions) as second:
            await second.execute(text("SET LOCAL lock_timeout = '100ms'"))
            await freeze(second)
