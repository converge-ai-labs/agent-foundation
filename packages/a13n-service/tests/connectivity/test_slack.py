from __future__ import annotations

import hashlib
import hmac
import json
from datetime import timedelta

import pytest
from a13n_service.connectivity.accounts.domain import CreateAccountRequest
from a13n_service.connectivity.accounts.service import AccountService
from a13n_service.connectivity.adapters import IngressAdapter
from a13n_service.connectivity.composition import AdapterDefinition, AdapterRegistry
from a13n_service.connectivity.ingress.admission import IngressEventService
from a13n_service.connectivity.ingress.admission_models import IngressAdmissionRecord, IngressBatchRecord
from a13n_service.connectivity.ingress.provider import (
    ExternalRef,
    InboundEvent,
    ProviderCompleteDecision,
    ProviderEventDecision,
    ProviderIrrelevantEventRouting,
    ProviderRequest,
    ProviderRequestError,
    ProviderRequiresBindingRouting,
)
from a13n_service.connectivity.providers.registry import built_in_ingress_adapter_registry
from a13n_service.connectivity.providers.slack import SlackIngressAdapter
from a13n_service.secrets import SecretProtector
from a13n_service.storage.object_store import LocalObjectStore
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .conftest import AGENT_ID, NOW, SERVICE_ACCOUNT_ID, WORKSPACE_ID, actor

_SIGNING_SECRET = "signing-secret"
_BOT_TOKEN = "xoxb-secret"


def _config() -> dict[str, object]:
    return {
        "api_app_id": "A123",
        "team_id": "T123",
        "enterprise_id": None,
        "bot_user_id": "UBOT",
    }


def _event_payload(*, event_id: str = "Ev01", text: str = "<@UBOT> hello 世界", user: str = "U123") -> bytes:
    return json.dumps(
        {
            "type": "event_callback",
            "api_app_id": "A123",
            "team_id": "T123",
            "enterprise_id": None,
            "event_id": event_id,
            "authorizations": [{"team_id": "T123", "enterprise_id": None}],
            "event": {
                "type": "app_mention",
                "user": user,
                "text": text,
                "channel": "C123",
                "channel_type": "channel",
                "ts": "1788422400.000100",
            },
        },
        ensure_ascii=False,
        sort_keys=True,
    ).encode()


def _signed_request(body: bytes, *, timestamp: int | None = None, signature: str | None = None) -> ProviderRequest:
    timestamp_text = str(timestamp if timestamp is not None else int(NOW.timestamp()))
    expected = (
        "v0="
        + hmac.new(
            _SIGNING_SECRET.encode(),
            b"v0:" + timestamp_text.encode() + b":" + body,
            hashlib.sha256,
        ).hexdigest()
    )
    return ProviderRequest(
        headers={
            "x-slack-request-timestamp": timestamp_text,
            "x-slack-signature": signature or expected,
        },
        body=body,
        content_type="application/json",
    )


def _registry() -> AdapterRegistry[IngressAdapter]:
    return AdapterRegistry(
        (
            AdapterDefinition[IngressAdapter](
                key="slack",
                config_versions=frozenset({"slack_http_v1"}),
                factory=SlackIngressAdapter,
            ),
        )
    )


def test_builtin_registry_exposes_slack_http_v1() -> None:
    registry = built_in_ingress_adapter_registry()

    assert registry.keys() == ("github", "lark", "slack")
    assert isinstance(registry.create("slack", config_version="slack_http_v1"), SlackIngressAdapter)


@pytest.mark.anyio
async def test_slack_hmac_unicode_event_and_correlation_are_normalized() -> None:
    adapter = SlackIngressAdapter()

    decision = await adapter.authenticate_and_normalize(
        _signed_request(_event_payload()),
        account_id="ing_test",
        account_config=_config(),
        credentials={"signing_secret": _SIGNING_SECRET, "bot_token": _BOT_TOKEN},
        received_at=NOW,
    )

    assert isinstance(decision, ProviderEventDecision)
    event = decision.event
    assert event.text == "hello 世界"
    assert event.actor == {"user_id": "U123"}
    assert event.refs["discussion"].id == "T123:C123:1788422400.000100"
    assert event.refs["message"].id == "T123:C123:1788422400.000100"
    assert "hello 世界" not in repr(event)


@pytest.mark.anyio
async def test_slack_rejects_wrong_or_stale_signature() -> None:
    adapter = SlackIngressAdapter()
    for request in (
        _signed_request(_event_payload(), signature="v0=" + "0" * 64),
        _signed_request(_event_payload(), timestamp=int((NOW - timedelta(minutes=6)).timestamp())),
    ):
        with pytest.raises(ProviderRequestError) as rejected:
            await adapter.authenticate_and_normalize(
                request,
                account_id="ing_test",
                account_config=_config(),
                credentials={"signing_secret": _SIGNING_SECRET, "bot_token": _BOT_TOKEN},
                received_at=NOW,
            )
        assert rejected.value.response.status_code == 401


