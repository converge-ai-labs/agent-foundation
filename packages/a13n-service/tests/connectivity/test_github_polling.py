from __future__ import annotations

from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx2
import pytest
from a13n_service.bots.connectivity.collection import list_bots
from a13n_service.bots.connectivity.domain import BotCheckRequest
from a13n_service.bots.connectivity.service import BotService
from a13n_service.connectivity.accounts.domain import CreateAccountRequest
from a13n_service.connectivity.accounts.models import AccountRecord
from a13n_service.connectivity.accounts.service import AccountService
from a13n_service.connectivity.accounts.target_service import AccountTargetService
from a13n_service.connectivity.accounts.targets import TargetConfig
from a13n_service.connectivity.http import ConnectivityHttpError
from a13n_service.connectivity.ingress.admission import IngressEventService
from a13n_service.connectivity.ingress.admission_models import IngressAdmissionRecord
from a13n_service.connectivity.ingress.provider import (
    ProviderEligibleEventRouting,
    ProviderIrrelevantEventRouting,
    ProviderRequest,
)
from a13n_service.connectivity.providers.github.adapter import GitHubIngressAdapter
from a13n_service.connectivity.providers.github.inspection import GitHubInspection
from a13n_service.connectivity.providers.github.notifications import (
    Notification,
    notification_event,
    scan_notifications,
)
from a13n_service.connectivity.providers.github.polling import GitHubNotificationPoller
from a13n_service.connectivity.providers.github.polling_config import POLLING_VERSION
from a13n_service.connectivity.providers.github.polling_models import GitHubPollRecord
from a13n_service.connectivity.providers.github.rest import GitHubREST
from a13n_service.connectivity.providers.registry import built_in_ingress_adapter_registry
from a13n_service.storage import transaction
from sqlalchemy import func, select

from .conftest import AGENT_ID, NOW, SERVICE_ACCOUNT_ID, WORKSPACE_ID, actor
from .test_github_client import _AllowEndpoint, _token_response


def notification(*, updated=NOW, identity="1", repository=42):
    return {
        "id": identity,
        "updated_at": updated.isoformat(),
        "reason": "mention",
        "repository": {"id": repository, "name": "repo", "owner": {"login": "acme"}},
        "subject": {
            "title": "Question",
            "type": "Issue",
            "url": "https://api.github.com/repos/acme/repo/issues/7",
            "latest_comment_url": "https://api.github.com/repos/acme/repo/issues/comments/8",
        },
    }


def source(*, author=10, login="alice", updated=NOW):
    return {
        "body": "Please help",
        "user": {"id": author, "login": login},
        "created_at": NOW.isoformat(),
        "updated_at": updated.isoformat(),
    }


@pytest.mark.anyio
async def test_notification_identity_source_attribution_and_foreign_origin():
    payload = source()
    calls = []

    def respond(request):
        calls.append(str(request.url))
        return httpx2.Response(200, json=payload)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http:
        rest = GitHubREST(http, _AllowEndpoint(), "https://api.github.com")
        item = Notification.model_validate(notification())
        first = await notification_event(rest, "secret", item, user_id=99, now=NOW)
        assert first.actor["login"] == "alice"
        assert first.refs["target"].id == "42:issue:7"
        adapter = GitHubIngressAdapter()
        assert isinstance(
            adapter.classify(first, {"allowed_senders": ["ALICE"]}, {"user_id": 99}, config_version=POLLING_VERSION),
            ProviderEligibleEventRouting,
        )
        assert isinstance(
            adapter.classify(first, {"allowed_senders": ["bob"]}, {"user_id": 99}, config_version=POLLING_VERSION),
            ProviderIrrelevantEventRouting,
        )
        payload = source(updated=NOW + timedelta(seconds=1))
        edited = await notification_event(rest, "secret", item, user_id=99, now=NOW)
        assert edited.actor == {}
        assert isinstance(
            adapter.classify(edited, {"allowed_senders": ["alice"]}, {"user_id": 99}, config_version=POLLING_VERSION),
            ProviderIrrelevantEventRouting,
        )
        payload = source(author=99)
        assert await notification_event(rest, "secret", item, user_id=99, now=NOW) is None
        item.subject.latest_comment_url = "https://foreign.example/steal"
        count = len(calls)
        with pytest.raises(ConnectivityHttpError, match="endpoint_denied"):
            await notification_event(rest, "secret", item, user_id=99, now=NOW)
        assert len(calls) == count


