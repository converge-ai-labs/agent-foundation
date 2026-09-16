"""Bot inspection checks use current IAM and the exact credential generation."""

import httpx2
import pytest
from a13n_service.connectivity.accounts.domain import CreateAccountRequest, ReplaceAccountCredentialsRequest
from a13n_service.connectivity.accounts.service import AccountService
from a13n_service.connectivity.bots.domain import BotCheckRequest
from a13n_service.connectivity.bots.service import BotService
from a13n_service.connectivity.composition import AdapterDefinition, AdapterRegistry
from a13n_service.connectivity.errors import NativeError
from a13n_service.connectivity.providers.slack.adapter import SlackIngressAdapter
from a13n_service.endpoint_policy import EndpointPolicy
from a13n_service.iam import AuthenticatedActor, PrincipalRef

from .conftest import WORKSPACE_ID, actor

pytestmark = pytest.mark.anyio


@pytest.fixture
async def bot_account(connectivity_sessions, credential_protector):
    accounts = AccountService(
        connectivity_sessions,
        AdapterRegistry(
            (AdapterDefinition(key="slack", config_versions=frozenset({"slack_http_v1"}), factory=SlackIngressAdapter),)
        ),
        credential_protector,
    )
    account = await accounts.create_account(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="inspection-account",
        request=CreateAccountRequest(
            name="Inspection bot",
            provider_key="slack",
            provider_config_version="slack_http_v1",
            provider_config={"api_app_id": "A1", "team_id": "T1", "bot_user_id": "U1"},
            credentials={"bot_token": "private-token", "signing_secret": "private-signing-secret"},
        ),
    )
    return accounts, account


def respond(request: httpx2.Request, *, team: str = "T1", member: bool = True) -> httpx2.Response:
    if request.url.path == "/api/auth.test":
        return httpx2.Response(200, json={"ok": True, "team_id": team, "team": "Acme", "user_id": "U1", "bot_id": "B1"})
    if request.url.path == "/api/bots.info":
        return httpx2.Response(
            200,
            json={"ok": True, "bot": {"id": "B1", "user_id": "U1", "app_id": "A1", "name": "Helper", "deleted": False}},
        )
    if request.url.path == "/api/conversations.info":
        return httpx2.Response(
            200,
            json={
                "ok": True,
                "channel": {
                    "id": "C1",
                    "name": "Engineering",
                    "is_member": member,
                    "is_archived": False,
                    "is_private": True,
                    "is_im": False,
                    "is_mpim": False,
                    "is_ext_shared": False,
                },
            },
        )
    assert request.url.path == "/api/users.conversations"
    return httpx2.Response(
        200,
        json={"ok": True, "channels": [{"id": "C1", "name": "Engineering"}], "response_metadata": {"next_cursor": ""}},
    )


async def test_bot_check_persists_installation_separately_from_membership(
    bot_account, connectivity_sessions, credential_protector
):
    _, account = bot_account
    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda request: respond(request, member=False))
    ) as http:
        service = BotService(connectivity_sessions, http, EndpointPolicy(), credential_protector)
        result = await service.check(
            actor=actor(), account_id=account.id, request=BotCheckRequest(expected_version=1, conversation_id="C1")
        )
        assert result.error_code is None
        assert result.installation.enabled
        assert result.conversation.is_member is False
        assert (await service.latest(actor=actor(), account_id=account.id, conversation_id="C1")).latest == result
        assert (await service.latest(actor=actor(), account_id=account.id)).latest is None
        assert "private-token" not in result.model_dump_json()
        assert "private-signing-secret" not in result.model_dump_json()


async def test_bot_check_does_not_accept_another_workspace(bot_account, connectivity_sessions, credential_protector):
    _, account = bot_account
    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda request: respond(request, team="OTHER"))
    ) as http:
        service = BotService(connectivity_sessions, http, EndpointPolicy(), credential_protector)
        result = await service.check(actor=actor(), account_id=account.id, request=BotCheckRequest(expected_version=1))
        assert result.error_code == "bot_identity_mismatch"
        assert result.installation is None
        with pytest.raises(NativeError, match="could not be verified"):
            await service.conversations(actor=actor(), account_id=account.id)


