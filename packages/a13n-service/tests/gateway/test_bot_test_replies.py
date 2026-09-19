"""Only a native reply containing the admitted test marker completes that test."""

from datetime import timedelta

import httpx2
import pytest
from a13n_service.bots.connectivity.models import BotTestRecord
from a13n_service.bots.connectivity.replies import BotReplyObserver
from a13n_service.bots.connectivity.setup_tests import get_bot_test
from a13n_service.connectivity.accounts.models import AccountRecord
from a13n_service.connectivity.accounts.target_models import AccountTargetRecord
from a13n_service.connectivity.providers.definition import InboundActionContext
from a13n_service.connectivity.providers.registry import require_native_provider
from a13n_service.interactions.models import RunRecord
from a13n_service.storage import transaction

from tests.hooks.support import RUN_ID, hook_actor
from tests.interactions.conftest import NOW, ORGANIZATION_ID, USER_ID, WORKSPACE_ID

from .test_bot_history import ACCOUNT_ID
from .test_bot_history import bot_history as bot_history
from .test_bot_replies import _records, _reply
from .test_bot_replies import reply_fixture as reply_fixture
from .test_session_previews import preview_queries as preview_queries

pytestmark = pytest.mark.anyio
MARKER = "btest_" + "a" * 32


async def _seed(sessions, *, variation="match"):
    async with transaction(sessions) as database:
        database.add(
            AccountTargetRecord(
                id="tgt_pilot",
                organization_id=ORGANIZATION_ID,
                workspace_id=WORKSPACE_ID,
                account_id=ACCOUNT_ID,
                target_kind="conversation",
                external_target_id="C1",
                receive_enabled=True,
                version=2 if variation == "target_changed" else 1,
                created_by_type="user",
                created_by_id=USER_ID,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        database.add(
            BotTestRecord(
                id=MARKER,
                organization_id=ORGANIZATION_ID,
                workspace_id=WORKSPACE_ID,
                account_id=ACCOUNT_ID,
                account_version=1,
                credential_generation=2 if variation == "generation" else 1,
                target_id="tgt_pilot",
                target_version=1,
                external_target_id="C1",
                created_at=NOW - timedelta(minutes=1),
                expires_at=NOW + timedelta(minutes=14),
                event_received_at=NOW,
                binding_id="bind_other" if variation == "binding" else "bind_first",
                accepted_at=None if variation == "unaccepted" else NOW,
                run_id=None if variation == "unaccepted" else RUN_ID,
                steer_id="steer_test" if variation == "steer" else None,
            )
        )


@pytest.mark.parametrize(
    "variation",
    ["match", "steer", "unrelated_reply", "unaccepted", "binding", "generation", "target_changed", "unknown"],
)
async def test_marker_and_trusted_binding_correlate_reply(reply_fixture, variation):
    sessions, context, _, observer = reply_fixture
    await _seed(sessions, variation=variation)

    def send(_request):
        if variation == "unknown":
            raise httpx2.ReadTimeout("fixture response lost")
        return httpx2.Response(200, json={"ok": True, "channel": "C1", "ts": "2.0"})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(send)) as http:
        await _reply(context, http).call_observed(
            {"text": "unrelated" if variation == "unrelated_reply" else MARKER}, observer
        )
    rows = await _records(sessions)
    expected = variation in {"match", "steer", "unknown"}
    assert rows[0].test_id == (MARKER if expected else None)
    result = (
        await get_bot_test(sessions, actor=hook_actor(), account_id=ACCOUNT_ID, test_id=MARKER, clock=lambda: NOW)
    ).latest
    assert bool(result.reply) == expected
    if expected:
        assert result.reply.status == ("outcome_unknown" if variation == "unknown" else "succeeded")
        assert result.reply.run_id == RUN_ID
    if variation == "target_changed":
        assert result.stale


@pytest.mark.parametrize("kind", ["text", "post"])
async def test_feishu_text_and_post_replies_correlate_without_storing_body(reply_fixture, kind):
    from tests.connectivity.test_lark import _config
    from tests.connectivity.test_lark_client import _AllowEndpoint, _token_response

    sessions, context, attempt, _ = reply_fixture
    await _seed(sessions)
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
        (await database.get(AccountRecord, ACCOUNT_ID)).provider_key = "lark"
        (await database.get(RunRecord, RUN_ID)).native_tool_contexts_json = [context.model_dump(mode="json")]
    observer = BotReplyObserver(
        sessions,
        attempt=attempt,
        context=context,
        workspace_id=WORKSPACE_ID,
        account_version=1,
        credential_generation=1,
        clock=lambda: NOW,
    )

    def send(request):
        if request.url.path.endswith("tenant_access_token/internal"):
            return _token_response()
        return httpx2.Response(
            200, json={"code": 0, "data": {"message_id": "om_reply", "root_id": "om_root", "thread_id": "omt_thread"}}
        )

    content = (
        {"kind": "text", "text": MARKER}
        if kind == "text"
        else {"kind": "post", "title": "Test", "paragraphs": ["private text", MARKER]}
    )
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(send)) as http:
        reply = require_native_provider("lark").inbound_actions(
            InboundActionContext(
                provider_context=context.provider_context,
                action_policy=context.action_policy,
                configuration=_config(),
                credentials={"app_secret": "private-secret"},
                http=http,
                endpoints=_AllowEndpoint(),
            )
        )["lark.reply"]
        assert await reply.call_observed({"content": content}, observer) == {"kind": "succeeded"}
    result = (
        await get_bot_test(sessions, actor=hook_actor(), account_id=ACCOUNT_ID, test_id=MARKER, clock=lambda: NOW)
    ).latest
    assert result.reply.test_id == MARKER and result.reply.receipt.message_id == "om_reply"
    assert "private" not in result.model_dump_json()


async def test_later_unknown_reply_does_not_erase_confirmed_test(reply_fixture):
    sessions, context, _, observer = reply_fixture
    await _seed(sessions)
    calls = []

    def send(request):
        calls.append(request)
        if len(calls) == 2:
            raise httpx2.ReadTimeout("fixture response lost")
        return httpx2.Response(200, json={"ok": True, "channel": "C1", "ts": "2.0"})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(send)) as http:
        reply = _reply(context, http)
        await reply.call_observed({"text": MARKER}, observer)
        await reply.call_observed({"text": MARKER}, observer)
    result = (
        await get_bot_test(sessions, actor=hook_actor(), account_id=ACCOUNT_ID, test_id=MARKER, clock=lambda: NOW)
    ).latest
    assert result.reply.status == "succeeded"
    assert len(calls) == len(await _records(sessions)) == 2
