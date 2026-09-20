"""Schedules require human confirmation, retain scope, and accept each occurrence once."""

import json
from datetime import timedelta
from types import SimpleNamespace

import anyio
import httpx2
import pytest
from a13n_service.bots.progress.actions import ProgressActions
from a13n_service.bots.progress.models import ProgressRecord
from a13n_service.bots.routines.cards import RoutineCards, render
from a13n_service.bots.routines.domain import ProposeRoutine, RoutineDefinition, Schedule
from a13n_service.bots.routines.models import RoutineRecord
from a13n_service.bots.routines.scheduler import RoutineScheduler, _LostClaim
from a13n_service.bots.routines.service import RoutineService
from a13n_service.connectivity.accounts.models import AccountRecord
from a13n_service.connectivity.ingress.provider import ProviderActionDecision
from a13n_service.connectivity.native_context import InboundRunContext
from a13n_service.connectivity.providers.slack.adapter import CONTEXT_VERSION, SlackAccountConfig, normalize_payload
from a13n_service.iam import PrincipalRef
from a13n_service.interactions.models import RunRecord
from a13n_service.storage import short_session, transaction
from a13n_service.temporal import assume_utc, utc_now
from sqlalchemy import func, select

from .test_bot_progress import ACCOUNT, EXECUTOR, task  # noqa: F401
from .test_slack import _config

pytestmark = [pytest.mark.anyio, pytest.mark.parametrize("task", ["slack"], indirect=True)]


@pytest.fixture
async def routines(task, credential_protector, monkeypatch):  # noqa: F811
    from a13n_service.bots.memory.behavior import ConversationMemory
    from a13n_service.memory.behaviors import MemoryBehaviors

    from tests.memory.selection_support import ordinary_memory

    ordinary = ordinary_memory(task.sessions).default
    monkeypatch.setattr(
        task.service.commands.acceptance,
        "_bindings",
        MemoryBehaviors(task.sessions, default=ordinary, behaviors=(ConversationMemory(ordinary.service, None),)),
    )
    context = InboundRunContext(
        account_id=ACCOUNT,
        binding_id="binding_routine",
        provider_key="slack",
        execution_principal_ref=PrincipalRef(principal_type="service_account", principal_id=EXECUTOR),
        provider_context_version=CONTEXT_VERSION,
        provider_context=task.batch.configuration.provider_context,
        action_policy={"reply_mode": "thread"},
        allowed_actions=("slack.reply", "slack.read_messages"),
    )
    async with transaction(task.sessions) as db:
        run = await db.get(RunRecord, task.receipt.run_id)
        run.trigger_type = "inbound"
        run.native_tool_contexts_json = [context.model_dump(mode="json")]
    service = RoutineService(task.sessions)
    cards = RoutineCards(task.sessions, task.service.delivery.http, credential_protector)
    return SimpleNamespace(
        task=task,
        context=context,
        service=service,
        cards=cards,
        scheduler=RoutineScheduler(task.sessions, task.service.commands, cards),
    )


def definition(*, once=False):
    return RoutineDefinition(
        title="Channel digest",
        prompt="Summarize the channel and reply here.",
        schedule=(
            Schedule(timezone="Asia/Shanghai", at=utc_now() + timedelta(hours=1))
            if once
            else Schedule(timezone="Asia/Shanghai", time_of_day="09:00", weekdays=(0, 1, 2, 3, 4))
        ),
    )


async def propose(routines, **kwargs):
    arguments = ProposeRoutine(request_key="digest", definition=definition(), **kwargs)
    async with transaction(routines.task.sessions) as db:
        result = await routines.service.propose(
            db, run_id=routines.task.receipt.run_id, context=routines.context, arguments=arguments
        )
    return result["routine_id"]


async def click(routines, identifier, operation="confirm", **overrides):
    async with transaction(routines.task.sessions) as db:
        row = await db.get(RoutineRecord, identifier)
        account = await db.get(AccountRecord, ACCOUNT)
        action = ProviderActionDecision.model_validate(
            {
                "action": f"routine_{operation}",
                "reference": row.id,
                "token": row.action_token,
                "actor_id": "U123",
                "conversation_id": "C123",
                "message_id": row.message_id,
                **overrides,
            }
        )
        await ProgressActions().handle(db, account, action)
        return action


