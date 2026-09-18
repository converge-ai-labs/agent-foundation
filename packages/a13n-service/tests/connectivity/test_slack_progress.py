"""Slack progress uses canonical task authority and provider-specific delivery semantics."""

import json
from urllib.parse import urlencode

import httpx2
import pytest
from a13n_service.bots.progress.actions import ProgressActions
from a13n_service.bots.progress.models import ProgressRecord
from a13n_service.connectivity.accounts.models import AccountRecord
from a13n_service.connectivity.http import ConnectivityHttpError
from a13n_service.connectivity.ingress.provider import (
    ProviderActionDecision,
    ProviderCompleteDecision,
    ProviderRequestError,
)
from a13n_service.connectivity.providers.slack.adapter import SlackAccountConfig, SlackIngressAdapter, normalize_payload
from a13n_service.connectivity.providers.slack.progress import STOP_ACTION_ID, task_message
from a13n_service.interactions.models import RunRecord
from a13n_service.storage import short_session, transaction

from .conftest import NOW
from .test_bot_progress import ACCOUNT, due
from .test_bot_progress import replying as replying
from .test_bot_progress import task as task
from .test_slack import _config, _signed_request

pytestmark = pytest.mark.anyio


def callback(**changes):
    payload = {
        "type": "block_actions",
        "api_app_id": "A123",
        "team": {"id": "T123"},
        "enterprise": None,
        "user": {"id": "U123"},
        "container": {"type": "message", "channel_id": "C123", "message_ts": "1788422401.000100"},
        "actions": [{"action_id": STOP_ACTION_ID, "value": json.dumps({"run_id": "run_test", "token": "secret"})}],
    }
    payload.update(changes)
    return payload


async def test_signed_form_and_socket_callback_have_same_decision():
    payload = callback()
    body = urlencode({"payload": json.dumps(payload)}).encode()
    request = _signed_request(body)
    request = request.model_copy(
        update={"headers": {**request.headers, "content-type": "application/x-www-form-urlencoded; charset=utf-8"}}
    )
    decision = await SlackIngressAdapter().authenticate_and_normalize(
        request,
        account_id=ACCOUNT,
        account_config=_config(),
        credentials={"signing_secret": "signing-secret"},
        received_at=NOW,
    )
    assert isinstance(decision, ProviderActionDecision)
    assert decision == normalize_payload(payload, SlackAccountConfig.model_validate(_config()), NOW)
    assert decision.actor_id == "U123" and decision.message_id == "1788422401.000100"
    with pytest.raises(ProviderRequestError):
        await SlackIngressAdapter().authenticate_and_normalize(
            request.model_copy(update={"body": body + b"x"}),
            account_id=ACCOUNT,
            account_config=_config(),
            credentials={"signing_secret": "signing-secret"},
            received_at=NOW,
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"team": {"id": "T_OTHER"}},
        {"api_app_id": "A_OTHER"},
        {"enterprise": {"id": "E_OTHER"}},
        {"container": {"type": "view"}},
        {"user": {}},
        {"actions": [{"action_id": STOP_ACTION_ID, "value": "bad"}]},
    ],
)
async def test_callback_rejects_foreign_installations_and_malformed_actions(changes):
    with pytest.raises(ProviderRequestError):
        normalize_payload(callback(**changes), SlackAccountConfig.model_validate(_config()), NOW)


async def test_details_action_is_acknowledged_without_control():
    result = normalize_payload(
        callback(actions=[{"action_id": "a13n.task_details.v1"}]), SlackAccountConfig.model_validate(_config()), NOW
    )
    assert isinstance(result, ProviderCompleteDecision) and result.response.status_code == 200


@pytest.mark.parametrize("status", ["accepted", "running", "waiting", "completed", "failed", "cancelled", "stopping"])
async def test_block_controls_and_full_answer_fallback(status):
    card = task_message(
        status=status,
        run_id="run_test",
        token="secret",
        details_url="https://console.example/run",
        replies=("first", "second"),
    )
    serialized = json.dumps(card)
    assert (STOP_ACTION_ID in serialized) == (status in {"accepted", "running"})
    assert "first" in card["text"] and "second" in card["text"]
    assert "https://console.example/run" in serialized


async def test_long_answer_chunks_preserve_content_and_reject_oversize():
    answer = "hello " * 1500
    card = task_message(status="running", run_id="run_test", token="secret", details_url=None, replies=(answer,))
    sections = [block["text"]["text"] for block in card["blocks"] if block["type"] == "section"]
    assert all(len(part) <= 3000 for part in sections)
    assert "".join(sections) == answer
    with pytest.raises(ConnectivityHttpError):
        task_message(status="running", run_id="run_test", token="secret", details_url=None, replies=("x" * 32_001,))


async def stop(task, **changes):
    async with transaction(task.sessions) as db:
        row = await db.get(ProgressRecord, task.receipt.run_id)
        data = dict(
            reference=row.run_id,
            token=row.action_token,
            actor_id="U123",
            conversation_id="C123",
            message_id="1788422401.000100",
        )
        data.update(changes)
        return await ProgressActions().handle(db, await db.get(AccountRecord, ACCOUNT), ProviderActionDecision(**data))


