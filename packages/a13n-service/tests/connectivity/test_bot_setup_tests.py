"""Setup observations belong to one authenticated, configured test message."""

import asyncio
import hashlib
import hmac
import json
from datetime import timedelta

import httpx2
import pytest
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from a13n_service.bots.connectivity.models import BotTestRecord
from a13n_service.bots.connectivity.service import BotService
from a13n_service.bots.connectivity.setup_tests import CreateBotTest
from a13n_service.connectivity.accounts.domain import UpdateAccountRequest
from a13n_service.connectivity.accounts.models import AccountRecord
from a13n_service.connectivity.accounts.targets import ReplaceTargetRequest
from a13n_service.connectivity.composition import AdapterDefinition, AdapterRegistry
from a13n_service.connectivity.errors import NativeError
from a13n_service.connectivity.ingress.admission import IngressEventService
from a13n_service.connectivity.ingress.provider import ProviderRequest
from a13n_service.connectivity.providers.slack.adapter import SlackIngressAdapter
from a13n_service.storage import transaction
from sqlalchemy import select

from .conftest import NOW, actor
from .test_bot_activation import pilot as pilot
from .test_bot_checks import bot_account as bot_account
from .test_bot_checks import respond

pytestmark = pytest.mark.anyio


@pytest.fixture
async def setup_test(pilot, connectivity_sessions, credential_protector):
    accounts, targets, original, target, activation = pilot
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http:
        service = BotService(
            connectivity_sessions, http, EndpointPolicy(), credential_protector, accounts=accounts, clock=lambda: NOW
        )
        account = await service.activate(actor=actor(), account_id=original.id, request=activation)
        request = CreateBotTest(expected_version=account.version, target_id=target.id, target_version=target.version)
        yield service, accounts, targets, account, target, request


async def _create(fixture, key="test-start"):
    service, _, _, account, _, request = fixture
    return await service.create_test(actor=actor(), account_id=account.id, request=request, idempotency_key=key)


async def test_start_is_idempotent_with_no_provider_dispatch(setup_test, connectivity_sessions):
    first, second = await asyncio.gather(_create(setup_test), _create(setup_test))
    assert first == second and first.id.startswith("btest_")
    assert first.event_received_at is first.accepted_at is first.reply is None
    assert first.expires_at == NOW + timedelta(minutes=15)
    service, accounts, _, account, _, request = setup_test
    # Retry and GET both project current staleness.
    await accounts.update_account(
        actor=actor(),
        account_id=account.id,
        request=UpdateAccountRequest(expected_version=account.version, receive_enabled=False),
    )
    assert (await _create(setup_test)).stale
    latest = (await service.test(actor=actor(), account_id=account.id, test_id=first.id)).latest
    assert latest.stale
    replay = await service.create_test(
        actor=actor(),
        account_id=account.id,
        request=request.model_copy(update={"target_version": 2}),
        idempotency_key="test-start",
    )
    assert replay.id == first.id and replay.stale
    async with transaction(connectivity_sessions) as session:
        assert len((await session.scalars(select(BotTestRecord))).all()) == 1


async def test_new_test_rejects_changed_target_or_disabled_reception(setup_test):
    service, _accounts, targets, account, target, request = setup_test
    await targets.replace(
        actor=actor(),
        account_id=account.id,
        target_id=target.id,
        request=ReplaceTargetRequest(
            expected_version=target.version,
            target_kind=target.target_kind,
            external_target_id=target.external_target_id,
            receive_enabled=False,
        ),
    )
    with pytest.raises(NativeError) as error:
        await _create(setup_test)
    assert error.value.code == "version_conflict"
    with pytest.raises(NativeError) as error:
        await service.create_test(
            actor=actor(),
            account_id=account.id,
            request=request.model_copy(update={"target_version": target.version + 1}),
            idempotency_key="disabled",
        )
    assert error.value.code == "bot_reception_disabled"


