"""Provider admission through real canonical Run/Steer persistence."""

from dataclasses import replace
from datetime import timedelta

import pytest
from a13n_service.agents.models import AgentRecord, AgentRevisionRecord
from a13n_service.bots.connectivity.models import BotTestRecord
from a13n_service.connectivity.accounts.models import AccountRecord
from a13n_service.connectivity.accounts.target_models import AccountTargetRecord
from a13n_service.connectivity.ingress.admission_models import AgentThreadBindingRecord, IngressBatchRecord
from a13n_service.connectivity.ingress.submission import IngressInputAcceptor
from a13n_service.iam.models import RoleBindingRecord, ServiceAccountRecord
from a13n_service.interactions.control_models import ThreadInboxRecord
from a13n_service.interactions.models import RunRecord
from a13n_service.interactions.state import InboxReceipt
from a13n_service.storage import transaction
from sqlalchemy import func, select

from tests.gateway.test_commands import _commands, _complete_run, _Freezing, _frozen, _Preparation, _wait_run
from tests.interactions.conftest import AGENT_ID, NOW, ORGANIZATION_ID, WORKSPACE_ID, _seed_interaction_database

from .conftest import USER_ID as ADMIN_ID
from .test_admission import _event_service, _request, reconciler

ACCOUNT = "acct_aaaaaaaaaaaaaaaa"
EXECUTOR = "sa_aaaaaaaaaaaaaaaa"