@pytest.mark.parametrize("task", ["slack"], indirect=True)
async def test_slack_create_update_and_canonical_stop(task):
    assert (await task.service.scan()).failed == 0
    assert len(task.calls) == 1
    assert json.loads(task.calls[0].content)["thread_ts"] == "1788422400.000100"
    await stop(task, actor_id="U_OTHER")
    async with short_session(task.sessions) as db:
        assert not (await db.get(ProgressRecord, task.receipt.run_id)).stop_requested
    assert await stop(task) == {}
    assert (await task.service.scan()).failed == 0
    async with short_session(task.sessions) as db:
        row = await db.get(ProgressRecord, task.receipt.run_id)
        assert (await db.get(RunRecord, task.receipt.run_id)).status == "cancelled"
        assert row.done and row.rendered_status == "cancelled"
    assert [r.url.path for r in task.calls] == ["/api/chat.postMessage", "/api/chat.update"]
    assert STOP_ACTION_ID not in task.calls[-1].content.decode()
    assert await stop(task) == {}


@pytest.mark.parametrize("task", ["slack"], indirect=True)
async def test_initial_lost_response_never_posts_duplicate(task):
    def lost(request):
        task.calls.append(request)
        raise httpx2.ReadTimeout("fixture")

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(lost)) as http:
        task.service.delivery.http = http
        assert (await task.service.scan()).failed == 1
        await due(task)
        assert (await task.service.scan()).failed == 1
    assert len(task.calls) == 1


@pytest.mark.parametrize("task", ["slack"], indirect=True)
async def test_replies_share_message_and_keep_both_answers(replying):
    await replying.service.scan()
    first = await replying.reply("First answer")
    second = await replying.reply("Second answer")
    assert first["kind"] == second["kind"] == "succeeded"
    assert "receipt" not in first and "receipt" not in second
    from a13n_service.bots.connectivity.models import BotReplyRecord
    from sqlalchemy import select

    async with short_session(replying.sessions) as db:
        observations = (await db.scalars(select(BotReplyRecord))).all()
        assert len(observations) == 2
        assert all(row.receipt_json["message_ts"] == "1788422401.000100" for row in observations)
        assert all(row.receipt_json["root_thread_ts"] == "1788422400.000100" for row in observations)
    assert [r.url.path for r in replying.calls].count("/api/chat.postMessage") == 1
    text = json.loads(replying.calls[-1].content)["text"]
    assert "First answer" in text and "Second answer" in text


@pytest.mark.parametrize("task", ["slack"], indirect=True)
async def test_slack_explicit_reply_can_create_quiet_card(replying):
    async with transaction(replying.sessions) as db:
        (await db.get(ProgressRecord, replying.receipt.run_id)).done = True
    assert (await replying.service.scan()).examined == 0
    assert (await replying.reply("Explicit answer"))["kind"] == "succeeded"
    assert [r.url.path for r in replying.calls] == ["/api/chat.postMessage", "/api/chat.update"]


@pytest.mark.parametrize("task", ["slack"], indirect=True)
@pytest.mark.parametrize("mode", ["receive_only", "quiet", "main", "direct_auto", "direct_thread"])
async def test_slack_admission_and_placement(task, mode):
    from a13n_service.bots.progress.acceptance import accept_progress
    from a13n_service.temporal import utc_now

    async with transaction(task.sessions) as db:
        await db.delete(await db.get(ProgressRecord, task.receipt.run_id))
        config = task.batch.configuration
        if mode == "receive_only":
            config.native_actions = ()
        if mode == "quiet":
            config.provider_policy = {"interaction_mode": "chat"}
        if mode == "main":
            config.provider_policy = {"reply_mode": "main"}
        if mode.startswith("direct"):
            config.provider_context["conversation_kind"] = "im"
            config.provider_policy = {"reply_mode": "auto" if mode == "direct_auto" else "thread"}
        await accept_progress(db, task.batch, task.receipt, utc_now())
    await task.service.scan()
    if mode in {"receive_only", "quiet"}:
        assert not task.calls
    else:
        assert ("thread_ts" in json.loads(task.calls[0].content)) == (mode == "direct_thread")


@pytest.mark.parametrize("task", ["slack"], indirect=True)
async def test_definite_rejection_allows_later_delivery(task):
    def respond(request):
        task.calls.append(request)
        if len(task.calls) == 1:
            return httpx2.Response(429, headers={"retry-after": "1"})
        return httpx2.Response(200, json={"ok": True, "channel": "C123", "ts": "1788422401.000100"})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http:
        task.service.delivery.http = http
        assert (await task.service.scan()).failed == 1
        await due(task)
        assert (await task.service.scan()).failed == 0
    assert len(task.calls) == 2