async def activate(routines):
    identifier = await propose(routines)
    await routines.cards.publish_one()
    await click(routines, identifier)
    return identifier


async def make_due(routines, identifier):
    async with transaction(routines.task.sessions) as db:
        row = await db.get(RoutineRecord, identifier)
        row.next_run_at = utc_now() - timedelta(minutes=1)
        row.available_at = utc_now() - timedelta(seconds=1)


async def test_confirmation_scope_and_replay(routines):
    identifier = await propose(routines)
    assert await propose(routines) == identifier
    assert await routines.scheduler.claim() is None
    await routines.cards.publish_one()
    await click(routines, identifier, actor_id="U_other")
    await click(routines, identifier, conversation_id="C_other")
    await click(routines, identifier, token="forged")
    async with short_session(routines.task.sessions) as db:
        assert (await db.get(RoutineRecord, identifier)).state == "draft"
    original = await click(routines, identifier)
    async with transaction(routines.task.sessions) as db:
        row = await db.get(RoutineRecord, identifier)
        next_at, version = row.next_run_at, row.version
        await ProgressActions().handle(db, await db.get(AccountRecord, ACCOUNT), original)
        assert row.next_run_at == next_at and row.version == version
        assert row.state == "active"


async def test_pause_resume_delete_and_edit(routines):
    identifier = await activate(routines)
    await click(routines, identifier, "pause")
    async with short_session(routines.task.sessions) as db:
        row = await db.get(RoutineRecord, identifier)
        assert row.state == "paused" and row.next_run_at is None
    await click(routines, identifier, "resume")
    async with transaction(routines.task.sessions) as db:
        arguments = ProposeRoutine(
            request_key="edit",
            routine_id=identifier,
            definition=definition().model_copy(update={"title": "Updated digest"}),
        )
        await routines.service.propose(
            db, run_id=routines.task.receipt.run_id, context=routines.context, arguments=arguments
        )
        row = await db.get(RoutineRecord, identifier)
        assert row.definition_json["title"] == "Channel digest"
    await click(routines, identifier)
    async with short_session(routines.task.sessions) as db:
        assert (await db.get(RoutineRecord, identifier)).definition_json["title"] == "Updated digest"
    await click(routines, identifier, "delete")
    await click(routines, identifier, "resume")
    async with short_session(routines.task.sessions) as db:
        assert (await db.get(RoutineRecord, identifier)).state == "deleted"


async def test_expired_proposal_and_discard(routines):
    identifier = await propose(routines)
    await routines.cards.publish_one()
    async with transaction(routines.task.sessions) as db:
        row = await db.get(RoutineRecord, identifier)
        row.proposal_expires_at = utc_now() - timedelta(seconds=1)
    await click(routines, identifier)
    assert await routines.scheduler.claim() is None
    await click(routines, identifier, "cancel")
    async with short_session(routines.task.sessions) as db:
        assert (await db.get(RoutineRecord, identifier)).state == "deleted"


async def test_acceptance_is_atomic_and_survives_restart(routines):
    identifier = await activate(routines)
    await make_due(routines, identifier)
    claim = await routines.scheduler.claim()
    assert claim is not None
    restarted = RoutineScheduler(routines.task.sessions, routines.task.service.commands, routines.cards)
    assert await restarted.claim() is None
    await restarted.execute(claim)
    with pytest.raises(_LostClaim):
        await restarted.execute(claim)
    async with short_session(routines.task.sessions) as db:
        row = await db.get(RoutineRecord, identifier)
        run = await db.get(RunRecord, row.last_run_id)
        assert run.trigger_type == "bot_schedule"
        assert run.native_tool_contexts_json[0]["action_policy"]["reply_mode"] == "main"
        progress = await db.get(ProgressRecord, run.id)
        assert progress.conversation_id == "C123" and not progress.reply_in_thread
        assert assume_utc(row.next_run_at) > utc_now()
        assert (
            await db.scalar(select(func.count()).select_from(RunRecord).where(RunRecord.trigger_type == "bot_schedule"))
            == 1
        )


