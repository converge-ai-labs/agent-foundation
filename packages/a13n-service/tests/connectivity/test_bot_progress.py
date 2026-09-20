"""Task cards share canonical Run authority and survive delivery retries."""

import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx2
import pytest
from a13n_service.bots.progress.acceptance import accept_progress
from a13n_service.bots.progress.actions import ProgressActions
from a13n_service.bots.progress.domain import TaskProgress
from a13n_service.bots.progress.models import ProgressRecord
from a13n_service.bots.progress.service import ProgressService
from a13n_service.connectivity.accounts.models import AccountRecord
from a13n_service.connectivity.ingress.provider import ProviderActionDecision, ProviderRequestError
from a13n_service.connectivity.providers.lark.progress import task_card
from a13n_service.connectivity.providers.lark.wire import LarkIdentity, authenticate_and_normalize
from a13n_service.iam import AuthenticatedActor, PrincipalRef
from a13n_service.iam.models import RoleBindingRecord, ServiceAccountRecord
from a13n_service.interactions.domain import RunStatus
from a13n_service.interactions.models import RunRecord
from a13n_service.storage import short_session, transaction
from a13n_service.temporal import assume_utc, utc_now
from sqlalchemy import func, select

from tests.gateway.test_commands import _commands, _Freezing, _frozen, _Preparation, _request
from tests.interactions.conftest import AGENT_ID, ORGANIZATION_ID, WORKSPACE_ID, _seed_interaction_database

from .conftest import USER_ID
from .test_lark import _ENCRYPT_KEY, _VERIFICATION_TOKEN, NOW, _config, _encrypted_request, _payload

pytestmark = pytest.mark.anyio
ACCOUNT = "acct_aaaaaaaaaaaaaaaa"
EXECUTOR = "sa_aaaaaaaaaaaaaaaa"


@pytest.fixture
async def task(connectivity_sessions, connectivity_objects, credential_protector, request):
    from .test_slack import _config as slack_config

    provider = getattr(request, "param", "lark")
    slack = provider == "slack"
    sessions = connectivity_sessions
    await _seed_interaction_database(sessions)
    now = utc_now()
    async with transaction(sessions) as db:
        db.add(
            ServiceAccountRecord(
                id=EXECUTOR,
                organization_id=ORGANIZATION_ID,
                workspace_id=WORKSPACE_ID,
                name="Progress",
                normalized_name="progress",
                status="active",
                created_at=now,
                updated_at=now,
            )
        )
        await db.flush()
        db.add(
            RoleBindingRecord(
                id="rb_progress",
                organization_id=ORGANIZATION_ID,
                workspace_id=WORKSPACE_ID,
                principal_type="service_account",
                principal_id=EXECUTOR,
                resource_type="workspace",
                resource_id=WORKSPACE_ID,
                role_key="runner",
                created_by_user_id=USER_ID,
                created_at=now,
                updated_at=now,
            )
        )
        account = AccountRecord(
            id=ACCOUNT,
            organization_id=ORGANIZATION_ID,
            workspace_id=WORKSPACE_ID,
            name="Progress",
            normalized_name="progress",
            provider_key=provider,
            provider_config_version=f"{provider}_http_v1",
            provider_config_json=slack_config() if slack else _config(),
            identity_digest="b" * 64,
            status="active",
            version=1,
            credential_generation=0,
            receive_enabled=True,
            default_agent_id=AGENT_ID,
            execution_service_account_id=EXECUTOR,
            created_by_type="service_account",
            created_by_id=EXECUTOR,
            created_at=now,
            updated_at=now,
        )
        account.replace_credential(
            '{"app_secret":"fixture-secret","verification_token":"verification-token","encrypt_key":"encrypt-key"}',
            credential_protector,
        )
        if slack:
            account.replace_credential(
                '{"bot_token":"fixture-token","signing_secret":"signing-secret"}', credential_protector
            )
        db.add(account)
    commands = _commands(sessions, connectivity_objects, _Preparation(), _Freezing([_frozen()]))
    actor = AuthenticatedActor(
        principal=PrincipalRef(principal_type="service_account", principal_id=EXECUTOR),
        auth_method="internal",
        credential_id="progress-fixture",
        boundary_workspace_id=WORKSPACE_ID,
    )
    receipt = await commands.runs.start(
        actor=actor, workspace_id=WORKSPACE_ID, idempotency_key="progress", request=_request()
    )
    batch = SimpleNamespace(
        configuration=SimpleNamespace(
            provider_key=provider,
            native_actions=(f"{provider}.reply",),
            provider_policy={},
            account_id=ACCOUNT,
            account_version=1,
            provider_context=(
                {
                    "channel_id": "C123",
                    "message_ts": "1788422400.000200",
                    "root_thread_ts": "1788422400.000100",
                    "conversation_kind": "channel",
                }
                if slack
                else {"message_id": "om_source", "chat_id": "oc_chat"}
            ),
        ),
        agent_input=SimpleNamespace(
            structured_content={"events": [{"actor": {"user_id": "U123"} if slack else {"open_id": "ou_owner"}}]}
        ),
    )
    async with transaction(sessions) as db:
        await accept_progress(db, batch, receipt, now)
    calls = []

    def respond(request):
        calls.append(request)
        if request.url.host == "slack.com":
            return httpx2.Response(200, json={"ok": True, "channel": "C123", "ts": "1788422401.000100"})
        if "auth/v3" in request.url.path:
            return httpx2.Response(200, json={"code": 0, "tenant_access_token": "fixture-token", "expire": 7200})
        return httpx2.Response(200, json={"code": 0, "data": {"message_id": "om_card"}})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http:
        service = ProgressService(
            sessions,
            commands,
            http,
            SimpleNamespace(validate=AsyncMock(return_value="https://open.feishu.cn")),
            credential_protector,
            public_origin="https://console.example.com",
        )
        yield SimpleNamespace(
            sessions=sessions,
            service=service,
            receipt=receipt,
            calls=calls,
            objects=connectivity_objects,
            batch=batch,
            provider=provider,
        )


