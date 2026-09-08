"""Bounded Task history projection and constant SQL query counts."""

from __future__ import annotations

from datetime import timedelta

import pytest
from a13n_service.gateway.models import A2AMessageBindingRecord
from a13n_service.storage import transaction
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.hooks.support import seed_hook_actor_access
from tests.interactions.conftest import AGENT_ID, NOW

from .test_a2a import _request, _service
from .test_commands import _actor

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("history_length", [0, 1, None])
async def test_task_list_history_query_count_is_constant(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    tmp_path,
    history_length: int | None,
) -> None:
    from tests.sql_capture import capture_sql

    await seed_hook_actor_access(lifecycle_interaction_sessions)
    service, _objects = await _service(lifecycle_interaction_sessions, tmp_path)
    expected_histories: dict[str, list[str]] = {}
    for index in range(4):
        task = await service.send(actor=_actor(), agent_id=AGENT_ID, request=_request(message_id=f"query-{index}"))
        expected_histories[task.id] = [f"query-{index}"]
        async with transaction(lifecycle_interaction_sessions) as database:
            original = await database.scalar(
                select(A2AMessageBindingRecord).where(A2AMessageBindingRecord.task_id == task.id)
            )
            assert original is not None
            for extra in range(index):
                message_id = f"history-{index}-{extra}"
                expected_histories[task.id].append(message_id)
                database.add(
                    A2AMessageBindingRecord(
                        id=f"a2amsg_{message_id}",
                        organization_id=original.organization_id,
                        workspace_id=original.workspace_id,
                        client_principal_type=original.client_principal_type,
                        client_principal_id=original.client_principal_id,
                        agent_id=original.agent_id,
                        task_id=task.id,
                        message_id=message_id,
                        request_digest_sha256="a" * 64,
                        request_json={
                            "message": {"messageId": message_id, "role": "ROLE_USER", "parts": [{"text": message_id}]}
                        },
                        run_id=original.run_id,
                        created_at=NOW + timedelta(seconds=1),
                    )
                )
    counts = []
    for page_size in (1, 4):
        with capture_sql(lifecycle_interaction_sessions) as statements:
            page = await service.list_tasks(
                actor=_actor(),
                agent_id=AGENT_ID,
                context_id=None,
                status=None,
                page_size=page_size,
                page_token=None,
                history_length=history_length,
                status_timestamp_after=None,
                include_artifacts=False,
            )
        assert len(page.tasks) == page_size
        history_queries = [sql for sql in statements if "a2a_message_bindings" in sql]
        assert len(history_queries) == (0 if history_length == 0 else 1)
        for task in page.tasks:
            expected = expected_histories[task.id]
            if history_length == 0:
                expected = []
            elif history_length is not None:
                expected = expected[-history_length:]
            assert [message.message_id for message in task.history] == expected
        counts.append(len(statements))
    assert counts[0] == counts[1]
