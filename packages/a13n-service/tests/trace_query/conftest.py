from __future__ import annotations

import pytest
from a13n_service.trace_query.domain import TraceCorrelation
from tests.interactions.conftest import WORKSPACE_ID
from tests.interactions.conftest import interaction_object_store as interaction_object_store
from tests.interactions.conftest import interaction_sessions as interaction_sessions
from tests.interactions.worker_helpers import accepted_running_attempt


@pytest.fixture
async def trace_correlation(interaction_sessions, interaction_object_store) -> TraceCorrelation:
    run, context = await accepted_running_attempt(interaction_sessions, interaction_object_store)
    return TraceCorrelation(
        organization_id=run.organization_id,
        workspace_id=WORKSPACE_ID,
        session_id=run.session_id,
        thread_id=run.thread_id,
        run_id=run.id,
        run_attempt_id=context.run_attempt_id,
        agent_id=run.agent_id,
    )
