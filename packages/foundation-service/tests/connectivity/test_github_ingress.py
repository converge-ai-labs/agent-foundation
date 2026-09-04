from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from a13n_service.connectivity.accounts.domain import CreateAccountRequest
from a13n_service.connectivity.accounts.service import AccountService
from a13n_service.connectivity.ingress.admission import IngressEventService
from a13n_service.connectivity.ingress.admission_models import IngressAdmissionRecord
from a13n_service.connectivity.ingress.domain import CreateIngressRequest
from a13n_service.connectivity.ingress.provider import (
    ProviderCompleteDecision,
    ProviderEligibleEventRouting,
    ProviderEventDecision,
    ProviderRequest,
    ProviderRequestError,
)
from a13n_service.connectivity.ingress.raw_objects import IngressRawObjectStore
from a13n_service.connectivity.ingress.service import IngressService
from a13n_service.connectivity.providers.github import GitHubIngressAdapter
from a13n_service.connectivity.providers.registry import built_in_ingress_adapter_registry
from a13n_service.secrets import SecretProtector
from a13n_service.storage.object_store import LocalObjectStore
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .conftest import AGENT_ID, NOW, SERVICE_ACCOUNT_ID, WORKSPACE_ID, actor

_WEBHOOK_SECRET = "It's a Secret to Everybody"


def _config(
    *,
    api_origin: str = "https://api.github.com",
    web_origin: str = "https://github.com",
) -> dict[str, object]:
    return {
        "api_origin": api_origin,
        "web_origin": web_origin,
        "app_id": 123,
        "installation_id": 456,
        "installation_account_id": 789,
        "bot_account_id": 999,
    }


def _payload(
    *,
    event_name: str = "issue_comment",
    action: str = "created",
    sender_id: int = 111,
) -> dict[str, object]:
    issue = {
        "number": 7,
        "title": "Issue title",
        "body": "Issue body",
        "state": "open",
        "html_url": "https://github.com/acme/repo/issues/7",
        "labels": [{"name": "bug"}],
        "updated_at": "2026-09-03T08:00:00Z",
    }
    pull_request = {
        "number": 8,
        "title": "PR title",
        "body": "PR body",
        "state": "open",
        "html_url": "https://github.com/acme/repo/pull/8",
        "labels": [{"name": "ready"}],
        "base": {"ref": "main"},
        "updated_at": "2026-09-03T08:00:00Z",
    }
    payload: dict[str, object] = {
        "action": action,
        "installation": {"id": 456},
        "repository": {
            "id": 42,
            "name": "repo",
            "owner": {"id": 789, "login": "acme"},
        },
        "sender": {"id": sender_id, "login": "human", "type": "User"},
    }
    if event_name == "issues":
        payload["issue"] = issue
        payload["label"] = {"name": "bug"}
    elif event_name == "issue_comment":
        payload["issue"] = issue
        payload["comment"] = {
            "id": 1001,
            "node_id": "IC_1",
            "body": "Comment 世界",
            "html_url": "https://github.com/acme/repo/issues/7#issuecomment-1001",
            "created_at": "2026-09-03T08:00:00Z",
        }
    elif event_name == "pull_request":
        payload["pull_request"] = pull_request
        payload["label"] = {"name": "ready"}
    elif event_name == "pull_request_review":
        payload["pull_request"] = pull_request
        payload["review"] = {
            "id": 1002,
            "node_id": "PRR_1",
            "body": "Review body",
            "submitted_at": "2026-09-03T08:00:00Z",
        }
    else:
        payload["pull_request"] = pull_request
        payload["comment"] = {
            "id": 1003,
            "node_id": "PRRC_1",
            "body": "Review comment",
            "created_at": "2026-09-03T08:00:00Z",
        }
    return payload