async def due(task):
    async with transaction(task.sessions) as db:
        row = await db.get(ProgressRecord, task.receipt.run_id)
        row.available_at = utc_now() - timedelta(seconds=1)


async def action(task, **changes):
    async with transaction(task.sessions) as db:
        row = await db.get(ProgressRecord, task.receipt.run_id)
        data = dict(
            action="stop",
            reference=row.run_id,
            token=row.action_token,
            actor_id="ou_owner",
            conversation_id="oc_chat",
            message_id="om_card",
        )
        data.update(changes)
        account = await db.get(AccountRecord, ACCOUNT)
        return await ProgressActions().handle(db, account, ProviderActionDecision(**data))


async def test_create_update_stop_is_canonical_and_idempotent(task):
    assert (await task.service.scan()).failed == 0
    messages = [r for r in task.calls if "/im/" in r.url.path]
    assert len(messages) == 1 and messages[0].method == "POST"
    card = json.loads(json.loads(messages[0].content)["content"])
    assert (
        f"https://console.example.com/workspace/test/sessions/{task.receipt.session_id}/threads/{task.receipt.thread_id}/runs/{task.receipt.run_id}"
        in json.dumps(card)
    )
    for _ in range(2):
        assert (await action(task))["toast"]["type"] == "info"
    assert (await task.service.scan()).failed == 0
    async with short_session(task.sessions) as db:
        run = await db.get(RunRecord, task.receipt.run_id)
        row = await db.get(ProgressRecord, run.id)
        assert run.status == "cancelled"
        assert row.done and row.message_id == "om_card" and row.rendered_status == "cancelled"
        assert await db.scalar(select(func.count()).select_from(RunRecord)) == 1
    assert [r.method for r in task.calls if "/im/" in r.url.path] == ["POST", "PATCH"]
    assert (await action(task))["toast"]["content"] == "This task has already ended."
    assert (await task.service.scan()).examined == 0


@pytest.mark.parametrize(
    "changed",
    [
        {"actor_id": "ou_other"},
        {"message_id": "om_forged"},
        {"conversation_id": "oc_other"},
        {"token": "forged"},
        {"reference": "run_missing"},
    ],
)
async def test_foreign_controls_rejected(task, changed):
    await task.service.scan()
    assert (await action(task, **changed))["toast"]["type"] == "error"
    async with short_session(task.sessions) as db:
        assert not (await db.get(ProgressRecord, task.receipt.run_id)).stop_requested


async def test_stale_account_and_lost_lease_cannot_update(task):
    run_id, old_lease = await task.service._claim()
    assert await task.service._claim() is None
    async with transaction(task.sessions) as db:
        row = await db.get(ProgressRecord, run_id)
        row.lease_until = utc_now() - timedelta(seconds=1)
    _, new_lease = await task.service._claim()
    await task.service.delivery.finish(run_id, old_lease, done=True, message_id="forged")
    async with transaction(task.sessions) as db:
        assert (await db.get(ProgressRecord, run_id)).lease_token == new_lease
        account = await db.get(AccountRecord, ACCOUNT)
        account.version += 1
    with pytest.raises(ValueError, match="source_changed"):
        await task.service.delivery.load(run_id, new_lease)
    assert not task.calls