async def test_pause_fences_claimed_occurrence(routines):
    identifier = await activate(routines)
    await make_due(routines, identifier)
    claim = await routines.scheduler.claim()
    await click(routines, identifier, "pause")
    with pytest.raises(_LostClaim):
        await routines.scheduler.execute(claim)
    async with short_session(routines.task.sessions) as db:
        assert (await db.get(RoutineRecord, identifier)).last_run_id is None


async def test_revocation_pauses_future_work(routines):
    identifier = await activate(routines)
    await make_due(routines, identifier)
    async with transaction(routines.task.sessions) as db:
        account = await db.get(AccountRecord, ACCOUNT)
        account.receive_enabled = False
    result = await routines.scheduler.scan()
    assert result.failed == 1
    async with short_session(routines.task.sessions) as db:
        row = await db.get(RoutineRecord, identifier)
        assert row.state == "paused" and row.last_run_id is None
        assert row.last_error == "execution_authority_changed"
    await click(routines, identifier, "delete")


async def test_concurrent_claims_and_no_overlap(routines):
    identifier = await activate(routines)
    await make_due(routines, identifier)
    claims = []

    async def claim():
        claims.append(await routines.scheduler.claim())

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(claim)
        tasks.start_soon(claim)
    accepted = [value for value in claims if value is not None]
    assert len(accepted) == 1
    await routines.scheduler.execute(accepted[0])
    await make_due(routines, identifier)
    assert await routines.scheduler.claim() is None


async def test_uncertain_initial_card_is_not_duplicated(routines, credential_protector):
    identifier = await propose(routines)
    calls = []

    def fail(request):
        calls.append(request)
        raise httpx2.ReadTimeout("fixture")

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(fail)) as http:
        cards = RoutineCards(routines.task.sessions, http, credential_protector)
        await cards.publish_one()
        async with transaction(routines.task.sessions) as db:
            row = await db.get(RoutineRecord, identifier)
            row.card_available_at = utc_now() - timedelta(seconds=1)
        await cards.publish_one()
    assert len(calls) == 1
    async with short_session(routines.task.sessions) as db:
        row = await db.get(RoutineRecord, identifier)
        assert row.card_error == "delivery_outcome_unknown"
        assert row.state == "draft"


async def test_other_channel_cannot_list_or_change(routines):
    identifier = await activate(routines)
    other = routines.context.model_copy(update={"provider_context": {"channel_id": "C_other"}})
    async with short_session(routines.task.sessions) as db:
        assert (await routines.service.list(db, context=other, cursor=None))["items"] == []
    async with transaction(routines.task.sessions) as db:
        progress = await db.get(ProgressRecord, routines.task.receipt.run_id)
        progress.requester_ids = ["U_other"]
        with pytest.raises(ValueError, match="not_owned"):
            await routines.service.propose(
                db,
                run_id=routines.task.receipt.run_id,
                context=routines.context,
                arguments=ProposeRoutine(request_key="edit", routine_id=identifier, definition=definition()),
            )


async def test_card_and_slack_callback(routines):
    identifier = await propose(routines)
    await routines.cards.publish_one()
    async with short_session(routines.task.sessions) as db:
        row = await db.get(RoutineRecord, identifier)
        card = render(row)
        button = card["blocks"][-2]["elements"][0]
        config = SlackAccountConfig.model_validate(_config())
        payload = {
            "type": "block_actions",
            "api_app_id": config.api_app_id,
            "team": {"id": config.team_id},
            "enterprise": None,
            "user": {"id": "U123"},
            "container": {"type": "message", "channel_id": "C123", "message_ts": row.message_id},
            "actions": [button],
        }
        decision = normalize_payload(payload, config, utc_now())
        assert decision.action == "routine_confirm" and decision.reference == identifier
        rendered = json.dumps(card)
        assert "09:00 (Asia/Shanghai)" in rendered
        assert "+00:00" not in rendered
        row.state = "completed"
        row.proposal_json = None
        assert "Run submitted" in render(row)["text"]