def _request(
    payload: dict[str, object],
    *,
    event_name: str = "issue_comment",
    delivery_id: str = "delivery-1",
    signature: str | None = None,
) -> ProviderRequest:
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    expected = "sha256=" + hmac.new(_WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return ProviderRequest(
        headers={
            "x-github-delivery": delivery_id,
            "x-github-event": event_name,
            "x-hub-signature-256": signature or expected,
        },
        body=body,
        content_type="application/json",
    )


def _credentials(private_key: str) -> dict[str, str]:
    return {"webhook_secret": _WEBHOOK_SECRET, "app_private_key_pem": private_key}


@pytest.mark.anyio
async def test_github_official_hmac_vector_passes_authentication(
    github_private_key_pem: str,
) -> None:
    request = ProviderRequest(
        headers={
            "x-github-delivery": "delivery-vector",
            "x-github-event": "issues",
            "x-hub-signature-256": ("sha256=757107ea0eb2509fc211221cce984b8a37570b6d7586c22c46f4379c8b043e17"),
        },
        body=b"Hello, World!",
        content_type="application/json",
    )

    with pytest.raises(ProviderRequestError) as rejected:
        await GitHubIngressAdapter().authenticate_and_normalize(
            request,
            ingress_id="ing_test",
            account_config=_config(),
            credentials=_credentials(github_private_key_pem),
            received_at=NOW,
        )

    assert rejected.value.response.status_code == 400
    assert rejected.value.reason_code == "invalid_payload"


@pytest.mark.anyio
async def test_github_hmac_unicode_comment_and_identity_are_normalized(
    github_private_key_pem: str,
) -> None:
    adapter = GitHubIngressAdapter()
    adapter.validate_credentials(_credentials(github_private_key_pem), config_version="github_app_http_v1")

    decision = await adapter.authenticate_and_normalize(
        _request(_payload()),
        ingress_id="ing_test",
        account_config=_config(),
        credentials=_credentials(github_private_key_pem),
        received_at=NOW,
    )

    assert isinstance(decision, ProviderEventDecision)
    assert decision.event.text == "Comment 世界"
    assert decision.event.refs["repository"].id == "456:42"
    assert decision.event.refs["target"].id == "42:issue:7"
    assert decision.event.refs["message"].id == "IC_1"
    assert decision.event.context["event_action"] == "issue_comment.created"
    assert "Comment 世界" not in repr(decision.event)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("event_name", "action", "expected_kind"),
    [
        ("issues", "opened", "issue"),
        ("issue_comment", "created", "issue"),
        ("pull_request", "synchronize", "pull_request"),
        ("pull_request_review", "submitted", "pull_request"),
        ("pull_request_review_comment", "created", "pull_request"),
    ],
)
async def test_github_g1_event_families_use_one_safe_target(
    event_name: str,
    action: str,
    expected_kind: str,
    github_private_key_pem: str,
) -> None:
    decision = await GitHubIngressAdapter().authenticate_and_normalize(
        _request(_payload(event_name=event_name, action=action), event_name=event_name),
        ingress_id="ing_test",
        account_config=_config(),
        credentials=_credentials(github_private_key_pem),
        received_at=NOW,
    )

    assert isinstance(decision, ProviderEventDecision)
    assert decision.event.context["target_kind"] == expected_kind


@pytest.mark.anyio
async def test_github_rejects_wrong_signature_or_installation(github_private_key_pem: str) -> None:
    wrong_installation = _payload()
    wrong_installation["installation"] = {"id": 9999}
    requests = (
        _request(_payload(), signature="sha256=" + "0" * 64),
        _request(wrong_installation),
    )
    for request in requests:
        with pytest.raises(ProviderRequestError) as rejected:
            await GitHubIngressAdapter().authenticate_and_normalize(
                request,
                ingress_id="ing_test",
                account_config=_config(),
                credentials=_credentials(github_private_key_pem),
                received_at=NOW,
            )
        assert rejected.value.response.status_code in {401, 404}


@pytest.mark.anyio
async def test_github_ping_unsupported_and_self_events_create_no_admission(
    github_private_key_pem: str,
) -> None:
    adapter = GitHubIngressAdapter()
    ping = {"hook": {"app_id": 123, "installation_target_id": 789}}
    decisions = (
        await adapter.authenticate_and_normalize(
            _request(ping, event_name="ping"),
            ingress_id="ing_test",
            account_config=_config(),
            credentials=_credentials(github_private_key_pem),
            received_at=NOW,
        ),
        await adapter.authenticate_and_normalize(
            _request(_payload(action="edited")),
            ingress_id="ing_test",
            account_config=_config(),
            credentials=_credentials(github_private_key_pem),
            received_at=NOW,
        ),
        await adapter.authenticate_and_normalize(
            _request(_payload(sender_id=999)),
            ingress_id="ing_test",
            account_config=_config(),
            credentials=_credentials(github_private_key_pem),
            received_at=NOW,
        ),
    )

    assert all(isinstance(decision, ProviderCompleteDecision) for decision in decisions)
    assert all(decision.response.status_code == 200 for decision in decisions)