async def test_restart_reuses_existing_card_and_revocation_blocks_stop(task):
    await task.service.scan()
    await due(task)
    original = task.service
    task.service = ProgressService(
        original.sessions,
        original.commands,
        original.delivery.http,
        original.delivery.endpoints,
        original.delivery.protector,
        public_origin=original.delivery.public_origin,
    )
    await task.service.scan()
    assert len([r for r in task.calls if "/im/" in r.url.path]) == 1
    await action(task)
    async with transaction(task.sessions) as db:
        await db.delete(await db.get(RoleBindingRecord, "rb_progress"))
    assert (await task.service.scan()).failed == 1
    async with short_session(task.sessions) as db:
        assert (await db.get(RunRecord, task.receipt.run_id)).status == "accepted"


@pytest.mark.parametrize("state", list(RunStatus))
def test_cards_only_offer_stop_for_nonterminal_state(state):
    progress = TaskProgress("run_test", state, stopping=True)
    card = task_card(status=progress.display_status, run_id=progress.run_id, token="secret", details_url=None)
    assert '"action": "stop"' not in json.dumps(card)
    assert progress.display_status == (state.value if progress.sealed else "stopping")
    normal = task_card(status=state.value, run_id=progress.run_id, token="secret", details_url=None)
    assert ('"action": "stop"' in json.dumps(normal)) == (not progress.sealed)


def test_authenticated_card_callback_is_not_model_input():
    payload = _payload()
    payload["header"]["event_type"] = "card.action.trigger"
    payload["event"] = {
        "host": "im_message",
        "operator": {"open_id": "ou_owner"},
        "context": {"open_chat_id": "oc_chat", "open_message_id": "om_card"},
        "action": {"value": {"kind": "a13n.task_control.v1", "action": "stop", "run_id": "run_test", "token": "token"}},
    }
    identity = LarkIdentity("cli_app", "tenant-1", "ou_bot")
    decision = authenticate_and_normalize(
        _encrypted_request(payload),
        identity=identity,
        encrypt_key=_ENCRYPT_KEY,
        verification_token=_VERIFICATION_TOKEN,
        received_at=NOW,
    )
    assert isinstance(decision, ProviderActionDecision)
    assert decision.actor_id == "ou_owner"
    payload["event"]["action"]["value"]["token"] = "invalid-\u2603"
    with pytest.raises(ProviderRequestError) as malformed:
        authenticate_and_normalize(
            _encrypted_request(payload),
            identity=identity,
            encrypt_key=_ENCRYPT_KEY,
            verification_token=_VERIFICATION_TOKEN,
            received_at=NOW,
        )
    assert malformed.value.response.status_code == 400
    with pytest.raises(ProviderRequestError):
        authenticate_and_normalize(
            _encrypted_request(payload, signature="forged"),
            identity=identity,
            encrypt_key=_ENCRYPT_KEY,
            verification_token=_VERIFICATION_TOKEN,
            received_at=NOW,
        )


async def test_retry_uses_same_provider_key_and_has_bounded_budget(task):

    original = task.service
    attempts = []

    def unavailable(request):
        if "auth/v3" in request.url.path:
            return httpx2.Response(200, json={"code": 0, "tenant_access_token": "fixture-token", "expire": 7200})
        attempts.append(json.loads(request.content))
        return httpx2.Response(503)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(unavailable)) as http:
        original.delivery.http = http
        for _ in range(8):
            await due(task)
            assert (await original.scan()).failed == 1
        assert len({body["uuid"] for body in attempts}) == 1
        assert (await original.scan()).examined == 0
    async with short_session(task.sessions) as db:
        row = await db.get(ProgressRecord, task.receipt.run_id)
        assert row.done and row.attempts == 8 and row.last_error == "delivery_failed"
        assert (await db.get(RunRecord, task.receipt.run_id)).status == "accepted"


