"""Native reply proof is durable before dispatch and independent of Run completion."""

import asyncio
from datetime import timedelta

import httpx2
import pytest
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from a13n_harness.providers.http import ProviderHttpError
from a13n_service.bots.connectivity.models import BotReplyRecord
from a13n_service.bots.connectivity.replies import BotReplyObserver
from a13n_service.bots.connectivity.reply_queries import list_bot_replies
from a13n_service.connectivity.accounts.models import AccountRecord
from a13n_service.connectivity.native_context import InboundRunContext
from a13n_service.connectivity.providers.registry import require_native_provider
from a13n_service.iam.models import RoleBindingRecord
from a13n_service.interactions.attempts import AttemptAuthorityError
from a13n_service.interactions.models import RunAttemptRecord, RunRecord
from a13n_service.interactions.scheduling import AttemptScheduler, ClaimedAttempt
from a13n_service.storage import short_session, transaction
from sqlalchemy import select

from tests.hooks.support import RUN_ID, hook_actor
from tests.interactions.conftest import NOW, WORKSPACE_ID
from tests.interactions.test_attempt_execution import _authority, _worker
from tests.lifecycle_support import test_lifecycle_writer

from .test_bot_history import ACCOUNT_ID
from .test_bot_history import bot_history as bot_history
from .test_session_previews import preview_queries as preview_queries

pytestmark = pytest.mark.anyio


@pytest.fixture
async def reply_fixture(bot_history):
    context = InboundRunContext(
        account_id=ACCOUNT_ID,
        binding_id="bind_first",
        target_id="tgt_pilot",
        provider_key="slack",
        execution_principal_ref=hook_actor().principal,
        provider_context_version=require_native_provider("slack").context_version,
        provider_context={"channel_id": "C1", "root_thread_ts": "1.0", "conversation_kind": "channel"},
        action_policy={"reply_mode": "thread"},
        allowed_actions=("slack.reply",),
    )
    async with transaction(bot_history) as database:
        account = await database.get(AccountRecord, ACCOUNT_ID)
        account.status = "active"
        run = await database.get(RunRecord, RUN_ID)
        run.native_tool_contexts_json = [context.model_dump(mode="json")]
        binding = await database.get(RoleBindingRecord, "rb_hookws717171717")
        binding.role_key = "admin"
    claim = await AttemptScheduler(bot_history, clock=lambda: NOW, lifecycle=test_lifecycle_writer()).claim(
        RUN_ID, _worker()
    )
    assert isinstance(claim, ClaimedAttempt)
    attempt = _authority(claim)
    observer = BotReplyObserver(
        bot_history,
        attempt=attempt,
        context=context,
        workspace_id=WORKSPACE_ID,
        account_version=1,
        credential_generation=1,
        clock=lambda: NOW,
    )
    return bot_history, context, attempt, observer


async def _records(sessions):
    async with short_session(sessions) as database:
        return tuple((await database.scalars(select(BotReplyRecord).order_by(BotReplyRecord.id))).all())


async def _page(sessions, *, clock=lambda: NOW, limit=20, cursor=None):
    return await list_bot_replies(
        sessions, actor=hook_actor(), account_id=ACCOUNT_ID, run_id=RUN_ID, limit=limit, cursor=cursor, clock=clock
    )


def _reply(context, http):
    return require_native_provider("slack").inbound_actions(
        context.provider_context, context.action_policy, {}, {"bot_token": "private-token"}, http, EndpointPolicy()
    )["slack.reply"]


@pytest.mark.parametrize("outcome", ["confirmed", "unknown", "rejected", "cancelled"])
async def test_observes_native_reply_before_hiding_receipt(reply_fixture, outcome):
    sessions, context, attempt, observer = reply_fixture
    requests = []

    async def send(request):
        requests.append(request)
        # A second SQL session sees a committed marker before external I/O.
        rows = await _records(sessions)
        assert len(rows) == 1 and rows[0].status == "dispatching"
        assert rows[0].run_attempt_id == attempt.run_attempt_id
        if outcome == "unknown":
            raise httpx2.ReadTimeout("private provider message")
        if outcome == "rejected":
            return httpx2.Response(200, json={"ok": False, "error": "private provider reason"})
        if outcome == "cancelled":
            raise asyncio.CancelledError()
        return httpx2.Response(200, json={"ok": True, "channel": "C1", "ts": "2.0"})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(send)) as http:
        action = _reply(context, http)
        if outcome in {"rejected", "cancelled"}:
            with pytest.raises(ProviderHttpError if outcome == "rejected" else asyncio.CancelledError):
                await action.call_observed({"text": "private reply body"}, observer)
        else:
            result = await action.call_observed({"text": "private reply body"}, observer)
            assert result["kind"] == ("succeeded" if outcome == "confirmed" else "outcome_unknown")
            assert "receipt" not in result
    page = await _page(sessions)
    proof = page.items[0]
    assert (
        proof.status
        == {
            "confirmed": "succeeded",
            "unknown": "outcome_unknown",
            "rejected": "rejected",
            "cancelled": "outcome_unknown",
        }[outcome]
    )
    assert bool(proof.receipt) == (outcome == "confirmed")
    assert len(requests) == 1
    assert "private" not in page.model_dump_json()
    if proof.receipt:
        assert proof.receipt.channel_id == "C1" and proof.receipt.message_ts == "2.0"
    assert (await _records(sessions))[0].finished_at is not None


