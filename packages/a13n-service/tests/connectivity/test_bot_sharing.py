"""Continuing sharing uses durable join boundaries and explicit future enrollment."""

import pytest
from a13n_service.bots.connectivity.domain import BotCheck
from a13n_service.bots.connectivity.models import BotCheckRecord
from a13n_service.bots.memory.domain import ConfigureScope, CreateDocument, ReplaceSharingPolicy, SharingPolicyInput
from a13n_service.bots.memory.models import ScopeRecord
from a13n_service.bots.memory.mutations import create
from a13n_service.bots.memory.sharing import list_policies, save_policy
from a13n_service.connectivity.accounts.models import AccountRecord
from a13n_service.connectivity.accounts.targets import TargetConfig
from a13n_service.connectivity.inspection import ConversationInfo, InstallationInfo
from a13n_service.storage import transaction
from a13n_service.temporal import utc_now
from sqlalchemy import select, update

from .conftest import ACCOUNT_ID, actor
from .test_bot_memory import bot_memory as bot_memory
from .test_bot_memory import sharing_groups

pytestmark = pytest.mark.anyio


async def group(lab, targets, sessions, name, *, audience="private", member=True, active=True):
    await targets.create(
        actor=actor(),
        account_id=ACCOUNT_ID,
        idempotency_key=name,
        request=TargetConfig(target_kind="conversation", external_target_id=name),
    )
    async with transaction(sessions) as session:
        account = await session.get(AccountRecord, ACCOUNT_ID)
        check = BotCheck(
            account_id=ACCOUNT_ID,
            credential_generation=account.credential_generation,
            checked_at=utc_now(),
            conversation_id=name,
            installation=InstallationInfo(
                app_id="A1",
                organization_id="T1",
                organization_name="Acme",
                bot_id="U1",
                bot_name="Helper",
                enabled=True,
            ),
            conversation=ConversationInfo(
                id=name, name=name, audience=audience, is_member=member, is_active=active, external=False
            ),
        )
        session.add(
            BotCheckRecord(
                account_id=ACCOUNT_ID,
                conversation_id=name,
                credential_generation=account.credential_generation,
                started_at=utc_now(),
                result_json=check.model_dump(mode="json"),
            )
        )
    return await lab.service.configure_scope(actor(), ACCOUNT_ID, ConfigureScope(external_conversation_id=name))


def replacement(policy, **changes):
    body = policy.model_dump(include=set(SharingPolicyInput.model_fields))
    body.update(changes)
    return ReplaceSharingPolicy(**body, expected_version=policy.version)


async def test_future_enrollment_waits_for_opt_in_and_never_backfills_known_groups(
    bot_memory, target_service, connectivity_sessions
):
    lab = bot_memory
    recipient = await sharing_groups(lab, target_service, connectivity_sessions)
    policy = await save_policy(
        lab.service, actor(), ACCOUNT_ID, SharingPolicyInput(name="Knowledge", scope_ids=(lab.scope_id, recipient.id))
    )
    known = await group(lab, target_service, connectivity_sessions, "known")
    assert known.id not in (await list_policies(lab.service, actor(), ACCOUNT_ID)).items[0].scope_ids
    policy = await save_policy(
        lab.service, actor(), ACCOUNT_ID, replacement(policy, enroll_future_groups=True), policy.id
    )
    await lab.service.configure_scope(
        actor(), ACCOUNT_ID, ConfigureScope(external_conversation_id="known", expected_version=known.version)
    )
    fresh = await group(lab, target_service, connectivity_sessions, "future")
    current = (await list_policies(lab.service, actor(), ACCOUNT_ID)).items[0]
    assert fresh.id in current.scope_ids and known.id not in current.scope_ids
    assert current.version == policy.version + 1
    assert current.future_since == policy.future_since
    assert next(p for p in current.participants if p.scope_id == fresh.id).joined_at >= policy.created_at


@pytest.mark.parametrize(
    "audience,member,active",
    [("direct", True, True), ("unknown", True, True), ("private", False, True), ("private", True, False)],
)
async def test_ineligible_groups_do_not_auto_enroll(
    bot_memory, target_service, connectivity_sessions, audience, member, active
):
    lab = bot_memory
    recipient = await sharing_groups(lab, target_service, connectivity_sessions)
    policy = await save_policy(
        lab.service,
        actor(),
        ACCOUNT_ID,
        SharingPolicyInput(name="Knowledge", scope_ids=(lab.scope_id, recipient.id), enroll_future_groups=True),
    )
    created = await group(
        lab, target_service, connectivity_sessions, "candidate", audience=audience, member=member, active=active
    )
    current = (await list_policies(lab.service, actor(), ACCOUNT_ID)).items[0]
    assert created.id not in current.scope_ids and current.version == policy.version


