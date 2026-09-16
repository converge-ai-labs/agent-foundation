"""Bot metadata is filtered before pagination and never grants transcript access."""

from datetime import timedelta

import pytest
from a13n_service.connectivity.accounts.domain import CreateAccountRequest
from a13n_service.connectivity.accounts.models import AccountRecord
from a13n_service.connectivity.accounts.target_models import AccountTargetRecord
from a13n_service.connectivity.bots.collection import get_bot_summary, list_bots
from a13n_service.connectivity.bots.domain import BotCheck
from a13n_service.connectivity.bots.models import BotCheckRecord, BotTestRecord
from a13n_service.connectivity.bots.observations import InstallationInfo
from a13n_service.connectivity.errors import NativeError
from a13n_service.iam import AuthenticatedActor, PrincipalRef
from a13n_service.iam.models import RoleBindingRecord
from a13n_service.storage import transaction
from a13n_service.temporal import utc_now

from .conftest import AGENT_ID, ORG_ID, SERVICE_ACCOUNT_ID, USER_ID, WORKSPACE_ID, actor

pytestmark = pytest.mark.anyio


async def seed(accounts, sessions, index, *, platform="slack", verified=True):
    account = await accounts.create_account(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key=f"catalog-{index}",
        request=CreateAccountRequest(
            name=f"Catalog {index}",
            provider_key="fake",
            provider_config_version="fake_http_v1",
            provider_config={"installation_id": f"catalog-{index}"},
            credentials={"token": "private"},
        ),
    )
    now = utc_now()
    async with transaction(sessions) as session:
        record = await session.get(AccountRecord, account.id)
        record.provider_key = platform
        record.provider_config_json = {"team_id": f"T{index}", "tenant_key": f"E{index}"}
        if verified:
            result = BotCheck(
                account_id=account.id,
                credential_generation=1,
                checked_at=now,
                installation=InstallationInfo(
                    app_id=f"A{index}",
                    organization_id=f"T{index}",
                    organization_name=f"Team {index}",
                    bot_id=f"B{index}",
                    bot_name="Helper",
                    enabled=True,
                ),
            )
            session.add(
                BotCheckRecord(
                    account_id=account.id,
                    conversation_id="",
                    credential_generation=1,
                    started_at=now,
                    result_json=result.model_dump(mode="json"),
                )
            )
    return account


async def test_collection_filters_before_pagination_and_fences_cursor(account_service, connectivity_sessions):
    slack = await seed(account_service, connectivity_sessions, 1)
    lark = await seed(account_service, connectivity_sessions, 2, platform="lark")
    await seed(account_service, connectivity_sessions, 3, verified=False)
    first = await list_bots(
        connectivity_sessions, actor=actor(), workspace_id=WORKSPACE_ID, condition="reception_off", limit=1
    )
    second = await list_bots(
        connectivity_sessions,
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        condition="reception_off",
        limit=1,
        cursor=first.next_cursor,
    )
    assert {first.items[0].account.id, second.items[0].account.id} == {slack.id, lark.id}
    assert second.next_cursor is None
    filtered = await list_bots(
        connectivity_sessions, actor=actor(), workspace_id=WORKSPACE_ID, platform="lark", search="team 2"
    )
    assert [item.account.id for item in filtered.items] == [lark.id]
    assert not (await list_bots(connectivity_sessions, actor=actor(), workspace_id=WORKSPACE_ID, search="%")).items
    for changed in (
        {"condition": "needs_verification"},
        {"condition": "reception_off", "search": "Team"},
        {"condition": "reception_off", "platform": "lark"},
    ):
        with pytest.raises(NativeError, match="cursor"):
            await list_bots(
                connectivity_sessions, actor=actor(), workspace_id=WORKSPACE_ID, cursor=first.next_cursor, **changed
            )