async def test_http_action_returns_toast_without_creating_input(task):
    from a13n_service.connectivity.ingress.admission import IngressEventService
    from a13n_service.connectivity.ingress.admission_models import IngressAdmissionRecord
    from a13n_service.connectivity.providers.registry import built_in_ingress_adapter_registry

    await task.service.scan()
    async with short_session(task.sessions) as db:
        row = await db.get(ProgressRecord, task.receipt.run_id)
        token = row.action_token
    ingress = IngressEventService(
        task.sessions,
        built_in_ingress_adapter_registry(),
        task.service.delivery.protector,
        request_max_bytes=1024 * 1024,
        workspace_pending_max_count=100,
        workspace_pending_max_bytes=1024 * 1024,
        account_pending_max_count=100,
        account_pending_max_bytes=1024 * 1024,
        batch_max_bytes=1024 * 1024,
        dedup_horizon_seconds=3600,
        clock=lambda: NOW,
        actions=ProgressActions(),
    )
    payload = _payload()
    payload["header"]["event_type"] = "card.action.trigger"
    payload["event"] = {
        "host": "im_message",
        "operator": {"open_id": "ou_owner"},
        "context": {"open_chat_id": "oc_chat", "open_message_id": "om_card"},
        "action": {
            "value": {"kind": "a13n.task_control.v1", "action": "stop", "run_id": task.receipt.run_id, "token": token}
        },
    }
    result = await ingress.receive(account_id=ACCOUNT, request=_encrypted_request(payload))
    assert result.status_code == 200 and json.loads(result.body)["toast"]["type"] == "info"
    async with short_session(task.sessions) as db:
        assert (await db.get(ProgressRecord, task.receipt.run_id)).stop_requested
        assert await db.scalar(select(func.count()).select_from(IngressAdmissionRecord)) == 0


async def test_revoked_authority_rejects_click_immediately(task):
    await task.service.scan()
    async with transaction(task.sessions) as db:
        await db.delete(await db.get(RoleBindingRecord, "rb_progress"))
    assert (await action(task))["toast"]["type"] == "error"


@pytest.mark.parametrize("state", ["running", "waiting", "completed"])
async def test_real_run_lifecycle_projects_and_can_stop_active_work(task, state):
    from a13n_service.interactions.scheduling import AttemptScheduler, ClaimedAttempt

    from tests.gateway.test_commands import _complete_run, _wait_run
    from tests.interactions.test_attempt_execution import _worker
    from tests.lifecycle_support import test_lifecycle_writer

    await task.service.scan()
    if state == "running":
        claim = await AttemptScheduler(
            task.sessions, clock=utc_now, token_factory=lambda: "fixture-lease-token", lifecycle=test_lifecycle_writer()
        ).claim(task.receipt.run_id, _worker())
        assert isinstance(claim, ClaimedAttempt)
    elif state == "waiting":
        await _wait_run(task.sessions, task.objects, run_id=task.receipt.run_id)
    else:
        await _complete_run(task.sessions, task.objects, run_id=task.receipt.run_id)
    await due(task)
    assert (await task.service.scan()).failed == 0
    async with short_session(task.sessions) as db:
        assert (await db.get(ProgressRecord, task.receipt.run_id)).rendered_status == state
        assert (await db.get(ProgressRecord, task.receipt.run_id)).done == (state in {"waiting", "completed"})
    if state == "waiting":
        assert (await action(task))["toast"]["content"] == "This task needs input. Open details to continue."
    if state == "running":
        await action(task)
        claim = await task.service._claim()
        assert claim is not None
        await task.service._process(*claim)
        async with short_session(task.sessions) as db:
            assert (await db.get(RunRecord, task.receipt.run_id)).status == "cancelled"


@pytest.mark.parametrize("mode", ["receive_only", "quiet_chat", "direct", "main"])
async def test_card_admission_and_placement_respect_source_policy(task, mode):
    async with transaction(task.sessions) as db:
        await db.delete(await db.get(ProgressRecord, task.receipt.run_id))
        config = task.batch.configuration
        if mode == "receive_only":
            config.native_actions = ()
        if mode == "quiet_chat":
            config.provider_policy = {"interaction_mode": "chat"}
            config.provider_context["chat_type"] = "group"
        if mode == "direct":
            config.provider_context["chat_type"] = "p2p"
        if mode == "main":
            config.provider_policy = {"reply_mode": "main"}
        await accept_progress(db, task.batch, task.receipt, utc_now())
    if mode in {"receive_only", "quiet_chat"}:
        assert (await task.service.scan()).examined == 0
        assert not task.calls
    else:
        assert (await task.service.scan()).failed == 0
        request = next(r for r in task.calls if "/im/" in r.url.path)
        assert request.url.path == "/open-apis/im/v1/messages"
        assert request.url.params["receive_id_type"] == "chat_id"
        assert json.loads(request.content)["receive_id"] == "oc_chat"


