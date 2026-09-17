"""Pilot activation verifies live membership and fences the final canonical account update."""

import httpx2
import pytest
from a13n_service.bots.connectivity.domain import ActivateBotRequest
from a13n_service.bots.connectivity.service import BotService
from a13n_service.connectivity.accounts.domain import UpdateAccountRequest
from a13n_service.connectivity.accounts.target_service import AccountTargetService
from a13n_service.connectivity.accounts.targets import ReplaceTargetRequest, TargetConfig
from a13n_service.connectivity.composition import AdapterDefinition, AdapterRegistry
from a13n_service.connectivity.errors import NativeError
from a13n_service.connectivity.providers.slack.adapter import SlackIngressAdapter
from a13n_service.endpoint_policy import EndpointPolicy
from a13n_service.iam.domain import AuthorizationError
from a13n_service.iam.models import ServiceAccountRecord
from a13n_service.storage import transaction

from .conftest import AGENT_ID, SERVICE_ACCOUNT_ID, actor
from .test_bot_checks import bot_account as bot_account
from .test_bot_checks import respond

pytestmark = pytest.mark.anyio


@pytest.fixture
async def pilot(bot_account, connectivity_sessions):
    accounts, original = bot_account
    account = await accounts.update_account(
        actor=actor(),
        account_id=original.id,
        request=UpdateAccountRequest(expected_version=original.version, reception_scope="configured_targets"),
    )
    targets = AccountTargetService(
        connectivity_sessions,
        AdapterRegistry(
            (
                AdapterDefinition(
                    key="slack",
                    config_versions=frozenset({"slack_http_v1"}),
                    factory=SlackIngressAdapter,
                ),
            )
        ),
        batch_max_events=100,
        batch_max_wait_seconds=300,
    )
    target = await targets.create(
        actor=actor(),
        account_id=account.id,
        idempotency_key="pilot-target",
        request=TargetConfig(target_kind="conversation", external_target_id="C1"),
    )
    request = ActivateBotRequest(
        expected_version=account.version,
        target_id=target.id,
        target_version=target.version,
        conversation_id="C1",
        agent_id=AGENT_ID,
        execution_service_account_id=SERVICE_ACCOUNT_ID,
        policy={"interaction_mode": "discussion", "reply_mode": "thread"},
    )
    return accounts, targets, account, target, request


async def test_activation_checks_live_membership_and_canonical_execution_identity(
    pilot, connectivity_sessions, credential_protector
):
    accounts, _, account, _, request = pilot
    paths = []

    def handler(req):
        paths.append(req.url.path)
        return respond(req)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http:
        service = BotService(connectivity_sessions, http, EndpointPolicy(), credential_protector, accounts=accounts)
        result = await service.activate(actor=actor(), account_id=account.id, request=request)
        assert result.receive_enabled
        assert result.reception_scope == "configured_targets"
        assert result.default_agent_id == AGENT_ID
        assert result.execution_service_account_id == SERVICE_ACCOUNT_ID
        assert result.provider_policy == {"interaction_mode": "discussion", "reply_mode": "thread"}
        assert result.version == account.version + 1
        assert paths == ["/api/auth.test", "/api/bots.info", "/api/conversations.info"]
        # A repeated command with a stale version cannot silently apply new settings or probe again.
        with pytest.raises(NativeError) as failure:
            await service.activate(actor=actor(), account_id=account.id, request=request)
        assert failure.value.code == "version_conflict"
        assert len(paths) == 3


@pytest.mark.parametrize("failure_mode", ["not_member", "inactive", "identity_mismatch", "unavailable"])
async def test_failed_provider_validation_keeps_reception_off(
    pilot, connectivity_sessions, credential_protector, failure_mode
):
    accounts, _, account, _, request = pilot

    def handler(req):
        if failure_mode == "unavailable":
            return httpx2.Response(503)
        response = respond(
            req, team="T2" if failure_mode == "identity_mismatch" else "T1", member=failure_mode != "not_member"
        )
        if failure_mode == "inactive" and req.url.path == "/api/conversations.info":
            body = response.json()
            body["channel"]["is_archived"] = True
            return httpx2.Response(200, json=body)
        return response

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http:
        service = BotService(connectivity_sessions, http, EndpointPolicy(), credential_protector, accounts=accounts)
        with pytest.raises(NativeError) as failure:
            await service.activate(actor=actor(), account_id=account.id, request=request)
        if failure_mode == "not_member":
            assert failure.value.code == "bot_not_in_conversation"
            assert "Invite the bot" in failure.value.message
        elif failure_mode == "inactive":
            assert failure.value.code == "bot_conversation_inactive"
    result = await accounts.get_account(actor=actor(), account_id=account.id)
    assert not result.receive_enabled and result.version == account.version


@pytest.mark.parametrize("change", ["target", "extra_target", "account", "principal"])
async def test_changes_during_probe_cannot_enable_a_stale_pilot(
    pilot, connectivity_sessions, credential_protector, change
):
    accounts, targets, account, target, request = pilot

    async def handler(req):
        if req.url.path == "/api/conversations.info":
            if change == "target":
                await targets.replace(
                    actor=actor(),
                    account_id=account.id,
                    target_id=target.id,
                    request=ReplaceTargetRequest(
                        expected_version=target.version,
                        target_kind="conversation",
                        external_target_id="C1",
                        receive_enabled=False,
                    ),
                )
            elif change == "extra_target":
                await targets.create(
                    actor=actor(),
                    account_id=account.id,
                    idempotency_key="second-pilot",
                    request=TargetConfig(target_kind="conversation", external_target_id="C2"),
                )
            elif change == "account":
                await accounts.update_account(
                    actor=actor(),
                    account_id=account.id,
                    request=UpdateAccountRequest(expected_version=account.version, name="Changed during verification"),
                )
            else:
                async with transaction(connectivity_sessions) as session:
                    principal = await session.get(ServiceAccountRecord, SERVICE_ACCOUNT_ID)
                    principal.status = "disabled"
        return respond(req)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http:
        service = BotService(connectivity_sessions, http, EndpointPolicy(), credential_protector, accounts=accounts)
        expected_error = AuthorizationError if change == "principal" else NativeError
        with pytest.raises(expected_error) as failure:
            await service.activate(actor=actor(), account_id=account.id, request=request)
        expected_code = {
            "target": "target_changed",
            "extra_target": "bot_pilot_setup_required",
            "account": "version_conflict",
            "principal": "principal_inactive",
        }[change]
        assert failure.value.code == expected_code
    result = await accounts.get_account(actor=actor(), account_id=account.id)
    assert not result.receive_enabled
