"""Bot navigation projects trusted bindings without enlarging history authority."""

from dataclasses import replace
from datetime import timedelta

import pytest
from a13n_service.bots.connectivity.history import list_bot_threads
from a13n_service.connectivity.accounts.models import AccountRecord
from a13n_service.connectivity.errors import NativeError
from a13n_service.connectivity.ingress.admission_models import AgentThreadBindingRecord
from a13n_service.iam import AuthorizationError, WorkspaceAction
from a13n_service.iam import authorization as iam_authorization
from a13n_service.iam.models import RoleBindingRecord
from a13n_service.interactions.models import RunRecord, SessionRecord
from a13n_service.storage import transaction
from sqlalchemy import delete

from tests.hooks.support import RUN_ID, hook_actor
from tests.interactions.conftest import NOW, ORGANIZATION_ID, SESSION_ID, THREAD_ID, USER_ID, WORKSPACE_ID

from .test_session_previews import _add_hidden_agent, _add_thread, _grant_agent_viewer
from .test_session_previews import preview_queries as preview_queries

pytestmark = pytest.mark.anyio
ACCOUNT_ID = "acct_bot_history"


@pytest.fixture
async def bot_history(preview_queries, lifecycle_interaction_sessions):
    async with transaction(lifecycle_interaction_sessions) as database:
        for suffix in ("history", "other"):
            database.add(
                AccountRecord(
                    id=f"acct_bot_{suffix}",
                    organization_id=ORGANIZATION_ID,
                    workspace_id=WORKSPACE_ID,
                    name=suffix,
                    normalized_name=suffix,
                    provider_key="slack",
                    provider_config_version="slack_http_v1",
                    provider_config_json={},
                    identity_digest=suffix,
                    status="disabled",
                    version=1,
                    credential_generation=1,
                    ciphertext=b"not-used",
                    nonce=b"0" * 12,
                    encryption_key_id="not-used",
                    created_by_type="user",
                    created_by_id=USER_ID,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
        await database.flush()
        _binding(database, "first", THREAD_ID)
        run = await database.get(RunRecord, RUN_ID)
        run.native_tool_contexts_json = [{"kind": "inbound", "account_id": ACCOUNT_ID, "target_id": "tgt_old"}]
    return lifecycle_interaction_sessions


def _binding(database, suffix, thread_id, account_id=ACCOUNT_ID):
    database.add(
        AgentThreadBindingRecord(
            id=f"bind_{suffix}",
            organization_id=ORGANIZATION_ID,
            workspace_id=WORKSPACE_ID,
            account_id=account_id,
            external_ref_kind="slack_thread",
            external_ref_id=suffix,
            thread_id=thread_id,
            next_batch_sequence=1,
            next_submission_at=NOW,
            created_at=NOW,
            updated_at=NOW,
        )
    )


async def _list(sessions, *, account_id=ACCOUNT_ID, target_id=None, cursor=None, limit=20):
    return await list_bot_threads(
        sessions, actor=hook_actor(), account_id=account_id, target_id=target_id, limit=limit, cursor=cursor
    )


async def test_exact_account_target_and_retained_context(bot_history):
    async with transaction(bot_history) as database:
        other_thread, _ = await _add_thread(database, "other")
        await database.flush()
        _binding(database, "other", other_thread, "acct_bot_other")
        session = await database.get(SessionRecord, SESSION_ID)
        session.labels = {"account_id": "acct_bot_other", "target_id": "tgt_forged"}
    page = await _list(bot_history)
    assert [(item.thread_id, item.run_id) for item in page.items] == [(THREAD_ID, RUN_ID)]
    assert len((await _list(bot_history, target_id="tgt_old")).items) == 1
    assert not (await _list(bot_history, target_id="tgt_forged")).items
    # No admission/batch row or live Target is required to navigate retained history.
    assert "not-used" not in page.model_dump_json()
    assert "slack_thread" not in page.model_dump_json()


async def test_stable_pagination_and_query_bound_cursor(bot_history):
    async with transaction(bot_history) as database:
        for suffix in ("a", "b"):
            thread, _ = await _add_thread(database, suffix, updated_seconds=2)
            await database.flush()
            _binding(database, suffix, thread)
    first = await _list(bot_history, limit=1)
    assert first.items[0].binding_id == "bind_b"
    second = await _list(bot_history, limit=1, cursor=first.next_cursor)
    assert second.items[0].binding_id == "bind_a"
    third = await _list(bot_history, limit=1, cursor=second.next_cursor)
    assert third.items[0].binding_id == "bind_first"
    assert third.next_cursor is None
    for changed in ({"target_id": "tgt_old"}, {"account_id": "acct_bot_other"}):
        with pytest.raises(NativeError, match="cursor"):
            await _list(bot_history, cursor=first.next_cursor, **changed)


@pytest.mark.parametrize(
    "action", [WorkspaceAction.session_read, WorkspaceAction.thread_read, WorkspaceAction.run_read]
)
async def test_account_read_does_not_grant_history(bot_history, monkeypatch, action):
    monkeypatch.setitem(
        iam_authorization._WORKSPACE_ROLE_ACTIONS,
        "builder",
        iam_authorization._WORKSPACE_ROLE_ACTIONS["builder"] - {action},
    )
    with pytest.raises(AuthorizationError):
        await _list(bot_history)


async def test_agent_visibility_is_applied_before_pagination(bot_history, monkeypatch):
    async with transaction(bot_history) as database:
        hidden_agent = await _add_hidden_agent(database)
        hidden_thread, _ = await _add_thread(database, "hidden", agent_id=hidden_agent, updated_seconds=9)
        await database.flush()
        _binding(database, "hidden", hidden_thread)
        _grant_agent_viewer(database)
    # Retain Account read through Workspace membership, while history comes only
    # from the selected Agent grant, just as on the regular conversation page.
    monkeypatch.setitem(
        iam_authorization._WORKSPACE_ROLE_ACTIONS,
        "builder",
        iam_authorization._WORKSPACE_ROLE_ACTIONS["builder"]
        - {WorkspaceAction.session_read, WorkspaceAction.thread_read, WorkspaceAction.run_read},
    )
    page = await _list(bot_history, limit=1)
    assert [item.thread_id for item in page.items] == [THREAD_ID]
    assert page.next_cursor is None
    async with transaction(bot_history) as database:
        await database.execute(delete(RoleBindingRecord).where(RoleBindingRecord.resource_type == "agent"))
    with pytest.raises(AuthorizationError):
        await _list(bot_history)


async def test_deleted_account_stops_navigation(bot_history):
    async with transaction(bot_history) as database:
        account = await database.get(AccountRecord, ACCOUNT_ID)
        account.deleted_at = NOW + timedelta(seconds=1)
        account.ciphertext = account.nonce = account.encryption_key_id = None
    with pytest.raises(NativeError, match="not found"):
        await _list(bot_history)


async def test_organization_browser_session_resolves_workspace_from_account(bot_history):
    browser_actor = replace(
        hook_actor(), auth_method="session", boundary_workspace_id=None, boundary_organization_id=ORGANIZATION_ID
    )
    page = await list_bot_threads(
        bot_history, actor=browser_actor, account_id=ACCOUNT_ID, target_id=None, cursor=None, limit=20
    )
    assert [item.thread_id for item in page.items] == [THREAD_ID]