@pytest.mark.anyio
async def test_slack_challenge_and_self_message_create_no_event() -> None:
    adapter = SlackIngressAdapter()
    challenge_body = json.dumps(
        {
            "type": "url_verification",
            "api_app_id": "A123",
            "team_id": "T123",
            "enterprise_id": None,
            "challenge": "challenge-value",
        },
        sort_keys=True,
    ).encode()

    challenge = await adapter.authenticate_and_normalize(
        _signed_request(challenge_body),
        account_id="ing_test",
        account_config=_config(),
        credentials={"signing_secret": _SIGNING_SECRET, "bot_token": _BOT_TOKEN},
        received_at=NOW,
    )
    own_message = await adapter.authenticate_and_normalize(
        _signed_request(_event_payload(user="UBOT")),
        account_id="ing_test",
        account_config=_config(),
        credentials={"signing_secret": _SIGNING_SECRET, "bot_token": _BOT_TOKEN},
        received_at=NOW,
    )

    assert isinstance(challenge, ProviderCompleteDecision)
    assert json.loads(challenge.response.body) == {"challenge": "challenge-value"}
    assert isinstance(own_message, ProviderCompleteDecision)
    assert own_message.response.status_code == 200


@pytest.mark.anyio
async def test_slack_real_protocol_fixture_is_durable_before_ack(
    connectivity_sessions: async_sessionmaker[AsyncSession],
    credential_protector: SecretProtector,
    connectivity_objects: LocalObjectStore,
) -> None:
    registry = _registry()
    account = await AccountService(
        connectivity_sessions, registry, credential_protector, clock=lambda: NOW
    ).create_account(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="slack-account",
        request=CreateAccountRequest(
            receive_enabled=True,
            default_agent_id=AGENT_ID,
            execution_service_account_id=SERVICE_ACCOUNT_ID,
            name="slack",
            provider_key="slack",
            provider_config_version="slack_http_v1",
            provider_config=_config(),
            credentials={"signing_secret": _SIGNING_SECRET, "bot_token": _BOT_TOKEN},
        ),
    )
    event_service = IngressEventService(
        connectivity_sessions,
        registry,
        credential_protector,
        request_max_bytes=8 * 1024 * 1024,
        workspace_pending_max_count=100,
        workspace_pending_max_bytes=1024 * 1024,
        account_pending_max_count=100,
        account_pending_max_bytes=1024 * 1024,
        batch_max_bytes=1024 * 1024,
        dedup_horizon_seconds=7 * 24 * 60 * 60,
        clock=lambda: NOW,
    )

    response = await event_service.receive(account_id=account.id, request=_signed_request(_event_payload()))

    assert response.status_code == 200
    async with connectivity_sessions() as session:
        admission = await session.scalar(select(IngressAdmissionRecord))
        batch = await session.get(IngressBatchRecord, admission.batch_id)
    assert admission is not None
    assert batch.status == "pending"
    assert batch.configuration_json["provider_key"] == "slack"
    assert batch.configuration_json["provider_context"]["channel_id"] == "C123"


def test_slack_route_overlap_and_interaction_classification() -> None:
    adapter = SlackIngressAdapter()
    discussion = adapter.validate_reception_policy(
        {"interaction_mode": "discussion", "reply_mode": "thread"}, config_version="slack_http_v1"
    )
    event = _normalized_event(mentioned=False)

    classified = adapter.classify(event, discussion, _config(), config_version="slack_http_v1")
    assert isinstance(classified, ProviderRequiresBindingRouting)
    assert classified.kind == "requires_binding"
    mention_policy = {"interaction_mode": "mention", "reply_mode": "thread"}
    ignored = adapter.classify(event, mention_policy, _config(), config_version="slack_http_v1")
    assert isinstance(ignored, ProviderIrrelevantEventRouting)


def _normalized_event(*, mentioned: bool) -> InboundEvent:
    return InboundEvent(
        identity_kind="slack.delivery",
        external_event_id="Ev01",
        normalization_version="slack_event_v1",
        type="slack.message",
        received_at=NOW,
        text="hello",
        actor={"user_id": "U1"},
        context={
            "team_id": "T123",
            "channel_id": "C1",
            "conversation_kind": "channel",
            "event_kind": "message",
            "message_ts": "1.0",
            "root_thread_ts": "1.0",
            "mentioned": mentioned,
        },
        refs={
            "conversation": ExternalRef(kind="slack.conversation", id="T123:C1"),
            "discussion": ExternalRef(kind="slack.discussion", id="T123:C1:1.0"),
        },
        data={},
        ordering_key="1.0:Ev01",
    )


@pytest.mark.anyio
@pytest.mark.parametrize("event_kind", ["app_mention", "message"])
async def test_slack_channel_type_is_optional_only_for_app_mentions(event_kind):
    # Slack's documented app_mention payload has no channel_type. Ordinary
    # message deliveries still need it to distinguish channel traffic from DMs.
    payload = json.loads(_event_payload())
    payload["event"]["type"] = event_kind
    del payload["event"]["channel_type"]
    decision = await SlackIngressAdapter().authenticate_and_normalize(
        _signed_request(json.dumps(payload).encode()),
        account_id="ing_test",
        account_config=_config(),
        credentials={"signing_secret": _SIGNING_SECRET, "bot_token": _BOT_TOKEN},
        received_at=NOW,
    )
    if event_kind == "app_mention":
        assert isinstance(decision, ProviderEventDecision)
        assert decision.event.context["conversation_kind"] == "channel"
        assert decision.event.context["channel_id"] == "C123"
        assert decision.event.context["root_thread_ts"] == "1788422400.000100"
    else:
        assert isinstance(decision, ProviderCompleteDecision)