async def test_later_join_cutoff_and_manual_removal_survive_group_reconfiguration(
    bot_memory, target_service, connectivity_sessions
):
    lab = bot_memory
    recipient = await sharing_groups(lab, target_service, connectivity_sessions)
    await save_policy(
        lab.service,
        actor(),
        ACCOUNT_ID,
        SharingPolicyInput(name="Knowledge", scope_ids=(lab.scope_id, recipient.id), enroll_future_groups=True),
    )
    earlier = await create(
        lab.service,
        actor(),
        ACCOUNT_ID,
        lab.scope_id,
        CreateDocument(text="Before join", title="Before"),
        "before-join",
    )
    joined = await group(lab, target_service, connectivity_sessions, "future")
    fresh = await create(
        lab.service, actor(), ACCOUNT_ID, lab.scope_id, CreateDocument(text="After join", title="After"), "after-join"
    )
    visible = await lab.service.list(actor(), ACCOUNT_ID, joined.id)
    assert {d.id for d in visible.items} == {fresh.id}
    assert earlier.id not in {d.id for d in visible.items}
    policy = (await list_policies(lab.service, actor(), ACCOUNT_ID)).items[0]
    policy = await save_policy(
        lab.service, actor(), ACCOUNT_ID, replacement(policy, scope_ids=(lab.scope_id, recipient.id)), policy.id
    )
    await lab.service.configure_scope(
        actor(),
        ACCOUNT_ID,
        ConfigureScope(external_conversation_id="future", expected_version=joined.version, enabled=False),
    )
    await lab.service.configure_scope(
        actor(), ACCOUNT_ID, ConfigureScope(external_conversation_id="future", expected_version=joined.version + 1)
    )
    current = (await list_policies(lab.service, actor(), ACCOUNT_ID)).items[0]
    assert joined.id not in current.scope_ids and current.version == policy.version
    assert not (await lab.service.list(actor(), ACCOUNT_ID, joined.id)).items


async def test_disable_remains_possible_when_a_participant_becomes_unknown(
    bot_memory, target_service, connectivity_sessions
):
    lab = bot_memory
    recipient = await sharing_groups(lab, target_service, connectivity_sessions)
    policy = await save_policy(
        lab.service, actor(), ACCOUNT_ID, SharingPolicyInput(name="Knowledge", scope_ids=(lab.scope_id, recipient.id))
    )
    async with transaction(connectivity_sessions) as session:
        await session.execute(update(ScopeRecord).where(ScopeRecord.id == recipient.id).values(audience="unknown"))
    stopped = await save_policy(lab.service, actor(), ACCOUNT_ID, replacement(policy, enabled=False), policy.id)
    assert not stopped.enabled


async def test_reactivation_starts_a_new_save_boundary_and_reports_it(
    bot_memory, target_service, connectivity_sessions
):
    lab = bot_memory
    recipient = await sharing_groups(lab, target_service, connectivity_sessions)
    policy = await save_policy(
        lab.service, actor(), ACCOUNT_ID, SharingPolicyInput(name="Knowledge", scope_ids=(lab.scope_id, recipient.id))
    )
    stopped = await save_policy(lab.service, actor(), ACCOUNT_ID, replacement(policy, enabled=False), policy.id)
    paused = await create(
        lab.service, actor(), ACCOUNT_ID, lab.scope_id, CreateDocument(text="During pause", title="Pause"), "paused"
    )
    resumed = await save_policy(lab.service, actor(), ACCOUNT_ID, replacement(stopped, enabled=True), policy.id)
    assert resumed.future_since > policy.future_since
    assert all(p.joined_at == resumed.future_since for p in resumed.participants)
    assert paused.id not in {d.id for d in (await lab.service.list(actor(), ACCOUNT_ID, recipient.id)).items}
    async with transaction(connectivity_sessions) as session:
        scopes = await session.scalars(select(ScopeRecord).where(ScopeRecord.id.in_(resumed.scope_ids)))
        assert len(list(scopes)) == 2


async def test_rollout_marks_existing_scopes_as_known_without_enrolling_them(
    bot_memory, target_service, connectivity_sessions, service_database
):
    import anyio
    from a13n_service.database.migration import DatabaseMigrator

    lab = bot_memory
    async with transaction(connectivity_sessions) as session:
        prior = await session.get(ScopeRecord, lab.scope_id)
        assert not prior.sharing_initialized
    migrator = DatabaseMigrator(service_database)
    await anyio.to_thread.run_sync(migrator.downgrade, "fa5bed0a37f2")
    await anyio.to_thread.run_sync(migrator.upgrade)
    async with transaction(connectivity_sessions) as session:
        prior = await session.get(ScopeRecord, lab.scope_id)
        assert prior.sharing_initialized
    await target_service.create(
        actor=actor(),
        account_id=ACCOUNT_ID,
        idempotency_key="after-rollout",
        request=TargetConfig(target_kind="conversation", external_target_id="after-rollout"),
    )
    created = await lab.service.configure_scope(
        actor(), ACCOUNT_ID, ConfigureScope(external_conversation_id="after-rollout")
    )
    async with transaction(connectivity_sessions) as session:
        later = await session.get(ScopeRecord, created.id)
        assert not later.sharing_initialized


async def test_verification_alone_does_not_enroll_until_configuration_is_saved(
    bot_memory, target_service, connectivity_sessions
):
    lab = bot_memory
    recipient = await sharing_groups(lab, target_service, connectivity_sessions)
    await save_policy(
        lab.service,
        actor(),
        ACCOUNT_ID,
        SharingPolicyInput(name="Knowledge", scope_ids=(lab.scope_id, recipient.id), enroll_future_groups=True),
    )
    candidate = await group(lab, target_service, connectivity_sessions, "unverified", member=False)
    async with transaction(connectivity_sessions) as session:
        record = await session.get(BotCheckRecord, (ACCOUNT_ID, "unverified"))
        checked = BotCheck.model_validate(record.result_json)
        record.result_json = checked.model_copy(
            update={"conversation": checked.conversation.model_copy(update={"is_member": True})}
        ).model_dump(mode="json")
    assert candidate.id not in (await list_policies(lab.service, actor(), ACCOUNT_ID)).items[0].scope_ids
    await lab.service.configure_scope(
        actor(), ACCOUNT_ID, ConfigureScope(external_conversation_id="unverified", expected_version=candidate.version)
    )
    assert candidate.id in (await list_policies(lab.service, actor(), ACCOUNT_ID)).items[0].scope_ids