@pytest.mark.anyio
async def test_notification_scan_paginates_and_honors_server_time_and_interval():
    seen = []

    def respond(request):
        seen.append(request)
        if request.url.params["page"] == "1":
            return httpx2.Response(
                200,
                json=[notification(identity=str(i)) for i in range(50)],
                headers={
                    "Date": format_datetime(NOW, usegmt=True),
                    "X-Poll-Interval": "120",
                    "Link": '<https://api.github.com/notifications?page=2>; rel="next"',
                },
            )
        return httpx2.Response(200, json=[notification(identity="50")])

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http:
        items, cursor, delay = await scan_notifications(
            GitHubREST(http, _AllowEndpoint(), "https://api.github.com"),
            "secret",
            NOW - timedelta(seconds=60),
            before=NOW + timedelta(minutes=5),
        )
    assert len(items) == 51
    assert cursor == NOW and delay == 120
    assert seen[0].headers.get("if-modified-since")
    assert seen[0].url.params["all"] == "true"
    assert seen[0].url.params["per_page"] == "50"


@pytest.mark.anyio
async def test_polling_resumes_deduplicates_and_admits_only_configured_repositories(
    connectivity_sessions, credential_protector
):
    registry = built_in_ingress_adapter_registry()
    clock = [NOW]
    accounts = AccountService(connectivity_sessions, registry, credential_protector, clock=lambda: clock[0])
    account = await accounts.create_account(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="poll-account",
        request=CreateAccountRequest(
            name="GitHub polling",
            provider_key="github",
            provider_config_version=POLLING_VERSION,
            provider_config={"user_id": 99, "initial_lookback_seconds": 60},
            credentials={"personal_access_token": "test-pat"},
            receive_enabled=True,
            default_agent_id=AGENT_ID,
            execution_service_account_id=SERVICE_ACCOUNT_ID,
            reception_scope="configured_targets",
        ),
    )
    targets = AccountTargetService(
        connectivity_sessions, registry, batch_max_events=100, batch_max_wait_seconds=300, clock=lambda: clock[0]
    )
    await targets.create(
        actor=actor(),
        account_id=account.id,
        idempotency_key="repository",
        request=TargetConfig(target_kind="repository", external_target_id="42", receive_enabled=True),
    )
    ingress = IngressEventService(
        connectivity_sessions,
        registry,
        credential_protector,
        request_max_bytes=1024 * 1024,
        workspace_pending_max_count=100,
        workspace_pending_max_bytes=1024 * 1024,
        account_pending_max_count=100,
        account_pending_max_bytes=1024 * 1024,
        batch_max_bytes=1024 * 1024,
        dedup_horizon_seconds=604800,
        clock=lambda: clock[0],
    )
    fail = [False]
    snapshots = [notification(), notification(identity="other", repository=43)]
    rotate = [False]

    async def respond(request):
        if request.url.path == "/user":
            return httpx2.Response(200, json={"id": 99, "login": "helper", "type": "User"})
        if request.url.path == "/notifications":
            if rotate[0]:
                async with transaction(connectivity_sessions) as db:
                    row = await db.get(AccountRecord, account.id, with_for_update=True)
                    row.credential_generation += 1
                    row.version += 1
            return httpx2.Response(200, json=snapshots, headers={"date": format_datetime(clock[0], usegmt=True)})
        if fail[0]:
            return httpx2.Response(503, json={})
        return httpx2.Response(200, json=source())

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http:
        poller = GitHubNotificationPoller(
            connectivity_sessions, ingress, http, _AllowEndpoint(), instance_id="svc_one", clock=lambda: clock[0]
        )
        assert await poller.run_once()
        async with connectivity_sessions() as db:
            assert await db.scalar(select(func.count()).select_from(IngressAdmissionRecord)) == 1
            state = await db.get(GitHubPollRecord, account.id)
            assert state.cursor_at == NOW and state.error_code is None
        assert not await poller.run_once()
        # A second process resumes the persisted cursor; changed source bodies do not break snapshot dedupe.
        clock[0] += timedelta(seconds=61)
        restarted = GitHubNotificationPoller(
            connectivity_sessions, ingress, http, _AllowEndpoint(), instance_id="svc_two", clock=lambda: clock[0]
        )
        assert await restarted.run_once()
        async with connectivity_sessions() as db:
            assert await db.scalar(select(func.count()).select_from(IngressAdmissionRecord)) == 1
        # Failed provider reads leave the cursor intact; the next pass safely retries.
        clock[0] += timedelta(seconds=61)
        fail[0] = True
        assert await restarted.run_once()
        async with connectivity_sessions() as db:
            state = await db.get(GitHubPollRecord, account.id)
            assert state.cursor_at == NOW + timedelta(seconds=61)
            assert state.error_code == "provider_unavailable"
        fail[0] = False
        clock[0] += timedelta(seconds=61)
        snapshots[:] = [notification(updated=clock[0])]
        assert await restarted.run_once()
        async with connectivity_sessions() as db:
            assert await db.scalar(select(func.count()).select_from(IngressAdmissionRecord)) == 2
        # Poll accounts never accept a fabricated HTTP notification.
        response = await ingress.receive(
            account_id=account.id, request=ProviderRequest(headers={}, body=b"{}", content_type="application/json")
        )
        assert response.status_code == 404
        bots = await list_bots(connectivity_sessions, actor=actor(), workspace_id=WORKSPACE_ID, platform="github")
        assert bots.items[0].configured_target_count == 1
        service = BotService(connectivity_sessions, http, _AllowEndpoint(), credential_protector)
        setup = await service.setup(actor=actor(), account_id=account.id)
        assert setup.reception_mode == "polling" and setup.event_path is None
        check = await service.check(
            actor=actor(), account_id=account.id, request=BotCheckRequest(expected_version=account.version)
        )
        assert check.installation.bot_id == "99"
        # Credentials changing during external I/O invalidate both admission and cursor advancement.
        previous_cursor = clock[0]
        clock[0] += timedelta(seconds=61)
        snapshots[:] = [notification(updated=clock[0])]
        rotate[0] = True
        assert await restarted.run_once()
        rotate[0] = False
        async with connectivity_sessions() as db:
            state = await db.get(GitHubPollRecord, account.id)
            assert state.cursor_at == previous_cursor
            assert state.error_code == "account_changed"
            assert await db.scalar(select(func.count()).select_from(IngressAdmissionRecord)) == 2
        # One live claim per Account; a superseded scanner cannot retain authority.
        clock[0] += timedelta(seconds=61)
        claim = await poller.claim()
        assert claim and await restarted.claim() is None
        clock[0] += timedelta(seconds=121)
        successor = await restarted.claim()
        assert successor and successor.generation > claim.generation
        with pytest.raises(ConnectivityHttpError, match="poll_lease_lost"):
            async with transaction(connectivity_sessions) as db:
                await poller.fence(claim)(db)