@pytest.mark.parametrize("change", ["account_version", "credential_generation", "context", "lease"])
async def test_observation_requires_current_source_and_attempt_before_dispatch(reply_fixture, change):
    sessions, context, attempt, observer = reply_fixture
    async with transaction(sessions) as database:
        if change in {"account_version", "credential_generation"}:
            account = await database.get(AccountRecord, ACCOUNT_ID)
            if change == "account_version":
                account.version += 1
            else:
                account.credential_generation += 1
        elif change == "context":
            run = await database.get(RunRecord, RUN_ID)
            run.native_tool_contexts_json = []
        else:
            run_attempt = await database.get(RunAttemptRecord, attempt.run_attempt_id)
            run_attempt.lease_expires_at = NOW
    requests = []
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(lambda request: requests.append(request))) as http:
        with pytest.raises(AttemptAuthorityError if change == "lease" else ValueError):
            await _reply(context, http).call_observed({"text": "hello"}, observer)
    assert not requests and not await _records(sessions)


async def test_reply_confirmation_survives_attempt_expiration_during_provider_io(reply_fixture):
    sessions, context, attempt, observer = reply_fixture

    async def send(request):
        async with transaction(sessions) as database:
            record = await database.get(RunAttemptRecord, attempt.run_attempt_id)
            record.lease_expires_at = NOW
        return httpx2.Response(200, json={"ok": True, "channel": "C1", "ts": "2.0"})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(send)) as http:
        assert await _reply(context, http).call_observed({"text": "hello"}, observer) == {"kind": "succeeded"}
    assert (await _page(sessions)).items[0].status == "succeeded"


async def test_storage_failure_after_reply_keeps_unknown_marker_without_resending(reply_fixture):
    sessions, context, _attempt, observer = reply_fixture
    calls = []

    def unavailable():
        raise OSError("private database details")

    async def send(request):
        calls.append(request)
        observer._sessions = unavailable
        return httpx2.Response(200, json={"ok": True, "channel": "C1", "ts": "2.0"})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(send)) as http:
        assert await _reply(context, http).call_observed({"text": "hello"}, observer) == {"kind": "succeeded"}
    assert len(calls) == 1
    assert (await _records(sessions))[0].status == "dispatching"
    assert (await _page(sessions)).items[0].status == "dispatching"
    assert (await _page(sessions, clock=lambda: NOW + timedelta(days=1))).items[0].status == "outcome_unknown"


async def test_concurrent_replies_have_independent_observations_and_cursor(reply_fixture):
    sessions, context, _, observer = reply_fixture
    calls = []

    def send(request):
        calls.append(request)
        return httpx2.Response(200, json={"ok": True, "channel": "C1", "ts": f"{len(calls) + 1}.0"})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(send)) as http:
        reply = _reply(context, http)
        await asyncio.gather(*(reply.call_observed({"text": str(index)}, observer) for index in range(2)))
    assert len(calls) == 2
    first = await _page(sessions, limit=1)
    second = await _page(sessions, limit=1, cursor=first.next_cursor)
    assert first.items[0].id != second.items[0].id
    assert first.items[0].receipt.request_id != second.items[0].receipt.request_id
    assert second.next_cursor is None


async def test_invalid_arguments_do_not_create_a_dispatch_observation(reply_fixture):
    from pydantic import ValidationError

    sessions, context, _, observer = reply_fixture
    async with httpx2.AsyncClient() as http:
        with pytest.raises(ValidationError):
            await _reply(context, http).call_observed({"text": "hello", "channel_id": "other"}, observer)
    assert not await _records(sessions)