async def _exercise(
    sessions,
    objects,
    protector,
    *,
    waiting=False,
    stale_claim=False,
    before_accept=None,
    setup_tests=False,
    native_slack=False,
    terminal_status=None,
    completed_head=False,
):
    await _seed_interaction_database(sessions)
    async with transaction(sessions) as session:
        session.add(
            ServiceAccountRecord(
                id=EXECUTOR,
                organization_id=ORGANIZATION_ID,
                workspace_id=WORKSPACE_ID,
                name="Inbound",
                normalized_name="inbound",
                status="active",
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await session.flush()
        session.add(
            RoleBindingRecord(
                id="rb_submission",
                organization_id=ORGANIZATION_ID,
                workspace_id=WORKSPACE_ID,
                principal_type="service_account",
                principal_id=EXECUTOR,
                resource_type="workspace",
                resource_id=WORKSPACE_ID,
                role_key="runner",
                created_by_user_id=ADMIN_ID,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        account = AccountRecord(
            id=ACCOUNT,
            organization_id=ORGANIZATION_ID,
            workspace_id=WORKSPACE_ID,
            name="Inbound",
            normalized_name="inbound",
            provider_key="fake",
            provider_config_version="fake_http_v1",
            provider_config_json={"installation_id": "installation-1"},
            identity_digest="a" * 64,
            status="active",
            version=1,
            credential_generation=0,
            receive_enabled=True,
            default_agent_id=AGENT_ID,
            execution_service_account_id=EXECUTOR,
            created_by_type="service_account",
            created_by_id=EXECUTOR,
            created_at=NOW,
            updated_at=NOW,
        )
        account.replace_credential('{"token":"secret-value"}', protector)
        session.add(account)
    next_agent = "agt_aaaaaaaaaaaaaaaa"
    next_revision = "agr_aaaaaaaaaaaaaaaa"
    async with transaction(sessions) as session:
        original = await session.get(AgentRecord, AGENT_ID)
        values = {column.key: getattr(original, column.key) for column in AgentRecord.__table__.columns}
        values.update(id=next_agent, name="Next", key="next", default_revision_id=next_revision)
        session.add(AgentRecord(**values))
        await session.flush()
        revision = await session.get(AgentRevisionRecord, original.default_revision_id)
        values = {column.key: getattr(revision, column.key) for column in AgentRevisionRecord.__table__.columns}
        values.update(id=next_revision, agent_id=next_agent)
        session.add(AgentRevisionRecord(**values))
    now = [NOW]
    selected_agents = []

    class Preparation(_Preparation):
        async def prepare(self, **kwargs):
            selected_agents.append(kwargs["agent_id"])
            return await super().prepare(**kwargs)

    preparation = Preparation()
    freezing = _Freezing([_frozen(), replace(_frozen(), agent_id=next_agent, agent_revision_id=next_revision)])
    commands = _commands(sessions, objects, preparation, freezing)

    class SetupContribution:
        async def prepare(self, session, account, external_conversation_id):
            return self

        async def accept(self, session, batch, receipt, now):
            from a13n_service.bots.connectivity.setup_tests import record_test_acceptance
            from a13n_service.interactions.control_domain import SteerReceipt

            await record_test_acceptance(
                session,
                batch_id=batch.batch_id,
                run_id=receipt.run_id,
                steer_id=receipt.steer_id if isinstance(receipt, SteerReceipt) else None,
                now=now,
            )

    acceptor = IngressInputAcceptor(
        sessions, commands, clock=lambda: now[0], contributions={"fake": SetupContribution()} if setup_tests else {}
    )
    delivery = _event_service(sessions, protector, clock=lambda: now[0])
    first_request, expected_status = _request("first"), 202
    if native_slack:
        from a13n_service.connectivity.ingress.admission import IngressEventService
        from a13n_service.connectivity.providers.registry import built_in_ingress_adapter_registry

        from .test_slack import _config, _event_payload, _signed_request

        assert before_accept is not None  # This fixture branch exercises a single native admission.
        async with transaction(sessions) as session:
            account = await session.get(AccountRecord, ACCOUNT)
            account.provider_key = "slack"
            account.provider_config_version = "slack_http_v1"
            account.provider_config_json = _config()
            account.replace_credential('{"bot_token":"fictional-token","signing_secret":"signing-secret"}', protector)
        delivery = IngressEventService(
            sessions,
            built_in_ingress_adapter_registry(),
            protector,
            request_max_bytes=1024 * 1024,
            workspace_pending_max_count=100,
            workspace_pending_max_bytes=1024 * 1024,
            account_pending_max_count=100,
            account_pending_max_bytes=1024 * 1024,
            batch_max_bytes=1024 * 1024,
            dedup_horizon_seconds=3600,
            clock=lambda: now[0],
        )
        first_request = _signed_request(_event_payload(event_id="first"), timestamp=int(now[0].timestamp()))
        expected_status = 200
    worker = reconciler(sessions, acceptor, clock=lambda: now[0])
    assert (await delivery.receive(account_id=ACCOUNT, request=first_request)).status_code == expected_status
    claim = await worker._claim()
    prepared = await worker._prepare(claim)
    if stale_claim:
        async with transaction(sessions) as session:
            stored = await session.get(IngressBatchRecord, prepared.batch_id)
            stored.claim_generation += 1
        lost = await acceptor.accept_ingress_batch(prepared)
        assert lost.kind == "lost_race"
        async with sessions() as session:
            assert await session.scalar(select(func.count()).select_from(RunRecord)) == 0
            assert await session.scalar(select(AgentThreadBindingRecord.thread_id)) is None
        return
    if before_accept is not None:
        await before_accept(prepared, acceptor, delivery)
        return
    if setup_tests:
        await _track_test(sessions, prepared.batch_id, "first")
    outcome = await acceptor.accept_ingress_batch(prepared)
    assert outcome.kind == "accepted", outcome
    async with sessions() as session:
        binding = await session.scalar(
            select(AgentThreadBindingRecord).where(AgentThreadBindingRecord.account_id == ACCOUNT)
        )
        first = await session.scalar(select(IngressBatchRecord).where(IngressBatchRecord.binding_id == binding.id))
        assert first.status == "accepted" and first.result_kind == "run"
        run = await session.get(RunRecord, first.result_id)
        assert binding.thread_id == run.thread_id
        assert run.trigger_type == "inbound"
        assert run.native_tool_contexts_json[0]["account_id"] == ACCOUNT
        assert run.authority_principal_id == EXECUTOR
        run_id = run.id
        if setup_tests:
            probe = await session.get(BotTestRecord, "btest_first")
            assert probe.run_id == run.id and probe.steer_id is None and probe.accepted_at == NOW
        thread_id = run.thread_id
    if terminal_status is not None:
        from a13n_harness import SafeFailure
        from a13n_service.interactions.attempts import AttemptExecutionService
        from a13n_service.interactions.control_domain import InterruptRequest
        from a13n_service.interactions.models import ThreadRecord
        from a13n_service.interactions.scheduling import AttemptScheduler, ClaimedAttempt

        from tests.interactions.test_attempt_execution import _authority, _worker
        from tests.lifecycle_support import test_lifecycle_writer

        parent_id = None
        if completed_head:
            await _complete_run(sessions, objects, run_id=run_id)
            parent_id = run_id
            now[0] += timedelta(seconds=10)
            await delivery.receive(account_id=ACCOUNT, request=_request("next-task"))
            assert await worker.run_once()
            async with sessions() as session:
                run_id = (await session.get(ThreadRecord, thread_id)).current_run_id
        if terminal_status == "failed":
            claim = await AttemptScheduler(sessions, clock=lambda: now[0], lifecycle=test_lifecycle_writer()).claim(
                run_id, _worker()
            )
            assert isinstance(claim, ClaimedAttempt)
            await AttemptExecutionService(sessions, clock=lambda: now[0], lifecycle=test_lifecycle_writer()).fail(
                _authority(claim), SafeFailure(code="test_failure", message="Test task failed."), retryable=False
            )
        else:
            async with sessions() as session:
                actor = (await acceptor._selection(session, prepared)).actor
                run = await session.get(RunRecord, run_id)
                thread = await session.get(ThreadRecord, thread_id)
                request = InterruptRequest(expected_run_version=run.version, expected_thread_version=thread.version)
            await commands.active.interrupt(actor=actor, run_id=run_id, idempotency_key="stop", request=request)
        now[0] += timedelta(seconds=10)
        await delivery.receive(account_id=ACCOUNT, request=_request("after-terminal", text="A new instruction"))
        assert await worker.run_once()
        async with sessions() as session:
            latest = await session.scalar(select(IngressBatchRecord).order_by(IngressBatchRecord.sequence.desc()))
            assert latest.status == "accepted"
            successor = await session.get(RunRecord, latest.result_id)
            assert successor.id != run_id and successor.thread_id == thread_id
            assert successor.parent_run_id == parent_id
            assert successor.input_json["structured_content"]["events"][0]["text"] == "A new instruction"
            assert (await session.get(RunRecord, run_id)).status == terminal_status
        assert await acceptor.accept_ingress_batch(prepared) == outcome
        return
    async with transaction(sessions) as session:
        account = await session.get(AccountRecord, ACCOUNT)
        account.default_agent_id = next_agent
        account.version += 1
    if waiting:
        await _wait_run(sessions, objects, run_id=run_id)
    now[0] += timedelta(milliseconds=100)
    await delivery.receive(account_id=ACCOUNT, request=_request("steer"))
    if setup_tests:
        async with sessions() as session:
            batch_id = await session.scalar(select(IngressBatchRecord.id).where(IngressBatchRecord.sequence == 2))
        await _track_test(sessions, batch_id, "second")
    assert await worker.run_once()
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(RunRecord)) == 1
        entry = await session.scalar(select(ThreadInboxRecord))
        assert entry.target_run_id == (None if waiting else run_id)
        assert entry.source_waiting_run_id == (run_id if waiting else None)
        second = await session.scalar(select(IngressBatchRecord).where(IngressBatchRecord.sequence == 2))
        assert second.status == "accepted" and second.result_id == entry.id
        if setup_tests:
            probe = await session.get(BotTestRecord, "btest_second")
            assert probe.run_id == run_id and probe.steer_id == entry.id and probe.accepted_at == now[0]
    assert preparation.calls == 1
    assert freezing.calls == 1
    assert selected_agents == [AGENT_ID]
    if waiting:
        return
    await _complete_run(
        sessions,
        objects,
        run_id=run_id,
        consumed_entries=(InboxReceipt(inbox_entry_id=entry.id, kind="steer"),),
    )
    now[0] += timedelta(seconds=10)
    await delivery.receive(account_id=ACCOUNT, request=_request("continue"))
    assert await worker.run_once()
    async with sessions() as session:
        runs = (await session.scalars(select(RunRecord))).all()
        assert len(runs) == 2
        assert {run.thread_id for run in runs} == {thread_id}
        assert preparation.calls == 2
        assert freezing.calls == 2
        assert selected_agents == [AGENT_ID, next_agent]
        successor = next(run for run in runs if run.id != run_id)
        assert successor.agent_id == next_agent
    async with transaction(sessions) as session:
        account = await session.get(AccountRecord, ACCOUNT)
        account.receive_enabled = False
    assert await acceptor.accept_ingress_batch(prepared) == outcome


async def test_inbound_run_steer_and_idle_continue(connectivity_sessions, connectivity_objects, credential_protector):
    await _exercise(connectivity_sessions, connectivity_objects, credential_protector)


@pytest.mark.parametrize("scenario", ["ordinary", "waiting", "stale_claim"])
async def test_postgresql_inbound_atomic_acceptance(
    connectivity_sessions, connectivity_objects, credential_protector, scenario
):
    await _exercise(
        connectivity_sessions,
        connectivity_objects,
        credential_protector,
        waiting=scenario == "waiting",
        stale_claim=scenario == "stale_claim",
    )


@pytest.mark.parametrize("change", ["account_reception", "target_reception", "disabled", "deleted", "revoked"])
async def test_admitted_batch_retains_reception_but_checks_execution(
    connectivity_sessions, connectivity_objects, credential_protector, change
):
    sessions = connectivity_sessions

    async def check_admitted(prepared, acceptor, delivery):
        async with transaction(sessions) as session:
            account = await session.get(AccountRecord, ACCOUNT)
            if change == "account_reception":
                account.receive_enabled = False
            elif change == "target_reception":
                session.add(
                    AccountTargetRecord(
                        id="target_closed",
                        organization_id=ORGANIZATION_ID,
                        workspace_id=WORKSPACE_ID,
                        account_id=ACCOUNT,
                        target_kind=prepared.configuration.target_kind,
                        external_target_id=prepared.configuration.external_target_id,
                        receive_enabled=False,
                        version=1,
                        created_by_type="service_account",
                        created_by_id=EXECUTOR,
                        created_at=NOW,
                        updated_at=NOW,
                    )
                )
            elif change == "disabled":
                account.status = "disabled"
            elif change == "deleted":
                account.deleted_at = NOW
                account.clear_credential()
            else:
                role = await session.get(RoleBindingRecord, "rb_submission")
                await session.delete(role)
            account.version += 1
        outcome = await acceptor.accept_ingress_batch(prepared)
        if change in {"account_reception", "target_reception"}:
            assert outcome.kind == "accepted"
            await delivery.receive(account_id=ACCOUNT, request=_request("after-closure"))
            async with sessions() as session:
                assert await session.scalar(select(func.count()).select_from(IngressBatchRecord)) == 1
                assert await session.scalar(select(func.count()).select_from(RunRecord)) == 1
        else:
            assert outcome.kind == "rejected"
            async with sessions() as session:
                assert await session.scalar(select(func.count()).select_from(RunRecord)) == 0

    await _exercise(sessions, connectivity_objects, credential_protector, before_accept=check_admitted)


async def _track_test(sessions, batch_id, suffix):
    async with transaction(sessions) as session:
        session.add(
            BotTestRecord(
                id=f"btest_{suffix}",
                organization_id=ORGANIZATION_ID,
                workspace_id=WORKSPACE_ID,
                account_id=ACCOUNT,
                account_version=1,
                credential_generation=1,
                target_id="tgt_probe",
                target_version=1,
                external_target_id="support",
                created_at=NOW,
                expires_at=NOW + timedelta(minutes=15),
                event_received_at=NOW,
                batch_id=batch_id,
            )
        )


async def test_setup_probe_acceptance_is_atomic_with_real_run_and_steer(
    connectivity_sessions, connectivity_objects, credential_protector
):
    await _exercise(connectivity_sessions, connectivity_objects, credential_protector, setup_tests=True)


async def test_bot_settings_change_between_selection_and_commit_rolls_back_acceptance(
    connectivity_sessions, connectivity_objects, credential_protector, monkeypatch
):
    from a13n_service.bots.connectivity.ingress import BotIngress
    from a13n_service.bots.memory.behavior import ConversationMemory
    from a13n_service.bots.memory.bindings import RunMemoryBindingRecord
    from a13n_service.bots.memory.settings import AccountSettingsRecord
    from a13n_service.memory.behaviors import MemoryBehaviors, RunMemorySelectionRecord

    from tests.memory.selection_support import ordinary_memory

    sessions = connectivity_sessions

    async def check_admitted(prepared, acceptor, delivery):
        ordinary = ordinary_memory(sessions).default
        bindings = MemoryBehaviors(sessions, default=ordinary, behaviors=(ConversationMemory(ordinary.service, None),))
        acceptance = acceptor._commands.acceptance
        monkeypatch.setattr(acceptance, "_bindings", bindings)
        monkeypatch.setattr(acceptor, "_contributions", {"slack": BotIngress()})
        publish = acceptance._publish_initial

        async def publish_then_change_configuration(run, state):
            await publish(run, state)
            # Commit independently after selection and before canonical acceptance.
            # The memory remains disabled: generation alone must reject the stale selection.
            async with transaction(sessions) as session:
                session.add(
                    AccountSettingsRecord(
                        account_id=ACCOUNT,
                        version=1,
                        provider_id=None,
                        use_memory=False,
                        save_on_request=False,
                        timezone="UTC",
                    )
                )

        monkeypatch.setattr(acceptance, "_publish_initial", publish_then_change_configuration)
        outcome = await acceptor.accept_ingress_batch(prepared)
        assert outcome.kind == "lost_race"
        async with transaction(sessions) as session:
            for model in (RunRecord, RunMemorySelectionRecord, RunMemoryBindingRecord):
                assert await session.scalar(select(func.count()).select_from(model)) == 0
            batch = await session.get(IngressBatchRecord, prepared.batch_id)
            assert batch.status == "pending" and batch.result_id is None
            assert batch.claim_owner == prepared.claim_owner and batch.claim_generation == prepared.claim_generation
            assert await session.scalar(select(AgentThreadBindingRecord.thread_id)) is None
            assert (await session.get(AccountSettingsRecord, ACCOUNT)).version == 1

        monkeypatch.setattr(acceptance, "_publish_initial", publish)
        accepted = await acceptor.accept_ingress_batch(prepared)
        assert accepted.kind == "accepted"
        assert await acceptor.accept_ingress_batch(prepared) == accepted
        async with transaction(sessions) as session:
            for model in (RunRecord, RunMemorySelectionRecord, RunMemoryBindingRecord):
                assert await session.scalar(select(func.count()).select_from(model)) == 1
            retained = await session.get(RunMemoryBindingRecord, accepted.receipt_id)
            assert retained.account_id == ACCOUNT and retained.external_conversation_id == "C123"
            assert not retained.use_memory and not retained.save_on_request
            assert (await session.get(RunMemorySelectionRecord, accepted.receipt_id)).behavior_key == "bot_conversation"
            assert (await session.get(IngressBatchRecord, prepared.batch_id)).result_id == accepted.receipt_id

    await _exercise(
        sessions, connectivity_objects, credential_protector, before_accept=check_admitted, native_slack=True
    )


@pytest.mark.parametrize("terminal_status", ["failed", "cancelled"])
@pytest.mark.parametrize("completed_head", [False, True])
async def test_new_message_after_terminal_task_retains_thread_and_completed_history(
    connectivity_sessions, connectivity_objects, credential_protector, terminal_status, completed_head
):
    await _exercise(
        connectivity_sessions,
        connectivity_objects,
        credential_protector,
        terminal_status=terminal_status,
        completed_head=completed_head,
    )