async def test_replacing_credentials_clears_visible_verification(
    bot_account, connectivity_sessions, credential_protector
):
    accounts, account = bot_account
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http:
        service = BotService(connectivity_sessions, http, EndpointPolicy(), credential_protector)
        await service.check(actor=actor(), account_id=account.id, request=BotCheckRequest(expected_version=1))
        await accounts.replace_credentials(
            actor=actor(),
            account_id=account.id,
            idempotency_key="replace",
            request=ReplaceAccountCredentialsRequest(
                expected_version=1, credentials={"bot_token": "new-token", "signing_secret": "new-secret"}
            ),
        )
        assert (await service.latest(actor=actor(), account_id=account.id)).latest is None


async def test_rotation_during_bot_check_fences_response_and_persistence(
    bot_account, connectivity_sessions, credential_protector
):
    accounts, account = bot_account

    async def changing(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/api/bots.info":
            await accounts.replace_credentials(
                actor=actor(),
                account_id=account.id,
                idempotency_key="race-replace",
                request=ReplaceAccountCredentialsRequest(
                    expected_version=1, credentials={"bot_token": "new-token", "signing_secret": "new-secret"}
                ),
            )
        return respond(request)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(changing)) as http:
        service = BotService(connectivity_sessions, http, EndpointPolicy(), credential_protector)
        with pytest.raises(NativeError) as failure:
            await service.check(actor=actor(), account_id=account.id, request=BotCheckRequest(expected_version=1))
        assert failure.value.code == "version_conflict"
        assert (await service.latest(actor=actor(), account_id=account.id)).latest is None


async def test_bot_check_denied_before_provider_io(bot_account, connectivity_sessions, credential_protector):
    _, account = bot_account

    def unexpected(_request: httpx2.Request) -> httpx2.Response:
        pytest.fail("unauthorized check must not send credentials")

    outsider = AuthenticatedActor(
        principal=PrincipalRef(principal_type="user", principal_id="usr_outsider123456789"),
        auth_method="session",
        credential_id="ses_outsider",
        boundary_workspace_id=WORKSPACE_ID,
    )
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(unexpected)) as http:
        service = BotService(connectivity_sessions, http, EndpointPolicy(), credential_protector)
        with pytest.raises(NativeError) as failure:
            await service.check(actor=outsider, account_id=account.id, request=BotCheckRequest(expected_version=1))
        assert failure.value.code == "resource_not_found"


async def test_failed_check_replaces_old_success(bot_account, connectivity_sessions, credential_protector):
    _, account = bot_account
    failed = False

    def changing(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(503) if failed else respond(request)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(changing)) as http:
        service = BotService(connectivity_sessions, http, EndpointPolicy(), credential_protector)
        await service.check(actor=actor(), account_id=account.id, request=BotCheckRequest(expected_version=1))
        failed = True
        result = await service.check(actor=actor(), account_id=account.id, request=BotCheckRequest(expected_version=1))
        assert result.error_code == "provider_unavailable"
        assert (await service.latest(actor=actor(), account_id=account.id)).latest == result