async def test_reply_queries_require_account_read_and_conceal_deleted_account(reply_fixture, monkeypatch):
    from a13n_service.connectivity.errors import NativeError
    from a13n_service.iam import WorkspaceAction
    from a13n_service.iam import authorization as iam_authorization

    sessions, context, _, observer = reply_fixture
    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda _: httpx2.Response(200, json={"ok": True, "channel": "C1", "ts": "2.0"}))
    ) as http:
        await _reply(context, http).call_observed({"text": "hello"}, observer)
    async with transaction(sessions) as database:
        binding = await database.get(RoleBindingRecord, "rb_hookws717171717")
        binding.role_key = "builder"
    assert len((await _page(sessions)).items) == 1
    monkeypatch.setitem(
        iam_authorization._WORKSPACE_ROLE_ACTIONS,
        "builder",
        iam_authorization._WORKSPACE_ROLE_ACTIONS["builder"] - {WorkspaceAction.application_account_read},
    )
    with pytest.raises(NativeError, match="not found"):
        await _page(sessions)
    async with transaction(sessions) as database:
        binding = await database.get(RoleBindingRecord, "rb_hookws717171717")
        binding.role_key = "admin"
        account = await database.get(AccountRecord, ACCOUNT_ID)
        account.deleted_at = NOW
        account.ciphertext = account.nonce = account.encryption_key_id = None
    with pytest.raises(NativeError, match="not found"):
        await _page(sessions)


async def test_worker_native_capability_wires_attempt_to_durable_observer(reply_fixture, monkeypatch):
    import json
    from functools import partial
    from unittest.mock import Mock

    from a13n_harness import AgentSpec, HarnessBuilder
    from a13n_harness.providers.catalog import ProviderCatalog
    from a13n_service.bots.connectivity import replies
    from a13n_service.bots.connectivity.replies import ReplyObservations
    from a13n_service.connectivity import execution
    from a13n_service.connectivity.execution import ExternalToolRuntime
    from a13n_service.connectivity.mcp.refresh import OAuthCredentialRefresh
    from a13n_service.connectivity.mcp.transport import RemoteTransport
    from a13n_service.secrets import SecretProtector
    from pydantic_ai.models.function import DeltaToolCall, FunctionModel

    from tests.interactions.worker_helpers import prepare_permissions

    sessions, _, attempt, _ = reply_fixture
    protector = SecretProtector(key=b"k" * 32, encryption_key_id="test")
    async with transaction(sessions) as database:
        account = await database.get(AccountRecord, ACCOUNT_ID)
        account.replace_credential('{"bot_token":"private-token"}', protector)
        run = (await database.get(RunRecord, RUN_ID)).to_resource()
    await prepare_permissions(sessions, run, attempt)
    monkeypatch.setattr(execution, "utc_now", lambda: NOW)
    monkeypatch.setattr(replies, "BotReplyObserver", partial(BotReplyObserver, clock=lambda: NOW))
    policy = EndpointPolicy()
    calls = []

    def send(request):
        calls.append(request)
        return httpx2.Response(200, json={"ok": True, "channel": "C1", "ts": "2.0"})

    step = 0

    async def model(messages, info):
        nonlocal step
        step += 1
        if step == 1:
            yield {
                0: DeltaToolCall(
                    name=info.function_tools[0].name,
                    json_args=json.dumps({"text": "hello"}),
                    tool_call_id="native-reply",
                )
            }
        else:
            yield "done"

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(send)) as http:
        runtime = ExternalToolRuntime(
            sessions,
            protector,
            ProviderCatalog(),
            None,
            RemoteTransport(policy),
            policy,
            http,
            Mock(spec=OAuthCredentialRefresh),
            observations=ReplyObservations(sessions),
        )
        async with runtime.capabilities(lambda: attempt) as capabilities:
            harness = HarnessBuilder().build(
                AgentSpec(), output_type=str, model=FunctionModel(stream_function=model), capabilities=capabilities
            )
            result = await harness.run("reply once")
    assert result.output_or_raise() == "done" and len(calls) == 1
    proof = (await _page(sessions)).items[0]
    assert proof.status == "succeeded" and proof.run_attempt_id == attempt.run_attempt_id
    assert proof.receipt.message_ts == "2.0"
    # The Run has not completed; the evidence came from the native reply itself.
    async with short_session(sessions) as database:
        assert (await database.get(RunRecord, RUN_ID)).status == "running"