async def test_other_installation_cannot_control_or_override_owner_response(task):
    await task.service.scan()
    async with transaction(task.sessions) as db:
        row = await db.get(ProgressRecord, task.receipt.run_id)
        decision = ProviderActionDecision(
            action="stop",
            reference=row.run_id,
            token=row.action_token,
            actor_id="ou_owner",
            conversation_id="oc_chat",
            message_id="om_card",
        )
        assert await ProgressActions().handle(db, SimpleNamespace(id="acct_other"), decision) == {}
        assert not row.stop_requested


@pytest.fixture
async def replying(task):
    from a13n_service.bots.connectivity.replies import ReplyObservations
    from a13n_service.bots.progress.replies import CardReplies
    from a13n_service.connectivity.ingress.admission_models import AgentThreadBindingRecord
    from a13n_service.connectivity.native_context import InboundRunContext
    from a13n_service.connectivity.providers.definition import InboundActionContext
    from a13n_service.connectivity.providers.registry import require_native_provider
    from a13n_service.interactions.scheduling import AttemptScheduler, ClaimedAttempt

    from tests.interactions.test_attempt_execution import _authority, _worker
    from tests.lifecycle_support import test_lifecycle_writer

    provider = task.provider
    slack = provider == "slack"
    from .test_slack import _config as slack_config

    context = InboundRunContext(
        account_id=ACCOUNT,
        binding_id="bind_progress",
        provider_key=provider,
        execution_principal_ref=PrincipalRef(principal_type="service_account", principal_id=EXECUTOR),
        provider_context_version=require_native_provider(provider).context_version,
        provider_context=(
            {
                "team_id": "T123",
                "channel_id": "C123",
                "message_ts": "1788422400.000200",
                "root_thread_ts": "1788422400.000100",
                "conversation_kind": "channel",
            }
            if slack
            else {
                "chat_id": "oc_chat",
                "message_id": "om_source",
                "discussion_id": "om_source",
                "chat_type": "group",
            }
        ),
        action_policy={"reply_mode": "thread"},
        allowed_actions=(f"{provider}.reply",),
    )
    async with transaction(task.sessions) as db:
        run = await db.get(RunRecord, task.receipt.run_id)
        run.native_tool_contexts_json = [context.model_dump(mode="json")]
        db.add(
            AgentThreadBindingRecord(
                id=context.binding_id,
                account_id=ACCOUNT,
                organization_id=ORGANIZATION_ID,
                workspace_id=WORKSPACE_ID,
                thread_id=run.thread_id,
                external_ref_kind=f"{provider}.discussion",
                external_ref_id="om_source",
                next_batch_sequence=1,
                next_submission_at=utc_now(),
                created_at=utc_now(),
                updated_at=utc_now(),
            )
        )
        generation = (await db.get(AccountRecord, ACCOUNT)).credential_generation
    claim = await AttemptScheduler(task.sessions, clock=utc_now, lifecycle=test_lifecycle_writer()).claim(
        task.receipt.run_id, _worker()
    )
    assert isinstance(claim, ClaimedAttempt)
    attempt = _authority(claim)
    observer = ReplyObservations(task.sessions, cards=CardReplies(task.service.delivery))(
        action=f"{provider}.reply",
        attempt=attempt,
        context=context,
        workspace_id=WORKSPACE_ID,
        account_version=1,
        credential_generation=generation,
    )
    native = require_native_provider(provider).inbound_actions(
        InboundActionContext(
            provider_context=context.provider_context,
            action_policy=context.action_policy,
            configuration=slack_config() if slack else _config(),
            credentials={"bot_token": "fixture-token"} if slack else {"app_secret": "fixture-secret"},
            http=task.service.delivery.http,
            endpoints=task.service.delivery.endpoints,
        )
    )[f"{provider}.reply"]

    async def reply(text):
        return await native.call_observed(
            {"text": text} if slack else {"content": {"kind": "text", "text": text}}, observer
        )

    task.reply = reply
    task.attempt = attempt
    task.context = context
    task.generation = generation
    return task


def cards(task):
    return [(r, json.loads(json.loads(r.content)["content"])) for r in task.calls if "/im/" in r.url.path]