async def test_later_started_check_wins_when_older_request_finishes_last(
    bot_account, connectivity_sessions, credential_protector
):
    from datetime import timedelta

    import anyio

    from .conftest import NOW

    _, account = bot_account
    started, release = anyio.Event(), anyio.Event()
    now = [NOW]
    requests = 0

    async def changing(request: httpx2.Request) -> httpx2.Response:
        nonlocal requests
        if request.url.path == "/api/auth.test":
            requests += 1
            if requests == 1:
                started.set()
                await release.wait()
                return httpx2.Response(503)
        return respond(request)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(changing)) as http:
        service = BotService(connectivity_sessions, http, EndpointPolicy(), credential_protector, clock=lambda: now[0])

        async def first() -> None:
            result = await service.check(
                actor=actor(), account_id=account.id, request=BotCheckRequest(expected_version=1)
            )
            assert result.error_code == "provider_unavailable"

        async with anyio.create_task_group() as group:
            group.start_soon(first)
            await started.wait()
            now[0] += timedelta(seconds=1)
            newer = await service.check(
                actor=actor(), account_id=account.id, request=BotCheckRequest(expected_version=1)
            )
            assert newer.installation is not None
            release.set()
        assert (await service.latest(actor=actor(), account_id=account.id)).latest == newer


async def test_cancelled_check_does_not_manufacture_saved_result(
    bot_account, connectivity_sessions, credential_protector
):
    import anyio

    _, account = bot_account

    async def waiting(_request: httpx2.Request) -> httpx2.Response:
        await anyio.sleep_forever()
        raise AssertionError("unreachable")

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(waiting)) as http:
        service = BotService(connectivity_sessions, http, EndpointPolicy(), credential_protector)
        with anyio.move_on_after(0.2) as scope:
            await service.check(actor=actor(), account_id=account.id, request=BotCheckRequest(expected_version=1))
        assert scope.cancel_called
        assert (await service.latest(actor=actor(), account_id=account.id)).latest is None


async def test_target_deletion_during_check_cannot_recreate_its_observation(
    bot_account, connectivity_sessions, credential_protector
):
    from a13n_service.connectivity.accounts.target_service import AccountTargetService
    from a13n_service.connectivity.accounts.targets import TargetConfig

    _, account = bot_account
    targets = AccountTargetService(
        connectivity_sessions,
        AdapterRegistry(
            (AdapterDefinition(key="slack", config_versions=frozenset({"slack_http_v1"}), factory=SlackIngressAdapter),)
        ),
        batch_max_events=100,
        batch_max_wait_seconds=300,
    )
    target = await targets.create(
        actor=actor(),
        account_id=account.id,
        idempotency_key="check-target",
        request=TargetConfig(target_kind="conversation", external_target_id="C1", receive_enabled=False),
    )

    async def changing(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/api/conversations.info":
            await targets.delete(
                actor=actor(), account_id=account.id, target_id=target.id, expected_version=target.version
            )
        return respond(request)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(changing)) as http:
        service = BotService(connectivity_sessions, http, EndpointPolicy(), credential_protector)
        with pytest.raises(NativeError) as failure:
            await service.check(
                actor=actor(), account_id=account.id, request=BotCheckRequest(expected_version=1, conversation_id="C1")
            )
        assert failure.value.code == "target_changed"
        assert (await service.latest(actor=actor(), account_id=account.id, conversation_id="C1")).latest is None


@pytest.mark.parametrize("origin", [None, "https://events.example.test"])
async def test_bot_setup_uses_only_configured_origin_without_provider_io(
    bot_account, connectivity_sessions, credential_protector, origin
):
    _, account = bot_account

    def unexpected(_request):
        pytest.fail("reading setup must not probe the provider or expose credentials")

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(unexpected)) as http:
        service = BotService(connectivity_sessions, http, EndpointPolicy(), credential_protector, public_origin=origin)
        setup = await service.setup(actor=actor(), account_id=account.id)
        assert setup.event_path == f"/connectivity/v1/accounts/{account.id}/events"
        assert setup.event_url == (f"{origin}{setup.event_path}" if origin else None)
        assert "private-token" not in setup.model_dump_json()
        outsider = AuthenticatedActor(
            principal=PrincipalRef(principal_type="user", principal_id="usr_outsider123456789"),
            auth_method="session",
            credential_id="ses_outsider",
            boundary_workspace_id=WORKSPACE_ID,
        )
        with pytest.raises(NativeError) as failure:
            await service.setup(actor=outsider, account_id=account.id)
        assert failure.value.code == "resource_not_found"
