from __future__ import annotations

from datetime import timedelta

import pytest
from a13n_service.agents.models import AgentRecord, AgentRevisionRecord
from a13n_service.gateway.queries import NativeInteractionQueries, NativeQueryError
from a13n_service.iam import WorkspaceAction
from a13n_service.iam import authorization as iam_authorization
from a13n_service.iam.models import RoleBindingRecord
from a13n_service.interactions.command_values import ContinueRunCommand
from a13n_service.interactions.domain import ThreadOriginKind, ThreadRole
from a13n_service.interactions.models import RunRecord, SessionRecord, ThreadRecord
from a13n_service.interactions.records import run_record, thread_record
from a13n_service.run_stream import RunDisplayStore
from a13n_service.storage import transaction
from a13n_service.storage.object_store import LocalObjectStore
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.hooks.support import RUN_ID, hook_actor, seed_hook_actor_access, seed_run_and_secret
from tests.interactions.conftest import (
    AGENT_ID,
    AGENT_REVISION_ID,
    NOW,
    ORGANIZATION_ID,
    SESSION_ID,
    THREAD_ID,
    USER_ID,
    WORKSPACE_ID,
    sealed_row_rewrite,
)
from tests.interactions.conftest import interaction_sessions as interaction_sessions
from tests.sql_capture import capture_sql

from .test_commands import _commands, _complete_run, _Freezing, _frozen, _Preparation, _request

pytestmark = pytest.mark.anyio


@pytest.fixture
async def preview_queries(lifecycle_interaction_sessions, tmp_path):
    await seed_run_and_secret(lifecycle_interaction_sessions)
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    objects = await LocalObjectStore.create(tmp_path / "objects")
    return NativeInteractionQueries(lifecycle_interaction_sessions, RunDisplayStore(objects))


async def _add_thread(
    database: AsyncSession,
    suffix: str,
    *,
    session_id: str = SESSION_ID,
    updated_seconds: int = 1,
    agent_id: str = AGENT_ID,
    empty: bool = False,
    trigger_type: str = "user_input",
) -> tuple[str, str]:
    source_thread = await database.get(ThreadRecord, THREAD_ID)
    source_run = await database.get(RunRecord, RUN_ID)
    assert source_thread is not None and source_run is not None
    thread_id, run_id = f"thread-{suffix}", f"run_{suffix}"
    values = {
        "id": thread_id,
        "session_id": session_id,
        "current_run_id": None if empty else run_id,
        "updated_at": NOW + timedelta(seconds=updated_seconds),
    }
    if session_id == SESSION_ID:
        values.update(
            role=ThreadRole.child,
            origin_kind=ThreadOriginKind.child,
            origin_thread_id=THREAD_ID,
            origin_run_id=RUN_ID,
        )
    database.add(thread_record(source_thread.to_resource().model_copy(update=values)))
    if not empty:
        database.add(
            run_record(
                source_run.to_resource().model_copy(
                    update={
                        "id": run_id,
                        "session_id": session_id,
                        "thread_id": thread_id,
                        "agent_id": agent_id,
                        "agent_revision_id": (AGENT_REVISION_ID if agent_id == AGENT_ID else "agtr_hidden"),
                        "input_text": f"input {suffix}",
                        "trigger_type": trigger_type,
                    }
                )
            )
        )
    return thread_id, run_id


def _add_session(database: AsyncSession, suffix: str, *, updated_seconds: int = 0) -> str:
    session_id = f"sess_{suffix}"
    database.add(
        SessionRecord(
            id=session_id,
            organization_id=ORGANIZATION_ID,
            workspace_id=WORKSPACE_ID,
            created_at=NOW,
            updated_at=NOW + timedelta(seconds=updated_seconds),
        )
    )
    return session_id


async def _add_hidden_agent(database: AsyncSession) -> str:
    agent = await database.get(AgentRecord, AGENT_ID)
    revision = await database.get(AgentRevisionRecord, AGENT_REVISION_ID)
    assert agent is not None and revision is not None
    agent_values = {column.name: getattr(agent, column.name) for column in AgentRecord.__table__.columns}
    agent_values.update(id="agt_hidden", name="Hidden", key="hidden", default_revision_id="agtr_hidden")
    database.add(AgentRecord(**agent_values))
    await database.flush()
    revision_values = {column.name: getattr(revision, column.name) for column in AgentRevisionRecord.__table__.columns}
    revision_values.update(id="agtr_hidden", agent_id="agt_hidden")
    database.add(AgentRevisionRecord(**revision_values))
    return "agt_hidden"


