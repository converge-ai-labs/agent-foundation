"""Feishu event subscriptions use native confirmation and result cards."""

import json

import pytest
from a13n_service.bots.progress.actions import ProgressActions
from a13n_service.bots.progress.models import ProgressRecord
from a13n_service.bots.routines.lark_cards import render
from a13n_service.bots.routines.models import EventOccurrenceRecord, RoutineRecord
from a13n_service.connectivity.accounts.models import AccountRecord
from a13n_service.connectivity.providers.lark.wire import LarkIdentity, authenticate_and_normalize
from a13n_service.interactions.models import RunRecord
from a13n_service.storage import short_session, transaction
from sqlalchemy import func, select

from tests.gateway.test_commands import _complete_run

from .test_bot_event_tasks import events as events
from .test_bot_event_tasks import send, subscribe
from .test_bot_progress import ACCOUNT, task  # noqa: F401
from .test_bot_routines import routines  # noqa: F401
from .test_lark import _ENCRYPT_KEY, _VERIFICATION_TOKEN, NOW, _encrypted_request, _payload

pytestmark = [pytest.mark.anyio, pytest.mark.parametrize("task", ["lark"], indirect=True)]


async def callback(events, identifier, operation="confirm", **overrides):
    async with short_session(events.task.sessions) as db:
        row = await db.get(RoutineRecord, identifier)
        card = render(row, language="zh_cn")
        button = next(button for button in card["elements"][-2]["actions"] if button["value"]["action"] == operation)
        payload = _payload()
        payload["header"]["event_type"] = "card.action.trigger"
        payload["event"] = {
            "host": "im_message",
            "operator": {"open_id": overrides.get("actor", events.owner)},
            "context": {
                "open_chat_id": overrides.get("conversation", events.conversation),
                "open_message_id": overrides.get("message", row.message_id),
            },
            "action": {"value": {**button["value"], "token": overrides.get("token", row.action_token)}},
        }
    decision = authenticate_and_normalize(
        _encrypted_request(payload),
        identity=LarkIdentity("cli_app", "tenant-1", "ou_bot"),
        encrypt_key=_ENCRYPT_KEY,
        verification_token=_VERIFICATION_TOKEN,
        received_at=NOW,
    )
    async with transaction(events.task.sessions) as db:
        await ProgressActions().handle(db, await db.get(AccountRecord, ACCOUNT), decision)


async def test_confirmed_event_delivers_only_to_originating_group(events):
    identifier = await subscribe(events, confirm=False)
    for overrides in (
        {"actor": "ou_other"},
        {"conversation": "oc_other"},
        {"message": "om_other"},
        {"token": "forged"},
    ):
        await callback(events, identifier, **overrides)
    async with short_session(events.task.sessions) as db:
        assert (await db.get(RoutineRecord, identifier)).state == "draft"
    await callback(events, identifier)
    await send(events, delivery="first")
    await send(events, delivery="redelivery")
    await events.scheduler.execute(await events.scheduler.claim())
    async with transaction(events.task.sessions) as db:
        row = await db.get(RoutineRecord, identifier)
        run_id = row.last_run_id
        run = await db.get(RunRecord, run_id)
        context = run.native_tool_contexts_json[0]
        assert context["provider_key"] == "lark" and context["allowed_actions"] == ["lark.reply"]
        assert context["provider_context"]["chat_id"] == events.conversation
        progress = await db.get(ProgressRecord, run_id)
        assert progress.requester_ids == [events.owner] and not progress.reply_in_thread
        # A completed Run alone must not invent a reply; seed an explicitly accepted reply.
        progress.replies_json = ["Event result fixture: PR #8 merged"]
    await _complete_run(events.task.sessions, events.task.objects, run_id=run_id)
    await events.task.service.scan()
    await events.cards.publish_one()
    async with short_session(events.task.sessions) as db:
        progress = await db.get(ProgressRecord, run_id)
        assert progress.done and progress.rendered_reply_count == 1
        assert progress.rendered_status == "completed"
        assert await db.scalar(select(func.count()).select_from(EventOccurrenceRecord)) == 1
        card = render(await db.get(RoutineRecord, identifier), language="zh_cn")
        assert "事件任务已结束 · 已提交执行" in json.dumps(card, ensure_ascii=False)
        assert not any(element["tag"] == "action" for element in card["elements"])
    creates = [
        json.loads(call.content)
        for call in events.task.calls
        if call.method == "POST" and call.url.path == "/open-apis/im/v1/messages"
    ]
    assert creates and all(body["receive_id"] == events.conversation for body in creates)
    assert any("确认事件任务" in body["content"] for body in creates)
    assert any("Event result fixture" in call.content.decode() for call in events.task.calls if call.method == "PATCH")
    assert await events.scheduler.claim() is None


async def test_event_management_buttons_pause_resume_and_delete(events):
    identifier = await subscribe(events, ci=True, confirm=False)
    await callback(events, identifier)
    await events.cards.publish_one()
    await callback(events, identifier, "pause")
    await send(events, ci=True)
    assert await events.scheduler.claim() is None
    await events.cards.publish_one()
    await callback(events, identifier, "resume")
    assert await events.scheduler.claim() is None
    await send(events, ci=True, run=101)
    assert await events.scheduler.claim() is not None
    await events.cards.publish_one()
    await callback(events, identifier, "delete")
    async with short_session(events.task.sessions) as db:
        assert (await db.get(RoutineRecord, identifier)).state == "deleted"
    assert await events.scheduler.claim() is None


@pytest.mark.parametrize("language", ["zh_cn", "en_us"])
@pytest.mark.parametrize("ci", [False, True])
async def test_card_explains_event_condition_source_and_sharing(events, language, ci):
    identifier = await subscribe(events, ci=ci, confirm=False)
    async with short_session(events.task.sessions) as db:
        row = await db.get(RoutineRecord, identifier)
        card = render(row, language=language)
        text = json.dumps(card, ensure_ascii=False)
        assert "GitHub events" in text and "42" in text
        if language == "zh_cn":
            assert "确认事件任务" in text and "当前群聊" in text and "信息可能会发送到本群" in text
            assert ("每次匹配都触发" if ci else "仅触发一次") in text
            assert ("分支: main" if ci else "PR 编号: 8") in text
        else:
            assert "Confirm event task" in text and "Source information may be shared here" in text
            assert ("each matching event" if ci else "Run once") in text
        assert "过期" not in text and "expired" not in text
        assert all(element["text"]["tag"] == "plain_text" for element in card["elements"] if element["tag"] == "div")
    await callback(events, identifier)
    async with short_session(events.task.sessions) as db:
        text = json.dumps(render(await db.get(RoutineRecord, identifier), language=language), ensure_ascii=False)
        assert ("等待匹配事件" if language == "zh_cn" else "Watching for matching events") in text