def test_github_config_requires_official_pair_or_allowlisted_enterprise(
    github_private_key_pem: str,
) -> None:
    adapter = GitHubIngressAdapter(allowed_provider_origins=("https://ghe.example",))

    validated = adapter.validate_config(
        _config(api_origin="https://ghe.example/api/v3", web_origin="https://ghe.example"),
        config_version="github_app_http_v1",
    )
    assert validated["api_origin"] == "https://ghe.example/api/v3"
    with pytest.raises(ValueError):
        adapter.validate_config(
            _config(api_origin="https://api.github.com", web_origin="https://ghe.example"),
            config_version="github_app_http_v1",
        )
    with pytest.raises(ValueError, match="private key"):
        adapter.validate_credentials(
            {"webhook_secret": "secret", "app_private_key_pem": "not-a-key"},
            config_version="github_app_http_v1",
        )
    adapter.validate_credentials(_credentials(github_private_key_pem), config_version="github_app_http_v1")


@pytest.mark.anyio
async def test_github_route_predicates_overlap_and_native_actions(
    github_private_key_pem: str,
) -> None:
    adapter = GitHubIngressAdapter()
    left, policy = adapter.validate_route(
        match={
            "repository_ids": [42],
            "event_actions": ["pull_request.labeled"],
            "labels": ["ready"],
            "base_branches": ["main"],
        },
        provider_policy={},
        account_config=_config(),
        config_version="github_app_http_v1",
    )
    right, _ = adapter.validate_route(
        match={
            "repository_ids": [42],
            "event_actions": ["pull_request.labeled"],
            "labels": ["blocked"],
            "base_branches": ["main"],
        },
        provider_policy={},
        account_config=_config(),
        config_version="github_app_http_v1",
    )
    decision = await adapter.authenticate_and_normalize(
        _request(_payload(event_name="pull_request", action="labeled"), event_name="pull_request"),
        ingress_id="ing_test",
        account_config=_config(),
        credentials=_credentials(github_private_key_pem),
        received_at=NOW,
    )
    assert isinstance(decision, ProviderEventDecision)

    assert adapter.prove_non_overlap(left, right) is True
    assert adapter.route_matches(decision.event, left, config_version="github_app_http_v1") is True
    classified = adapter.classify(decision.event, policy, _config(), config_version="github_app_http_v1")
    assert isinstance(classified, ProviderEligibleEventRouting)
    assert classified.native_actions[-1] == "github.list_pr_files"


@pytest.mark.anyio
async def test_github_real_protocol_fixture_is_durable_and_duplicate_acknowledges_200(
    connectivity_sessions: async_sessionmaker[AsyncSession],
    credential_protector: SecretProtector,
    connectivity_objects: LocalObjectStore,
    github_private_key_pem: str,
) -> None:
    registry = built_in_ingress_adapter_registry()
    account = await AccountService(
        connectivity_sessions, registry, credential_protector, clock=lambda: NOW
    ).create_account(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="github-account",
        request=CreateAccountRequest(
            name="github",
            provider_key="github",
            provider_config_version="github_app_http_v1",
            provider_config=_config(),
            credentials=_credentials(github_private_key_pem),
        ),
    )
    ingress_service = IngressService(connectivity_sessions, clock=lambda: NOW)
    ingress = await ingress_service.create_ingress(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="github-ingress",
        request=CreateIngressRequest(
            name="GitHub",
            account_id=account.id,
            provider_config={"events_transport": "http"},
            execution_service_account_id=SERVICE_ACCOUNT_ID,
            agents=(AGENT_ID,),
            default_agent_id=AGENT_ID,
        ),
    )
    event_service = IngressEventService(
        connectivity_sessions,
        registry,
        credential_protector,
        IngressRawObjectStore(connectivity_objects),
        request_max_bytes=8 * 1024 * 1024,
        raw_retention_seconds=0,
        workspace_pending_max_count=100,
        workspace_pending_max_bytes=8 * 1024 * 1024,
        ingress_pending_max_count=100,
        ingress_pending_max_bytes=8 * 1024 * 1024,
        batch_max_bytes=1024 * 1024,
        dedup_horizon_seconds=7 * 24 * 60 * 60,
        clock=lambda: NOW,
    )
    request = _request(_payload())

    first = await event_service.receive(ingress_id=ingress.id, request=request)
    duplicate = await event_service.receive(ingress_id=ingress.id, request=request)

    assert first.status_code == 202
    assert duplicate.status_code == 200
    async with connectivity_sessions() as session:
        count = await session.scalar(select(func.count()).select_from(IngressAdmissionRecord))
    assert count == 1