def _signed(marker, *, channel="C1", event_id="Ev1", valid=True, mention=True):
    timestamp = str(int(NOW.timestamp()))
    body = json.dumps(
        {
            "type": "event_callback",
            "team_id": "T1",
            "api_app_id": "A1",
            "event_id": event_id,
            "authorizations": [{"team_id": "T1", "enterprise_id": None}],
            "event": {
                "type": "app_mention" if mention else "message",
                "channel": channel,
                "channel_type": "channel",
                "user": "U2",
                "ts": "123.0",
                "text": ("<@U1> " if mention else "") + "Please echo " + marker,
            },
        }
    ).encode()
    signature = (
        "v0="
        + hmac.new(b"private-signing-secret", b"v0:" + timestamp.encode() + b":" + body, hashlib.sha256).hexdigest()
    )
    return ProviderRequest(
        headers={"x-slack-request-timestamp": timestamp, "x-slack-signature": signature if valid else "v0=" + "0" * 64},
        body=body,
        content_type="application/json",
    )


def _ingress(sessions, protector, *, clock=lambda: NOW):
    from a13n_service.bots.connectivity.setup_tests import SetupObservations

    return IngressEventService(
        sessions,
        AdapterRegistry(
            (AdapterDefinition(key="slack", config_versions=frozenset({"slack_http_v1"}), factory=SlackIngressAdapter),)
        ),
        protector,
        request_max_bytes=1024 * 1024,
        workspace_pending_max_count=100,
        workspace_pending_max_bytes=1024 * 1024,
        account_pending_max_count=100,
        account_pending_max_bytes=1024 * 1024,
        batch_max_bytes=1024 * 1024,
        dedup_horizon_seconds=3600,
        clock=clock,
        observations=SetupObservations(),
    )


@pytest.mark.parametrize(
    "variation", ["match", "wrong_channel", "bad_signature", "another_marker", "ambiguous", "expired", "ignored"]
)
async def test_authenticated_admission_matches_only_exact_live_test(
    setup_test, connectivity_sessions, credential_protector, variation
):
    test = await _create(setup_test)
    marker = test.id
    if variation == "another_marker":
        marker = "btest_" + "0" * 32
    elif variation == "ambiguous":
        marker += " btest_" + "0" * 32
    if variation == "expired":
        async with transaction(connectivity_sessions) as session:
            row = await session.get(BotTestRecord, test.id)
            row.created_at = NOW - timedelta(minutes=16)
            row.expires_at = NOW
    ingress = _ingress(connectivity_sessions, credential_protector)
    request = _signed(
        marker,
        channel="C2" if variation == "wrong_channel" else "C1",
        valid=variation != "bad_signature",
        mention=variation != "ignored",
    )
    await ingress.receive(account_id=test.account_id, request=request)
    # Same delivery cannot replace the first observation.
    await ingress.receive(account_id=test.account_id, request=request)
    service = setup_test[0]
    observation = (await service.test(actor=actor(), account_id=test.account_id, test_id=test.id)).latest
    if variation in {"match", "ignored"}:
        assert observation.event_received_at == NOW
        assert observation.accepted_at is observation.run_id is observation.reply is None
        if variation == "match":
            assert observation.admission_id is not None and observation.rejection_code is None
        else:
            assert observation.admission_id is None and observation.rejection_code is not None
    else:
        assert observation.event_received_at is None


async def test_generation_change_never_attaches_new_delivery_to_old_test(
    setup_test, connectivity_sessions, credential_protector
):
    test = await _create(setup_test)
    async with transaction(connectivity_sessions) as session:
        account = await session.get(AccountRecord, test.account_id)
        account.replace_credential(
            json.dumps({"bot_token": "private-token", "signing_secret": "private-signing-secret"}), credential_protector
        )
    await _ingress(connectivity_sessions, credential_protector).receive(
        account_id=test.account_id, request=_signed(test.id)
    )
    latest = (await setup_test[0].test(actor=actor(), account_id=test.account_id, test_id=test.id)).latest
    assert latest.stale and latest.event_received_at is None


async def test_preparing_requires_admin_even_when_account_metadata_is_readable(setup_test, connectivity_sessions):
    from a13n_service.iam.models import RoleBindingRecord

    test = await _create(setup_test)
    async with transaction(connectivity_sessions) as session:
        (await session.get(RoleBindingRecord, "rb_connectivity_admin")).role_key = "viewer"
    service = setup_test[0]
    assert (await service.test(actor=actor(), account_id=test.account_id, test_id=test.id)).latest.id == test.id
    with pytest.raises(NativeError) as denied:
        await _create(setup_test, "viewer-test")
    assert denied.value.code == "resource_not_found"
