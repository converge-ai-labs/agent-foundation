"""Pre-account identity discovery uses trusted provider reads and current IAM."""

import json

import anyio
import httpx2
import pytest
from a13n_service.bots.connectivity.domain import DiscoverFeishuInstallationRequest
from a13n_service.bots.connectivity.service import BotService
from a13n_service.connectivity.accounts.models import AccountRecord
from a13n_service.connectivity.errors import NativeError
from a13n_service.endpoint_policy import EndpointPolicy
from a13n_service.iam import AuthenticatedActor, PrincipalRef
from a13n_service.storage import transaction
from sqlalchemy import func, select

from .conftest import WORKSPACE_ID, actor

pytestmark = pytest.mark.anyio


def discovery_request():
    return DiscoverFeishuInstallationRequest(app_id="cli_test", app_secret="fictional-app-secret")


def respond(request, *, enabled=True):
    assert request.url.host == "open.feishu.cn"
    if request.url.path == "/open-apis/auth/v3/tenant_access_token/internal":
        assert json.loads(request.content) == {"app_id": "cli_test", "app_secret": "fictional-app-secret"}
        return httpx2.Response(200, json={"code": 0, "tenant_access_token": "fictional-token", "expire": 7200})
    assert request.headers["authorization"] == "Bearer fictional-token"
    if request.url.path == "/open-apis/bot/v3/info":
        return httpx2.Response(
            200,
            json={
                "code": 0,
                "bot": {
                    "activate_status": 2 if enabled else 0,
                    "app_name": "Test bot",
                    "open_id": "ou_verified",
                },
            },
        )
    assert request.url.path == "/open-apis/tenant/v2/tenant/query"
    return httpx2.Response(
        200, json={"code": 0, "data": {"tenant": {"tenant_key": "verified-tenant", "name": "Test enterprise"}}}
    )


async def test_discovery_derives_identity_without_persisting_account(connectivity_sessions, credential_protector):
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http:
        service = BotService(connectivity_sessions, http, EndpointPolicy(), credential_protector)
        result = await service.discover_feishu_installation(
            actor=actor(), workspace_id=WORKSPACE_ID, request=discovery_request()
        )
    assert (result.app_id, result.organization_id, result.bot_id) == ("cli_test", "verified-tenant", "ou_verified")
    assert result.enabled
    assert "fictional-app-secret" not in result.model_dump_json()
    assert "fictional-app-secret" not in repr(discovery_request())
    async with transaction(connectivity_sessions) as session:
        assert (
            await session.scalar(select(func.count()).select_from(AccountRecord)) == 1
        )  # Existing fixture Account only.


@pytest.mark.parametrize(
    "failure,code",
    [
        ("credential", "provider_rejected"),
        ("tenant", "provider_rejected"),
        ("inactive", "bot_inactive"),
        ("malformed", "invalid_provider_response"),
        ("timeout", "provider_unavailable"),
    ],
)
async def test_discovery_failure_does_not_create_account(connectivity_sessions, credential_protector, failure, code):
    async def failing(request):
        if failure == "timeout":
            await anyio.sleep(1)
        if failure == "credential" or (failure == "tenant" and request.url.path.endswith("/tenant/query")):
            return httpx2.Response(200, json={"code": 99991663, "msg": "fictional-private-upstream-detail"})
        if failure == "malformed" and request.url.path.endswith("/bot/v3/info"):
            return httpx2.Response(200, json={"code": 0, "bot": {}})
        return respond(request, enabled=failure != "inactive")

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(failing)) as http:
        service = BotService(
            connectivity_sessions,
            http,
            EndpointPolicy(),
            credential_protector,
            timeout_seconds=0.05 if failure == "timeout" else 20,
        )
        with pytest.raises(NativeError) as error:
            await service.discover_feishu_installation(
                actor=actor(), workspace_id=WORKSPACE_ID, request=discovery_request()
            )
        assert error.value.code == code
        assert "fictional-private" not in str(error.value)
    async with transaction(connectivity_sessions) as session:
        assert (
            await session.scalar(select(func.count()).select_from(AccountRecord)) == 1
        )  # Existing fixture Account only.


async def test_discovery_rejects_unauthorized_before_provider_io(connectivity_sessions, credential_protector):
    def unexpected(_request):
        pytest.fail("Unauthorized discovery must not send credentials")

    outsider = AuthenticatedActor(
        principal=PrincipalRef(principal_type="user", principal_id="usr_outsider123456789"),
        auth_method="session",
        credential_id="ses_outsider",
        boundary_workspace_id=WORKSPACE_ID,
    )
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(unexpected)) as http:
        service = BotService(connectivity_sessions, http, EndpointPolicy(), credential_protector)
        with pytest.raises(NativeError):
            await service.discover_feishu_installation(
                actor=outsider, workspace_id=WORKSPACE_ID, request=discovery_request()
            )


async def test_discovery_rechecks_authority_after_network_io(connectivity_sessions, credential_protector):
    from a13n_service.iam.models import RoleBindingRecord
    from sqlalchemy import delete

    async def revoking(request):
        if request.url.path.endswith("/tenant/query"):
            async with transaction(connectivity_sessions) as session:
                await session.execute(delete(RoleBindingRecord).where(RoleBindingRecord.id == "rb_connectivity_admin"))
        return respond(request)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(revoking)) as http:
        service = BotService(connectivity_sessions, http, EndpointPolicy(), credential_protector)
        with pytest.raises(NativeError):
            await service.discover_feishu_installation(
                actor=actor(), workspace_id=WORKSPACE_ID, request=discovery_request()
            )