def _grant_agent_viewer(database: AsyncSession) -> None:
    database.add(
        RoleBindingRecord(
            id="rb_preview_agent",
            organization_id=ORGANIZATION_ID,
            workspace_id=WORKSPACE_ID,
            principal_type="user",
            principal_id=USER_ID,
            resource_type="agent",
            resource_id=AGENT_ID,
            role_key="viewer",
            created_by_user_id=USER_ID,
            created_at=NOW,
            updated_at=NOW,
        )
    )


async def test_preview_uses_latest_thread_with_stable_id_tiebreaker(
    preview_queries: NativeInteractionQueries,
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with transaction(lifecycle_interaction_sessions) as database:
        await _add_thread(database, "older", updated_seconds=1)
        await _add_thread(database, "latest_a", updated_seconds=2)
        thread_id, run_id = await _add_thread(database, "latest_z", updated_seconds=2)
        original = await database.get(RunRecord, RUN_ID)
        assert original is not None
        original.updated_at = NOW + timedelta(seconds=100)
        agent = await database.get(AgentRecord, AGENT_ID)
        assert agent is not None
        agent_name, status, trigger_type = (agent.name, original.status, original.trigger_type)
    page = await preview_queries.list_sessions(actor=hook_actor(), workspace_id=WORKSPACE_ID, limit=20, cursor=None)
    assert page.items[0].preview is not None
    assert page.items[0].preview.model_dump() == {
        "thread_id": thread_id,
        "run_id": run_id,
        "input_text": "input latest_z",
        "output_text": None,
        "agent_name": agent_name,
        "run_status": status,
        "trigger_type": trigger_type,
    }
    assert page.items[0].run_count == 4


async def test_empty_session_and_latest_empty_thread_have_no_preview(
    preview_queries: NativeInteractionQueries,
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with transaction(lifecycle_interaction_sessions) as database:
        empty_session = _add_session(database, "empty")
        await _add_thread(database, "latest_empty", empty=True)
    page = await preview_queries.list_sessions(actor=hook_actor(), workspace_id=WORKSPACE_ID, limit=20, cursor=None)
    assert {item.id: item.preview for item in page.items} == {SESSION_ID: None, empty_session: None}
    assert {item.id: item.run_count for item in page.items} == {SESSION_ID: 1, empty_session: 0}


async def test_agent_scoped_preview_selects_latest_visible_thread_before_selection(
    preview_queries: NativeInteractionQueries,
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with transaction(lifecycle_interaction_sessions) as database:
        visible_thread, visible_run = await _add_thread(database, "visible", updated_seconds=1)
        hidden_agent = await _add_hidden_agent(database)
        await _add_thread(database, "hidden", updated_seconds=2, agent_id=hidden_agent)
        await database.execute(delete(RoleBindingRecord).where(RoleBindingRecord.resource_type == "workspace"))
        _grant_agent_viewer(database)
    page = await preview_queries.list_sessions(actor=hook_actor(), workspace_id=WORKSPACE_ID, limit=20, cursor=None)
    assert len(page.items) == 1
    assert page.items[0].preview is not None
    assert (page.items[0].preview.thread_id, page.items[0].preview.run_id) == (visible_thread, visible_run)
    assert "hidden" not in page.model_dump_json()
    assert page.items[0].run_count == 2


@pytest.mark.parametrize("missing_action", [WorkspaceAction.thread_read, WorkspaceAction.run_read])
async def test_session_read_does_not_grant_preview_permissions(
    preview_queries: NativeInteractionQueries,
    monkeypatch: pytest.MonkeyPatch,
    missing_action: WorkspaceAction,
) -> None:
    monkeypatch.setitem(
        iam_authorization._WORKSPACE_ROLE_ACTIONS,
        "builder",
        iam_authorization._WORKSPACE_ROLE_ACTIONS["builder"] - {missing_action},
    )
    page = await preview_queries.list_sessions(actor=hook_actor(), workspace_id=WORKSPACE_ID, limit=20, cursor=None)
    assert [item.id for item in page.items] == [SESSION_ID]
    assert page.items[0].preview is None
    assert THREAD_ID not in page.model_dump_json() and RUN_ID not in page.model_dump_json()
    assert page.items[0].run_count == (None if missing_action == WorkspaceAction.run_read else 1)


async def test_unreadable_selected_run_does_not_fall_back_to_an_older_thread(
    preview_queries: NativeInteractionQueries,
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with transaction(lifecycle_interaction_sessions) as database:
        hidden_agent = await _add_hidden_agent(database)
        await _add_thread(database, "unreadable", agent_id=hidden_agent)
        _grant_agent_viewer(database)
    monkeypatch.setitem(
        iam_authorization._WORKSPACE_ROLE_ACTIONS,
        "builder",
        iam_authorization._WORKSPACE_ROLE_ACTIONS["builder"] - {WorkspaceAction.run_read},
    )
    page = await preview_queries.list_sessions(actor=hook_actor(), workspace_id=WORKSPACE_ID, limit=20, cursor=None)
    assert [item.id for item in page.items] == [SESSION_ID]
    assert page.items[0].preview is None


async def test_preview_does_not_bypass_session_authorization(
    preview_queries: NativeInteractionQueries,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(
        iam_authorization._WORKSPACE_ROLE_ACTIONS,
        "builder",
        iam_authorization._WORKSPACE_ROLE_ACTIONS["builder"] - {WorkspaceAction.session_read},
    )
    with pytest.raises(NativeQueryError) as caught:
        await preview_queries.list_sessions(actor=hook_actor(), workspace_id=WORKSPACE_ID, limit=20, cursor=None)
    assert caught.value.code == "resource_not_found"


async def test_preview_text_is_bounded_unicode_and_prefers_current_over_head(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    tmp_path,
) -> None:
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    objects = await LocalObjectStore.create(tmp_path / "objects")
    commands = _commands(lifecycle_interaction_sessions, objects, _Preparation(), _Freezing([_frozen()]))
    source = await commands.runs.start(
        actor=hook_actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="preview-source",
        request=_request("输入🌏" * 200),
    )
    await _complete_run(lifecycle_interaction_sessions, objects, run_id=source.run_id)
    async with transaction(lifecycle_interaction_sessions) as database:
        async with sealed_row_rewrite(database):
            run = await database.get(RunRecord, source.run_id)
            thread = await database.get(ThreadRecord, source.thread_id)
            assert run is not None and thread is not None
            run.output_text = "结果🌏" * 200
            thread.current_run_id = None
            await database.flush()
    queries = NativeInteractionQueries(lifecycle_interaction_sessions, RunDisplayStore(objects))
    page = await queries.list_sessions(actor=hook_actor(), workspace_id=WORKSPACE_ID, limit=20, cursor=None)
    assert page.items[0].preview is not None
    assert page.items[0].preview.input_text == ("输入🌏" * 200)[:256]
    assert page.items[0].preview.output_text == ("结果🌏" * 200)[:512]
    assert page.items[0].preview.run_id == source.run_id
    async with transaction(lifecycle_interaction_sessions) as database:
        thread = await database.get(ThreadRecord, source.thread_id)
        assert thread is not None
        thread.current_run_id = source.run_id
    continued = await commands.runs.continue_from(
        actor=hook_actor(),
        source_run_id=source.run_id,
        idempotency_key="preview-continue",
        request=ContinueRunCommand(expected_thread_version=2, input=_request("current input").input),
    )
    page = await queries.list_sessions(actor=hook_actor(), workspace_id=WORKSPACE_ID, limit=20, cursor=None)
    assert page.items[0].preview is not None
    assert page.items[0].preview.run_id == continued.run_id
    assert page.items[0].preview.input_text == "current input"
    assert page.items[0].preview.output_text is None


async def test_session_pagination_has_constant_sql_count_and_page_local_previews(
    preview_queries: NativeInteractionQueries,
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with transaction(lifecycle_interaction_sessions) as database:
        for index in range(6):
            session_id = _add_session(database, f"page_{index}", updated_seconds=index)
            await _add_thread(database, f"page_{index}", session_id=session_id)
    counts = []
    for limit in (1, 6):
        with capture_sql(lifecycle_interaction_sessions) as statements:
            page = await preview_queries.list_sessions(
                actor=hook_actor(), workspace_id=WORKSPACE_ID, limit=limit, cursor=None
            )
        assert len(page.items) == limit
        assert all(item.preview is not None for item in page.items)
        assert len([statement for statement in statements if "substr(" in statement]) == 1
        counts.append(len(statements))
    assert counts[0] == counts[1]
    ids = []
    cursor = None
    while True:
        page = await preview_queries.list_sessions(
            actor=hook_actor(), workspace_id=WORKSPACE_ID, limit=2, cursor=cursor
        )
        ids.extend(item.id for item in page.items)
        for item in page.items:
            assert item.preview is not None
            if item.id.startswith("sess_page_"):
                assert item.preview.run_id == f"run_{item.id.removeprefix('sess_')}"
        if page.next_cursor is None:
            break
        cursor = page.next_cursor
    assert len(ids) == len(set(ids)) == 7
    assert ids[:5] == [f"sess_page_{index}" for index in range(5, 0, -1)]


async def test_postgresql_batch_previews_use_bounded_unicode_projections(
    interaction_sessions: async_sessionmaker[AsyncSession], tmp_path
) -> None:
    sessions = interaction_sessions
    await seed_run_and_secret(sessions)
    await seed_hook_actor_access(sessions)
    input_text, output_text = "输入🌏" * 300, "结果🌏" * 300
    async with transaction(sessions) as database:
        for index in range(3):
            session_id = _add_session(database, f"postgres_{index}", updated_seconds=index)
            _, run_id = await _add_thread(database, f"postgres_{index}", session_id=session_id)
            await database.flush()
            run = await database.get(RunRecord, run_id)
            assert run is not None
            run.input_text = input_text
        latest_thread, latest_run = await _add_thread(database, "postgres_latest", updated_seconds=20)
        await database.flush()
        run = await database.get(RunRecord, latest_run)
        assert run is not None
        run.input_text = input_text
    objects = await LocalObjectStore.create(tmp_path / "postgres-objects")
    commands = _commands(sessions, objects, _Preparation(), _Freezing([_frozen()]))
    completed = await commands.runs.start(
        actor=hook_actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="postgres-preview",
        request=_request(input_text),
    )
    await _complete_run(sessions, objects, run_id=completed.run_id)
    async with transaction(sessions) as database:
        async with sealed_row_rewrite(database):
            run = await database.get(RunRecord, completed.run_id)
            owner = await database.get(SessionRecord, completed.session_id)
            assert run is not None and owner is not None
            run.output_text = output_text
            owner.updated_at = NOW + timedelta(seconds=10)
            await database.flush()
    queries = NativeInteractionQueries(sessions, RunDisplayStore(objects))
    counts = []
    for limit in (1, 5):
        with capture_sql(sessions) as statements:
            page = await queries.list_sessions(actor=hook_actor(), workspace_id=WORKSPACE_ID, limit=limit, cursor=None)
        assert len(page.items) == limit
        assert page.items[0].id == completed.session_id
        assert page.items[0].preview is not None
        assert page.items[0].preview.output_text == output_text[:512]
        assert all(item.preview is not None and item.preview.input_text == input_text[:256] for item in page.items)
        projection_queries = [statement for statement in statements if "substr(" in statement]
        assert len(projection_queries) == 1
        assert "substr(" in projection_queries[0]
        counts.append(len(statements))
    assert counts[0] == counts[1]
    original_session = next(item for item in page.items if item.id == SESSION_ID)
    assert original_session.preview is not None
    assert (original_session.preview.thread_id, original_session.preview.run_id) == (latest_thread, latest_run)


async def test_agent_name_requires_agent_read(
    preview_queries: NativeInteractionQueries, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(
        iam_authorization._WORKSPACE_ROLE_ACTIONS,
        "builder",
        iam_authorization._WORKSPACE_ROLE_ACTIONS["builder"] - {WorkspaceAction.agent_read},
    )
    page = await preview_queries.list_sessions(actor=hook_actor(), workspace_id=WORKSPACE_ID, limit=20, cursor=None)
    assert page.items[0].preview is not None
    assert page.items[0].preview.agent_name is None
    assert page.items[0].run_count == 1