async def test_once_and_missed_intervals_do_not_replay_backlog(routines):
    identifier = await activate(routines)
    async with transaction(routines.task.sessions) as db:
        row = await db.get(RoutineRecord, identifier)
        row.definition_json = definition(once=True).model_dump(mode="json")
        row.definition_json["schedule"]["at"] = (utc_now() - timedelta(hours=2)).isoformat()
        row.next_run_at = utc_now() - timedelta(hours=2)
    claim = await routines.scheduler.claim()
    await routines.scheduler.execute(claim)
    async with short_session(routines.task.sessions) as db:
        row = await db.get(RoutineRecord, identifier)
        assert row.state == "completed" and row.next_run_at is None
    assert await routines.scheduler.claim() is None


async def test_expired_worker_lease_can_be_reclaimed(routines):
    identifier = await activate(routines)
    await make_due(routines, identifier)
    stale = await routines.scheduler.claim()
    async with transaction(routines.task.sessions) as db:
        (await db.get(RoutineRecord, identifier)).lease_until = utc_now() - timedelta(seconds=1)
    fresh = await routines.scheduler.claim()
    assert fresh.lease != stale.lease
    with pytest.raises(_LostClaim):
        await routines.scheduler.execute(stale)
    await routines.scheduler.execute(fresh)


async def test_scheduled_runs_and_inline_children_have_no_management_tools(routines):
    from unittest.mock import AsyncMock

    from a13n_service.bots.routines.runtime import RoutineTools

    tools = RoutineTools(routines.service)
    scope = SimpleNamespace(native_tool_contexts=())
    attempt = SimpleNamespace(run_id=routines.task.receipt.run_id)
    assert await tools(scope, AsyncMock(), attempt) is None
    scope.native_tool_contexts = (routines.context,)
    assert await tools(scope, AsyncMock(), attempt) is not None
    async with transaction(routines.task.sessions) as db:
        run = await db.get(RunRecord, attempt.run_id)
        run.trigger_type = "bot_schedule"
    assert await tools(scope, AsyncMock(), attempt) is None


async def test_card_does_not_use_replaced_account(routines):
    identifier = await propose(routines)
    async with transaction(routines.task.sessions) as db:
        account = await db.get(AccountRecord, ACCOUNT)
        account.version += 1
    await routines.cards.publish_one()
    async with short_session(routines.task.sessions) as db:
        row = await db.get(RoutineRecord, identifier)
        assert row.message_id is None
        assert row.card_error == "account_unavailable"
        assert row.card_available_at is None


async def test_invalid_task_reference_is_recoverable_tool_result(routines, monkeypatch):
    from unittest.mock import AsyncMock

    from a13n_service.bots.routines import runtime

    capability = AsyncMock(side_effect=lambda **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(runtime, "local_capability", capability)
    monkeypatch.setattr(runtime, "authorized_account", AsyncMock())
    scope = SimpleNamespace(
        native_tool_contexts=(routines.context,), actor=None, organization_id="org", workspace_id="workspace"
    )
    guard = AsyncMock()
    tools = await runtime.RoutineTools(routines.service)(
        scope, guard, SimpleNamespace(run_id=routines.task.receipt.run_id)
    )
    arguments = ProposeRoutine(request_key="edit", routine_id="routine_missing", definition=definition()).model_dump(
        mode="json"
    )
    result = await tools.handler("propose", arguments)
    assert result["error"] == "routine_unavailable"
    assert "No change was applied" in result["message"]
    identifier = await propose(routines)
    listing = await tools.handler("list", {})
    assert listing["value"]["items"][0]["id"] == identifier
    arguments["routine_id"] = identifier
    result = await tools.handler("propose", arguments)
    assert result["value"]["confirmation_required"]


async def test_authority_refreshes_account_loaded_before_lock(routines):
    from a13n_service.bots.progress.authority import ProgressUnavailable
    from a13n_service.bots.routines.authority import authorize_routine

    identifier = await activate(routines)
    async with short_session(routines.task.sessions) as db:
        row = await db.get(RoutineRecord, identifier)
        cached = await db.get(AccountRecord, ACCOUNT)
        old_version = cached.version
        async with transaction(routines.task.sessions) as update:
            account = await update.get(AccountRecord, ACCOUNT)
            account.version += 1
        assert cached.version == old_version
        with pytest.raises(ProgressUnavailable):
            await authorize_routine(db, row)