async def test_collection_target_count_test_stage_rotation_and_viewer(account_service, connectivity_sessions):
    account = await seed(account_service, connectivity_sessions, 1)
    now = utc_now()
    async with transaction(connectivity_sessions) as session:
        record = await session.get(AccountRecord, account.id)
        record.receive_enabled = True
        record.default_agent_id = AGENT_ID
        record.execution_service_account_id = SERVICE_ACCOUNT_ID
        # A read-only member may inspect setup metadata, not the linked history or credentials.
        binding = await session.get(RoleBindingRecord, "rb_connectivity_admin")
        binding.role_key = "viewer"
        session.add(
            AccountTargetRecord(
                id="tgt_catalog",
                account_id=account.id,
                organization_id=ORG_ID,
                workspace_id=WORKSPACE_ID,
                target_kind="conversation",
                external_target_id="C1",
                receive_enabled=True,
                version=1,
                created_by_type="user",
                created_by_id=USER_ID,
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            BotTestRecord(
                id="btest_catalog",
                organization_id=ORG_ID,
                workspace_id=WORKSPACE_ID,
                account_id=account.id,
                account_version=1,
                credential_generation=1,
                target_id="tgt_catalog",
                target_version=1,
                external_target_id="C1",
                created_at=now,
                expires_at=now + timedelta(minutes=15),
                event_received_at=now,
                run_id="run_private",
                admission_id="adm_private",
            )
        )
    result = await list_bots(connectivity_sessions, actor=actor(), workspace_id=WORKSPACE_ID)
    item = result.items[0]
    assert item.configured_target_count == 1
    assert item.test_stage == "received"
    assert item.external_organization_name == "Team 1"
    assert "run_private" not in result.model_dump_json()
    assert "adm_private" not in result.model_dump_json()
    assert '"token"' not in result.model_dump_json()
    for disabled in ("account", "reception", "target"):
        async with transaction(connectivity_sessions) as session:
            record = await session.get(AccountRecord, account.id)
            target = await session.get(AccountTargetRecord, "tgt_catalog")
            record.status = "disabled" if disabled == "account" else "active"
            record.receive_enabled = disabled != "reception"
            target.receive_enabled = disabled != "target"
        assert (
            await get_bot_summary(connectivity_sessions, actor=actor(), account_id=account.id)
        ).test_stage == "stale"
    async with transaction(connectivity_sessions) as session:
        record = await session.get(AccountRecord, account.id)
        target = await session.get(AccountTargetRecord, "tgt_catalog")
        record.status = "active"
        record.receive_enabled = target.receive_enabled = True
    async with transaction(connectivity_sessions) as session:
        record = await session.get(AccountRecord, account.id)
        record.credential_generation += 1
        record.version += 1
    changed = (await list_bots(connectivity_sessions, actor=actor(), workspace_id=WORKSPACE_ID)).items[0]
    assert changed.setup_condition == "needs_verification"
    assert changed.external_organization_name is None
    assert changed.external_organization_id == "T1"
    assert changed.checked_at is None and changed.test_stage == "stale"


async def test_collection_conceals_deleted_accounts_and_denies_outsider(account_service, connectivity_sessions):
    account = await seed(account_service, connectivity_sessions, 1)
    outsider = AuthenticatedActor(
        principal=PrincipalRef(principal_type="user", principal_id="usr_outsider123456789"),
        auth_method="session",
        credential_id="ses_outsider",
        boundary_workspace_id=WORKSPACE_ID,
    )
    with pytest.raises(NativeError):
        await list_bots(connectivity_sessions, actor=outsider, workspace_id=WORKSPACE_ID)
    await account_service.delete_account(actor=actor(), account_id=account.id, expected_version=1)
    assert not (await list_bots(connectivity_sessions, actor=actor(), workspace_id=WORKSPACE_ID)).items


async def test_collection_separates_test_acceptance_expiry_and_stale_target(account_service, connectivity_sessions):
    account = await seed(account_service, connectivity_sessions, 1)
    now = utc_now()
    async with transaction(connectivity_sessions) as session:
        record = await session.get(AccountRecord, account.id)
        record.receive_enabled = True
        record.default_agent_id = AGENT_ID
        record.execution_service_account_id = SERVICE_ACCOUNT_ID
        session.add(
            AccountTargetRecord(
                id="tgt_catalog",
                account_id=account.id,
                organization_id=ORG_ID,
                workspace_id=WORKSPACE_ID,
                target_kind="conversation",
                external_target_id="C1",
                receive_enabled=True,
                version=1,
                created_by_type="user",
                created_by_id=USER_ID,
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            BotTestRecord(
                id="btest_catalog",
                organization_id=ORG_ID,
                workspace_id=WORKSPACE_ID,
                account_id=account.id,
                account_version=1,
                credential_generation=1,
                target_id="tgt_catalog",
                target_version=1,
                external_target_id="C1",
                created_at=now - timedelta(minutes=20),
                expires_at=now - timedelta(minutes=5),
            )
        )

    async def latest():
        return (await list_bots(connectivity_sessions, actor=actor(), workspace_id=WORKSPACE_ID)).items[0]

    assert (await latest()).test_stage == "expired"
    async with transaction(connectivity_sessions) as session:
        probe = await session.get(BotTestRecord, "btest_catalog")
        probe.event_received_at = now - timedelta(minutes=10)
        probe.accepted_at = now - timedelta(minutes=9)
    # Accepted work does not become expired or imply a reply when the send deadline passes.
    assert (await latest()).test_stage == "accepted"
    async with transaction(connectivity_sessions) as session:
        target = await session.get(AccountTargetRecord, "tgt_catalog")
        await session.delete(target)
    item = await latest()
    assert item.test_stage == "stale" and item.configured_target_count == 0


async def test_collection_failed_verification_and_administrative_disable_are_distinct(
    account_service, connectivity_sessions
):
    account = await seed(account_service, connectivity_sessions, 1)
    async with transaction(connectivity_sessions) as session:
        check = await session.get(BotCheckRecord, (account.id, ""))
        check.result_json = BotCheck(
            account_id=account.id, credential_generation=1, checked_at=utc_now(), error_code="provider_unavailable"
        ).model_dump(mode="json")
    failed = await list_bots(connectivity_sessions, actor=actor(), workspace_id=WORKSPACE_ID, condition="check_failed")
    assert failed.items[0].account.id == account.id
    assert failed.items[0].external_organization_name is None
    async with transaction(connectivity_sessions) as session:
        record = await session.get(AccountRecord, account.id)
        record.status = "disabled"
    assert not (
        await list_bots(connectivity_sessions, actor=actor(), workspace_id=WORKSPACE_ID, condition="check_failed")
    ).items
    disabled = await list_bots(connectivity_sessions, actor=actor(), workspace_id=WORKSPACE_ID, condition="disabled")
    assert disabled.items[0].account.id == account.id


async def test_exact_summary_matches_collection_and_excludes_other_providers(account_service, connectivity_sessions):
    first = await seed(account_service, connectivity_sessions, 1)
    await seed(account_service, connectivity_sessions, 2)
    summary = await get_bot_summary(connectivity_sessions, actor=actor(), account_id=first.id)
    collection = await list_bots(connectivity_sessions, actor=actor(), workspace_id=WORKSPACE_ID)
    assert summary == next(item for item in collection.items if item.account.id == first.id)
    async with transaction(connectivity_sessions) as session:
        record = await session.get(AccountRecord, first.id)
        record.provider_key = "fake"
    with pytest.raises(NativeError) as caught:
        await get_bot_summary(connectivity_sessions, actor=actor(), account_id=first.id)
    assert caught.value.code == "account_not_found"