@pytest.mark.parametrize("initial_progress", [True, False])
async def test_explicit_native_reply_updates_single_card_and_survives_completion(replying, initial_progress):
    from a13n_service.bots.connectivity.models import BotReplyRecord
    from a13n_service.interactions.attempts import AttemptExecutionService
    from a13n_service.interactions.objects import RunPayloadStore, RunStateStore
    from a13n_service.interactions.outcomes import RunOutcomeService
    from a13n_service.interactions.state import CompletedOutcomeCandidate

    from tests.interactions.test_attempt_execution import _completed_state
    from tests.lifecycle_support import test_lifecycle_writer

    task = replying
    if initial_progress:
        await task.service.scan()
    assert await task.reply("**Answer one**") == {"kind": "succeeded"}
    assert await task.reply("Answer two") == {"kind": "succeeded"}
    assert len([r for r, _ in cards(task) if r.method == "POST"]) == 1
    assert all(json.loads(r.content).get("msg_type", "interactive") == "interactive" for r, _ in cards(task))
    assert "Answer one" in json.dumps(cards(task)[-1][1]) and "Answer two" in json.dumps(cards(task)[-1][1])
    async with short_session(task.sessions) as db:
        observations = list(await db.scalars(select(BotReplyRecord)))
        assert len(observations) == 2 and all(r.status == "succeeded" for r in observations)
        assert all(r.receipt_json["message_id"] == "om_card" for r in observations)
    execution = AttemptExecutionService(task.sessions, clock=utc_now, lifecycle=test_lifecycle_writer())
    preparation = await execution.commit_preparation_success(task.attempt)
    await execution.enter_harness(task.attempt, preparation=preparation, harness_run_id="harness-progress")
    states = RunStateStore(task.objects)
    current = await states.read(ORGANIZATION_ID, task.receipt.run_id)
    candidate = _completed_state(
        current.envelope,
        task.attempt.run_attempt_id,
        task.attempt.attempt_number,
        outcome=CompletedOutcomeCandidate(output={"answer": "not an automatic reply"}),
    )
    stored = await execution.publish_checkpoint(task.attempt, states, current, candidate)
    await RunOutcomeService(
        task.sessions, RunPayloadStore(task.objects), clock=utc_now, lifecycle=test_lifecycle_writer()
    ).commit_state_outcome(task.attempt, stored)
    await due(task)
    assert (await task.service.scan()).failed == 0
    rendered = json.dumps(cards(task)[-1][1], ensure_ascii=False)
    assert "Answer one" in rendered and "Answer two" in rendered
    assert "not an automatic reply" not in rendered and "a13n.task_control.v1" not in rendered
    assert cards(task)[-1][1]["header"]["template"] == "green"


@pytest.mark.parametrize("language", ["en_us", "zh_cn"])
@pytest.mark.parametrize(
    "replies", [("I am creating the file.",), ("I am creating the file.", "File delivery failed: no Environment.")]
)
def test_completed_card_does_not_infer_task_success_from_replies(language, replies):
    card = task_card(
        status="completed", run_id="run_test", token="test", details_url=None, language=language, replies=replies
    )
    title = card["header"]["title"]["content"]
    assert title == ("执行完成" if language == "zh_cn" else "Execution completed")
    rendered = json.dumps(card, ensure_ascii=False)
    assert all(reply in rendered for reply in replies)
    assert ("不代表任务目标已达成" if language == "zh_cn" else "does not confirm task success") in rendered


async def test_answer_waits_for_progress_lease_without_losing_content(replying):
    import anyio

    task = replying
    claim = await task.service._claim()
    entered = anyio.Event()

    async def reply():
        entered.set()
        assert await task.reply("racing answer") == {"kind": "succeeded"}

    async with anyio.create_task_group() as group:
        group.start_soon(reply)
        await entered.wait()
        await task.service._process(*claim)
    assert len([r for r, _ in cards(task) if r.method == "POST"]) == 1
    assert "racing answer" in json.dumps(cards(task)[-1][1])


@pytest.mark.parametrize("failure", ["rejected", "unknown"])
async def test_failed_card_reply_has_honest_receipt_and_never_falls_back_to_text(replying, failure):
    from a13n_harness.http import ProviderHttpError
    from a13n_service.bots.connectivity.models import BotReplyRecord

    task = replying
    await task.service.scan()
    original_http = task.service.delivery.http
    failed_calls = []

    def fail(request):
        failed_calls.append(request)
        if "auth/v3" in request.url.path:
            return httpx2.Response(200, json={"code": 0, "tenant_access_token": "token", "expire": 7200})
        if failure == "unknown":
            raise httpx2.ReadTimeout("lost provider response")
        return httpx2.Response(200, json={"code": 230001})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(fail)) as http:
        task.service.delivery.http = http
        if failure == "rejected":
            with pytest.raises(ProviderHttpError, match="provider_rejected"):
                await task.reply("new answer")
        else:
            assert (await task.reply("new answer"))["kind"] == "outcome_unknown"
    task.service.delivery.http = original_http
    assert [r.method for r in failed_calls if "/im/" in r.url.path] == ["PATCH"]
    async with short_session(task.sessions) as db:
        observation = await db.scalar(select(BotReplyRecord))
        assert observation.status == ("rejected" if failure == "rejected" else "outcome_unknown")
        row = await db.get(ProgressRecord, task.receipt.run_id)
        assert bool(row.replies_json) == (failure == "unknown")
    await due(task)
    assert (await task.service.scan()).failed == 0
    assert ("new answer" in json.dumps(cards(task)[-1][1])) == (failure == "unknown")
    assert len([r for r, _ in cards(task) if r.method == "POST"]) == 1