@pytest.mark.anyio
async def test_github_app_probe_checks_installation_identity(github_private_key_pem):
    config = {
        "api_origin": "https://api.github.com",
        "web_origin": "https://github.com",
        "app_id": 1,
        "installation_id": 2,
        "installation_account_id": 3,
        "bot_account_id": 4,
    }
    wrong = [False]

    def respond(request):
        if request.url.path.endswith("/access_tokens"):
            return _token_response(now=datetime.now(UTC))
        if request.url.path == "/app":
            return httpx2.Response(200, json={"id": 1, "slug": "helper"})
        if request.url.path.startswith("/app/installations/"):
            return httpx2.Response(
                200,
                json={
                    "app_id": 1,
                    "account": {"id": 99 if wrong[0] else 3, "login": "acme"},
                    "permissions": {"issues": "write", "pull_requests": "write"},
                },
            )
        return httpx2.Response(200, json={"id": 4, "login": "helper[bot]"})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http:
        probe = GitHubInspection(
            http, _AllowEndpoint(), config, {"app_private_key_pem": github_private_key_pem, "webhook_secret": "secret"}
        )
        assert (await probe.installation()).enabled
        wrong[0] = True
        with pytest.raises(ConnectivityHttpError, match="bot_identity_mismatch"):
            await probe.installation()


@pytest.mark.anyio
async def test_polling_conditional_response_and_rate_limit_backoff(monkeypatch):
    monkeypatch.setattr("a13n_service.connectivity.providers.github.api.time.time", lambda: 1000)
    responses = iter(
        [
            httpx2.Response(304, headers={"date": format_datetime(NOW, usegmt=True), "x-poll-interval": "180"}),
            httpx2.Response(403, json={}, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1300"}),
        ]
    )
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(lambda _: next(responses))) as http:
        rest = GitHubREST(http, _AllowEndpoint(), "https://api.github.com")
        items, cursor, interval = await scan_notifications(rest, "secret", NOW - timedelta(minutes=1), before=NOW)
        assert items == [] and cursor == NOW and interval == 180
        with pytest.raises(ConnectivityHttpError, match="rate_limited") as error:
            await rest.request("/notifications", token="secret")
        assert error.value.retry_after_seconds == 301


@pytest.mark.anyio
@pytest.mark.parametrize(
    "user_id,repository_id,reason", [(100, 42, "bot_identity_mismatch"), (99, 43, "invalid_binding")]
)
async def test_personal_token_never_posts_with_a_changed_user_or_repository(user_id, repository_id, reason):
    from a13n_service.connectivity.providers.github.actions import GitHubAddCommentArguments
    from a13n_service.connectivity.providers.github.client import GitHubNativeClient
    from a13n_service.connectivity.providers.github.rest import GitHubPersonalTokenProvider

    from .test_github_client import _binding

    requests = []

    def respond(request):
        requests.append(request)
        return httpx2.Response(200, json={"id": user_id if request.url.path == "/user" else repository_id})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http:
        endpoint = _AllowEndpoint()
        rest = GitHubREST(http, endpoint, "https://api.github.com")
        native = GitHubNativeClient(
            http,
            endpoint,
            GitHubPersonalTokenProvider(rest, "secret", 99),
            api_origin="https://api.github.com",
            web_origin="https://github.com",
        )
        with pytest.raises(ConnectivityHttpError, match=reason):
            await native.add_comment(_binding(), GitHubAddCommentArguments(body="hello"), request_id="req_test")
        assert all(request.method == "GET" for request in requests)