@pytest.mark.parametrize("lost_response", [False, True])
async def test_feishu_receipts_use_the_same_observation_contract(reply_fixture, lost_response):
    from tests.connectivity.test_lark import _config
    from tests.connectivity.test_lark_client import _AllowEndpoint, _token_response

    sessions, context, attempt, _ = reply_fixture
    context = context.model_copy(
        update={
            "provider_key": "lark",
            "provider_context_version": require_native_provider("lark").context_version,
            "provider_context": {
                "chat_id": "oc_chat",
                "message_id": "om_message",
                "discussion_id": "omt_thread",
                "chat_type": "group",
            },
            "allowed_actions": ("lark.reply",),
        }
    )
    async with transaction(sessions) as database:
        account = await database.get(AccountRecord, ACCOUNT_ID)
        account.provider_key = "lark"
        run = await database.get(RunRecord, RUN_ID)
        run.native_tool_contexts_json = [context.model_dump(mode="json")]
    observer = BotReplyObserver(
        sessions,
        attempt=attempt,
        context=context,
        workspace_id=WORKSPACE_ID,
        account_version=1,
        credential_generation=1,
        clock=lambda: NOW,
    )
    writes = []

    async def respond(request):
        assert (await _records(sessions))[0].status == "dispatching"
        if request.url.path.endswith("tenant_access_token/internal"):
            return _token_response()
        writes.append(request)
        if lost_response:
            raise httpx2.ReadTimeout("private transport details")
        return httpx2.Response(
            200, json={"code": 0, "data": {"message_id": "om_reply", "root_id": "om_root", "thread_id": "omt_thread"}}
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http:
        reply = require_native_provider("lark").inbound_actions(
            context.provider_context,
            context.action_policy,
            _config(),
            {"app_secret": "private-secret"},
            http,
            _AllowEndpoint(),
        )["lark.reply"]
        result = await reply.call_observed({"content": {"kind": "text", "text": "private message"}}, observer)
    assert len(writes) == 1
    proof = (await _page(sessions)).items[0]
    assert result["kind"] == proof.status == ("outcome_unknown" if lost_response else "succeeded")
    if not lost_response:
        assert proof.receipt.message_id == "om_reply" and "receipt" not in result
    assert "private" not in proof.model_dump_json()


@pytest.mark.parametrize("outcome", ["confirmed", "unknown"])
async def test_github_comment_receipt_is_retained_before_hiding_it(reply_fixture, outcome):
    from a13n_service.connectivity.providers.github.actions import GitHubCommentReceipt

    from tests.connectivity.test_github_client import _AllowEndpoint

    sessions, context, attempt, _observer = reply_fixture
    context = context.model_copy(
        update={
            "provider_key": "github",
            "provider_context_version": require_native_provider("github").context_version,
            "provider_context": {
                "repository_id": 42,
                "repository_owner": "acme",
                "repository_name": "repo",
                "number": 7,
                "target_kind": "issue",
            },
            "action_policy": {},
            "allowed_actions": ("github.add_comment",),
        }
    )
    async with transaction(sessions) as database:
        account = await database.get(AccountRecord, ACCOUNT_ID)
        account.provider_key = "github"
        run = await database.get(RunRecord, RUN_ID)
        run.native_tool_contexts_json = [context.model_dump(mode="json")]
    observer = BotReplyObserver(
        sessions,
        attempt=attempt,
        context=context,
        workspace_id=WORKSPACE_ID,
        account_version=1,
        credential_generation=1,
        clock=lambda: NOW,
    )
    posts = []

    async def respond(request):
        if request.url.path == "/user":
            return httpx2.Response(200, json={"id": 99})
        if request.url.path == "/repos/acme/repo":
            return httpx2.Response(200, json={"id": 42})
        rows = await _records(sessions)
        assert len(rows) == 1 and rows[0].status == "dispatching"
        posts.append(request)
        if outcome == "unknown":
            raise httpx2.ReadTimeout("external reply could have been created")
        return httpx2.Response(
            201,
            json={
                "id": 123,
                "node_id": "IC_test",
                "html_url": "https://github.com/acme/repo/issues/7#issuecomment-123",
            },
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http:
        action = require_native_provider("github").inbound_actions(
            context.provider_context,
            {},
            {"user_id": 99, "api_origin": "https://api.github.com", "web_origin": "https://github.com"},
            {"personal_access_token": "private-pat"},
            http,
            _AllowEndpoint(),
        )["github.add_comment"]
        result = await action.call_observed({"body": "private-comment"}, observer)
        assert "receipt" not in result
    evidence = (await _page(sessions)).items[0]
    assert len(posts) == 1
    assert evidence.status == ("succeeded" if outcome == "confirmed" else "outcome_unknown")
    if outcome == "confirmed":
        assert isinstance(evidence.receipt, GitHubCommentReceipt)
        assert evidence.receipt.comment_id == 123
    assert "private" not in evidence.model_dump_json()