async def test_oversized_reply_is_rejected_before_persistence_or_network(replying):
    from a13n_harness.http import ProviderHttpError

    with pytest.raises(ProviderHttpError, match="invalid_arguments"):
        await replying.reply("x" * 30_000)
    assert not replying.calls
    async with short_session(replying.sessions) as db:
        row = await db.get(ProgressRecord, replying.receipt.run_id)
        assert not row.replies_json and row.lease_token is None


async def test_quiet_group_only_creates_card_when_agent_explicitly_replies(replying):
    async with transaction(replying.sessions) as db:
        row = await db.get(ProgressRecord, replying.receipt.run_id)
        row.done = True
    assert (await replying.service.scan()).examined == 0
    assert await replying.reply("explicit quiet-group answer") == {"kind": "succeeded"}
    assert len([r for r, _ in cards(replying) if r.method == "POST"]) == 1
    assert cards(replying)[-1][0].method == "PATCH"


async def test_card_reply_rechecks_attempt_after_waiting_for_delivery_lease(replying):
    import anyio
    from a13n_service.interactions.attempts import AttemptAuthorityError

    claim = await replying.service._claim()
    entered = anyio.Event()

    async def reply():
        entered.set()
        with pytest.raises(AttemptAuthorityError):
            await replying.reply("must not be sent")

    async with anyio.create_task_group() as group:
        group.start_soon(reply)
        await entered.wait()
        replying.attempt.lease.invalidate()
        await replying.service._process(*claim)
    assert "must not be sent" not in json.dumps([c for _, c in cards(replying)])


async def test_late_quiet_reply_starts_dedup_window_at_delivery(replying):
    async with transaction(replying.sessions) as db:
        row = await db.get(ProgressRecord, replying.receipt.run_id)
        row.created_at = utc_now() - timedelta(hours=2)
        row.done = True
    assert await replying.reply("late explicit answer") == {"kind": "succeeded"}
    assert "late explicit answer" in json.dumps(cards(replying)[-1][1])


async def test_expired_initial_delivery_never_creates_a_duplicate(task):
    async with transaction(task.sessions) as db:
        row = await db.get(ProgressRecord, task.receipt.run_id)
        row.delivery_started_at = utc_now() - timedelta(minutes=51)
    assert (await task.service.scan()).failed == 1
    assert not task.calls


async def test_deduplicated_initial_create_is_patched_before_answer_confirmation(replying):
    from a13n_service.bots.connectivity.models import BotReplyRecord

    requests = []

    async def provider(request):
        if "auth/v3" in request.url.path:
            return httpx2.Response(200, json={"code": 0, "tenant_access_token": "token", "expire": 7200})
        requests.append(request)
        async with short_session(replying.sessions) as db:
            record = await db.scalar(select(BotReplyRecord))
            assert record.status == "dispatching"
        # The provider deduplicates to a card whose initial progress response was lost.
        return httpx2.Response(200, json={"code": 0, "data": {"message_id": "om_existing"}})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(provider)) as http:
        replying.service.delivery.http = http
        assert await replying.reply("answer after lost create") == {"kind": "succeeded"}
    assert [r.method for r in requests] == ["POST", "PATCH"]
    assert requests[-1].url.path.endswith("/om_existing")
    assert "answer after lost create" in json.loads(requests[-1].content)["content"]
    async with short_session(replying.sessions) as db:
        record = await db.scalar(select(BotReplyRecord))
        assert record.status == "succeeded" and record.receipt_json["message_id"] == "om_existing"


async def test_multiple_concurrent_replies_append_instead_of_overwriting(replying):
    import anyio

    async with anyio.create_task_group() as group:
        group.start_soon(replying.reply, "first concurrent reply")
        group.start_soon(replying.reply, "second concurrent reply")
    rendered = json.dumps(cards(replying)[-1][1])
    assert "first concurrent reply" in rendered and "second concurrent reply" in rendered
    assert len([r for r, _ in cards(replying) if r.method == "POST"]) == 1


