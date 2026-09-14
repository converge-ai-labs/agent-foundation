from datetime import timedelta

import pytest
from a13n_service.gateway.queries import NativeInteractionQueries, NativeQueryError
from a13n_service.gateway.session_queries import SessionFilters
from a13n_service.iam import WorkspaceAction
from a13n_service.iam import authorization as iam_authorization
from a13n_service.iam.models import RoleBindingRecord
from a13n_service.interactions.models import RunRecord
from a13n_service.storage import transaction
from pydantic import ValidationError
from sqlalchemy import delete

from tests.hooks.support import hook_actor
from tests.interactions.conftest import AGENT_ID, NOW, SESSION_ID, THREAD_ID, WORKSPACE_ID

from .test_session_previews import _add_hidden_agent, _add_session, _add_thread, _grant_agent_viewer
from .test_session_previews import preview_queries as preview_queries

pytestmark = pytest.mark.anyio


async def _list(queries: NativeInteractionQueries, **filters):
    return await queries.list_sessions(
        actor=hook_actor(), workspace_id=WORKSPACE_ID, limit=20, cursor=None, filters=SessionFilters(**filters)
    )


@pytest.mark.parametrize("q", [SESSION_ID, THREAD_ID, f"  {SESSION_ID}  "])
async def test_search_resolves_exact_session_or_thread(preview_queries, q):
    page = await _list(preview_queries, q=q)
    assert [item.id for item in page.items] == [SESSION_ID]


@pytest.mark.parametrize("q", [SESSION_ID[:6], "missing", "%", "_"])
async def test_search_never_uses_partial_or_wildcard_matching(preview_queries, q):
    assert not (await _list(preview_queries, q=q)).items


async def test_run_filters_match_selected_run_before_pagination(preview_queries, lifecycle_interaction_sessions):
    async with transaction(lifecycle_interaction_sessions) as database:
        _, run_id = await _add_thread(database, "failed", updated_seconds=2, trigger_type="inbound")
        await database.flush()
        run = await database.get(RunRecord, run_id)
        assert run is not None
        run.status = "failed"
        run.sealed_at = NOW
        run.failure_json = {"code": "test_failure", "message": "Test failure."}
        for index in range(3):
            session_id = _add_session(database, f"newer_{index}", updated_seconds=10 + index)
            await _add_thread(database, f"newer_{index}", session_id=session_id)
    filters = SessionFilters(agent_id=AGENT_ID, status=("failed", "cancelled"), trigger_type=("inbound", "feedback"))
    page = await preview_queries.list_sessions(
        actor=hook_actor(), workspace_id=WORKSPACE_ID, limit=1, cursor=None, filters=filters
    )
    assert [item.id for item in page.items] == [SESSION_ID]
    assert page.next_cursor is None
    assert page.items[0].preview.run_id == run_id
    assert not (await _list(preview_queries, q=SESSION_ID, status=("accepted",))).items
    assert not (await _list(preview_queries, q=SESSION_ID, trigger_type=("user_input",))).items


async def test_filter_does_not_fall_back_from_latest_empty_thread(preview_queries, lifecycle_interaction_sessions):
    async with transaction(lifecycle_interaction_sessions) as database:
        await _add_thread(database, "empty", empty=True)
    assert not (await _list(preview_queries, status=("accepted",))).items


async def test_time_range_and_cursor_are_bound_to_filters(preview_queries, lifecycle_interaction_sessions):
    async with transaction(lifecycle_interaction_sessions) as database:
        for index in range(4):
            session_id = _add_session(database, f"range_{index}", updated_seconds=index + 1)
            await _add_thread(database, f"range_{index}", session_id=session_id)
    filters = SessionFilters(updated_after=NOW + timedelta(seconds=2), updated_before=NOW + timedelta(seconds=4))
    page = await preview_queries.list_sessions(
        actor=hook_actor(), workspace_id=WORKSPACE_ID, limit=1, cursor=None, filters=filters
    )
    assert page.items[0].id == "sess_range_2"
    assert page.next_cursor
    following = await preview_queries.list_sessions(
        actor=hook_actor(), workspace_id=WORKSPACE_ID, limit=1, cursor=page.next_cursor, filters=filters
    )
    assert following.items[0].id == "sess_range_1"
    assert following.next_cursor is None
    with pytest.raises(NativeQueryError, match="cursor"):
        await preview_queries.list_sessions(
            actor=hook_actor(), workspace_id=WORKSPACE_ID, limit=1, cursor=page.next_cursor, filters=SessionFilters()
        )


async def test_search_and_filters_do_not_reveal_hidden_threads(preview_queries, lifecycle_interaction_sessions):
    async with transaction(lifecycle_interaction_sessions) as database:
        hidden_agent = await _add_hidden_agent(database)
        hidden_thread, _ = await _add_thread(database, "hidden_filter", updated_seconds=5, agent_id=hidden_agent)
        await database.execute(delete(RoleBindingRecord).where(RoleBindingRecord.resource_type == "workspace"))
        _grant_agent_viewer(database)
    assert not (await _list(preview_queries, q=hidden_thread)).items
    assert not (await _list(preview_queries, agent_id=hidden_agent)).items
    page = await _list(preview_queries, agent_id=AGENT_ID, status=("accepted",))
    assert [item.id for item in page.items] == [SESSION_ID]


@pytest.mark.parametrize("missing_action", [WorkspaceAction.thread_read, WorkspaceAction.run_read])
async def test_run_filters_require_preview_authority(preview_queries, monkeypatch, missing_action):
    monkeypatch.setitem(
        iam_authorization._WORKSPACE_ROLE_ACTIONS,
        "builder",
        iam_authorization._WORKSPACE_ROLE_ACTIONS["builder"] - {missing_action},
    )
    assert not (await _list(preview_queries, status=("accepted",))).items
    if missing_action == WorkspaceAction.thread_read:
        assert not (await _list(preview_queries, q=THREAD_ID)).items
    assert (await _list(preview_queries, q=SESSION_ID)).items


def test_filters_validate_and_canonicalize():
    assert SessionFilters(status=("failed", "accepted", "failed")).status == ("accepted", "failed")
    for values in (
        {"updated_after": "2026-09-10T00:00:00"},
        {"updated_after": NOW, "updated_before": NOW},
        {"status": ["unknown"]},
        {"trigger_type": ["x" * 257]},
        {"q": "x" * 73},
    ):
        with pytest.raises(ValidationError):
            SessionFilters(**values)