@pytest.mark.parametrize("task", ["slack"], indirect=True)
async def test_lost_update_retries_existing_message_only(replying):
    await replying.service.scan()

    def lost(request):
        replying.calls.append(request)
        raise httpx2.ReadTimeout("fixture")

    previous = replying.service.delivery.http
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(lost)) as http:
        replying.service.delivery.http = http
        assert (await replying.reply("Retain uncertain answer"))["kind"] == "outcome_unknown"
    replying.service.delivery.http = previous
    await due(replying)
    assert (await replying.service.scan()).failed == 0
    assert [r.url.path for r in replying.calls].count("/api/chat.postMessage") == 1
    assert "Retain uncertain answer" in json.loads(replying.calls[-1].content)["text"]


async def test_socket_callback_routes_exact_installation():
    from types import SimpleNamespace

    from a13n_service.connectivity.transports.supervisor import _matches

    snapshot = SimpleNamespace(provider_key="slack", provider_config=_config())
    assert _matches(snapshot, callback())
    assert not _matches(snapshot, callback(team={"id": "OTHER"}))
    assert not _matches(snapshot, callback(enterprise={"id": "OTHER"}))


@pytest.mark.parametrize("task", ["slack"], indirect=True)
@pytest.mark.parametrize("transport", ["http", "websocket"])
async def test_ingress_stop_commits_before_ack_and_cancels_without_new_input(task, transport):
    from a13n_service.connectivity.ingress.admission import IngressEventService
    from a13n_service.connectivity.ingress.admission_models import IngressAdmissionRecord
    from a13n_service.connectivity.providers.registry import built_in_ingress_adapter_registry
    from a13n_service.connectivity.transports.configuration import connection_key
    from a13n_service.connectivity.transports.leases import ConnectionLeases
    from a13n_service.connectivity.transports.supervisor import _normalize
    from sqlalchemy import func, select

    await task.service.scan()
    async with transaction(task.sessions) as db:
        account = await db.get(AccountRecord, ACCOUNT)
        account.provider_config_json = {**account.provider_config_json, "event_transport": transport}
        token = (await db.get(ProgressRecord, task.receipt.run_id)).action_token
    ingress = IngressEventService(
        task.sessions,
        built_in_ingress_adapter_registry(),
        task.service.delivery.protector,
        request_max_bytes=1048576,
        workspace_pending_max_count=100,
        workspace_pending_max_bytes=1048576,
        account_pending_max_count=100,
        account_pending_max_bytes=1048576,
        batch_max_bytes=1048576,
        dedup_horizon_seconds=3600,
        clock=lambda: NOW,
        actions=ProgressActions(),
    )
    snapshot, _ = await ingress.load_socket_account(ACCOUNT)
    claim = None
    if transport == "websocket":
        claim = await ConnectionLeases(task.sessions, "slack-progress-test").claim(
            connection_key("slack", snapshot.provider_config), {ACCOUNT: snapshot.version}
        )
        assert claim is not None
    payload = callback(
        actions=[{"action_id": STOP_ACTION_ID, "value": json.dumps({"run_id": task.receipt.run_id, "token": token})}]
    )

    async def receive(value, *, tampered=False):
        if transport == "websocket":
            return await ingress.receive_socket(snapshot=snapshot, decision=_normalize(snapshot, value), claim=claim)
        body = urlencode({"payload": json.dumps(value)}).encode()
        request = _signed_request(body)
        request = request.model_copy(
            update={
                "body": body + b"x" if tampered else body,
                "headers": {**request.headers, "content-type": "application/x-www-form-urlencoded"},
                "content_type": "application/x-www-form-urlencoded",
            }
        )
        response = await ingress.receive(account_id=ACCOUNT, request=request)
        if tampered:
            assert response.status_code == 401
            return None
        assert response.status_code == 200
        return json.loads(response.body)

    if transport == "http":
        await receive(payload, tampered=True)
    assert await receive({**payload, "user": {"id": "U_OTHER"}}) == {}
    async with short_session(task.sessions) as db:
        assert not (await db.get(ProgressRecord, task.receipt.run_id)).stop_requested
    assert await receive(payload) == {}
    # Read in a separate session: ACK is returned only after the intent commits.
    async with short_session(task.sessions) as db:
        assert (await db.get(ProgressRecord, task.receipt.run_id)).stop_requested
        assert (await db.get(RunRecord, task.receipt.run_id)).status == "accepted"
    assert (await task.service.scan()).failed == 0
    assert await receive(payload) == {}
    assert (await task.service.scan()).examined == 0
    async with short_session(task.sessions) as db:
        row = await db.get(ProgressRecord, task.receipt.run_id)
        assert row.done and row.rendered_status == "cancelled"
        assert (await db.get(RunRecord, task.receipt.run_id)).status == "cancelled"
        assert await db.scalar(select(func.count()).select_from(RunRecord)) == 1
        assert await db.scalar(select(func.count()).select_from(IngressAdmissionRecord)) == 0
    assert [r.url.path for r in task.calls] == ["/api/chat.postMessage", "/api/chat.update"]
    assert STOP_ACTION_ID not in task.calls[-1].content.decode()