async def test_conflicting_placement_is_rejected_without_sending(replying):
    from a13n_harness.http import ProviderHttpError
    from a13n_service.bots.progress.replies import CardReplies
    from a13n_service.connectivity.providers.lark.actions import LarkAutoReplyArguments, LarkTextContent

    with pytest.raises(ProviderHttpError, match="invalid_arguments"):
        await CardReplies(replying.service.delivery).reply(
            attempt=replying.attempt,
            context=replying.context,
            arguments=LarkAutoReplyArguments(content=LarkTextContent(text="wrong placement"), placement="main"),
            account_version=1,
            credential_generation=replying.generation,
        )
    assert not replying.calls


async def test_revoked_permissions_cannot_queue_a_new_answer(replying):
    from a13n_service.iam import AuthorizationError

    async with transaction(replying.sessions) as db:
        await db.delete(await db.get(RoleBindingRecord, "rb_progress"))
    with pytest.raises(AuthorizationError):
        await replying.reply("must not be queued")
    assert not replying.calls
    async with short_session(replying.sessions) as db:
        row = await db.get(ProgressRecord, replying.receipt.run_id)
        assert not row.replies_json and row.lease_token is None


async def test_post_reply_preserves_title_and_paragraphs(replying):
    from a13n_service.bots.progress.replies import CardReplies
    from a13n_service.connectivity.providers.lark.actions import LarkForcedReplyArguments, LarkPostContent

    result = await CardReplies(replying.service.delivery).reply(
        attempt=replying.attempt,
        context=replying.context,
        arguments=LarkForcedReplyArguments(
            content=LarkPostContent(title="Report", paragraphs=("First paragraph", "Second paragraph"))
        ),
        account_version=1,
        credential_generation=replying.generation,
    )
    assert result.kind == "succeeded"
    content = cards(replying)[-1][1]["elements"][0]["content"]
    assert content == "Report\n\nFirst paragraph\n\nSecond paragraph"


@pytest.mark.parametrize("task", ["slack", "lark"], indirect=True)
async def test_pending_stop_takes_priority_over_explicit_replies(replying):
    from a13n_service.bots.progress.replies import CardReplies
    from a13n_service.connectivity.providers.lark.actions import LarkAutoReplyArguments
    from a13n_service.connectivity.providers.slack.client import SlackAutoReplyArguments

    task = replying
    await task.service.scan()
    await due(task)
    run_id, lease = await task.service._claim()
    # A provider callback arrives while an explicit answer holds the shared lease.
    changes = (
        {"actor_id": "U123", "conversation_id": "C123", "message_id": "1788422401.000100"}
        if task.provider == "slack"
        else {}
    )
    await action(task, **changes)
    await task.service.delivery.publish(run_id, lease)
    async with short_session(task.sessions) as db:
        row = await db.get(ProgressRecord, run_id)
        assert assume_utc(row.available_at) <= utc_now()
        assert row.rendered_status == "stopping"
    arguments = (
        SlackAutoReplyArguments(text="Must wait for stop")
        if task.provider == "slack"
        else LarkAutoReplyArguments(content={"kind": "text", "text": "Must wait for stop"})
    )
    replies = CardReplies(task.service.delivery)
    for _ in range(3):
        assert await replies._claim(task.attempt, task.context, arguments, 1, task.generation) is None
    assert (await task.service.scan()).failed == 0
    async with short_session(task.sessions) as db:
        assert (await db.get(RunRecord, run_id)).status == "cancelled"
        row = await db.get(ProgressRecord, run_id)
        assert row.done and row.rendered_status == "cancelled"
        assert not row.replies_json


@pytest.mark.parametrize("task", ["lark", "slack"], indirect=True)
async def test_repeated_identical_reply_reuses_card_without_duplicate_content(replying):
    task = replying
    assert await task.reply("FIX-920-RECOVERED") == {"kind": "succeeded"}
    call_count = len(task.calls)
    assert await task.reply("FIX-920-RECOVERED") == {"kind": "succeeded"}
    assert len(task.calls) == call_count
    async with short_session(task.sessions) as db:
        row = await db.get(ProgressRecord, task.receipt.run_id)
        assert row.replies_json == ["FIX-920-RECOVERED"]
        assert row.rendered_reply_count == 1
    assert await task.reply("A different update") == {"kind": "succeeded"}
    async with short_session(task.sessions) as db:
        row = await db.get(ProgressRecord, task.receipt.run_id)
        assert row.replies_json == ["FIX-920-RECOVERED", "A different update"]
