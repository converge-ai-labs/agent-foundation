"""Account lifecycle, independent operation, and exact target authorization."""

import json

import httpx2
import pytest
from a13n_service.agents.domain import AccountToolSelection
from a13n_service.connectivity.accounts.actions import account_actions
from a13n_service.connectivity.accounts.domain import (
    AccountStatus,
    CreateAccountRequest,
    ReplaceAccountCredentialsRequest,
    UpdateAccountRequest,
)
from a13n_service.connectivity.accounts.models import AccountRecord
from a13n_service.connectivity.errors import NativeError
from a13n_service.connectivity.execution import AttemptToolScope
from a13n_service.connectivity.ingress.domain import IngressStatus
from a13n_service.connectivity.native import IngressRunContext, native_capability
from a13n_service.connectivity.selection_resolution import ConnectivitySelectionResolver, FrozenRunConnectivity
from a13n_service.endpoint_policy import EndpointPolicy
from a13n_service.iam import AuthenticatedActor, PrincipalRef
from a13n_service.storage import transaction
from pydantic import ValidationError

from .conftest import ACCOUNT_ID, ORG_ID, SERVICE_ACCOUNT_ID, WORKSPACE_ID, actor
from .test_ingress_service import ingress_request

pytestmark = pytest.mark.anyio


def request():
    return CreateAccountRequest(
        name="Another account",
        provider_key="fake",
        provider_config_version="fake_http_v1",
        provider_config={"installation_id": "installation-2"},
        credentials={"token": "private"},
    )


async def test_account_credentials_identity_and_idempotency(account_service):
    value = await account_service.create_account(
        actor=actor(), workspace_id=WORKSPACE_ID, idempotency_key="create-account", request=request()
    )
    assert (
        await account_service.create_account(
            actor=actor(), workspace_id=WORKSPACE_ID, idempotency_key="create-account", request=request()
        )
        == value
    )
    assert "private" not in value.model_dump_json()
    with pytest.raises(NativeError) as duplicate:
        await account_service.create_account(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            idempotency_key="duplicate-account",
            request=request().model_copy(update={"name": "Another name"}),
        )
    assert duplicate.value.code == "account_conflict"
    with pytest.raises(NativeError) as changed:
        await account_service.update_account(
            actor=actor(),
            account_id=value.id,
            request=UpdateAccountRequest(expected_version=1, provider_config={"installation_id": "different"}),
        )
    assert changed.value.code == "immutable_account_identity"
    rotation = ReplaceAccountCredentialsRequest(expected_version=1, credentials={"token": "replacement"})
    rotated = await account_service.replace_credentials(
        actor=actor(), account_id=value.id, idempotency_key="rotate-account", request=rotation
    )
    assert rotated.id == value.id and rotated.credential_generation == rotated.version == 2
    assert (
        await account_service.replace_credentials(
            actor=actor(), account_id=value.id, idempotency_key="rotate-account", request=rotation
        )
        == rotated
    )
    with pytest.raises(NativeError) as stale:
        await account_service.update_account(
            actor=actor(), account_id=value.id, request=UpdateAccountRequest(expected_version=1, name="stale")
        )
    assert stale.value.code == "version_conflict"


async def test_account_has_at_most_one_ingress_and_reference_blocks_deletion(account_service, ingress_service):
    ingress = await ingress_service.create_ingress(
        actor=actor(), workspace_id=WORKSPACE_ID, idempotency_key="first-ingress", request=ingress_request()
    )
    with pytest.raises(NativeError) as duplicate:
        await ingress_service.create_ingress(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            idempotency_key="second-ingress",
            request=ingress_request().model_copy(update={"name": "Second ingress"}),
        )
    assert duplicate.value.code == "ingress_conflict"
    await ingress_service.set_status(
        actor=actor(),
        ingress_id=ingress.id,
        status=IngressStatus.disabled,
        expected_version=1,
        idempotency_key="pause-input",
    )
    assert (await account_service.get_account(actor=actor(), account_id=ACCOUNT_ID)).status == AccountStatus.active
    with pytest.raises(NativeError) as used:
        await account_service.delete_account(actor=actor(), account_id=ACCOUNT_ID, expected_version=1)
    assert used.value.code == "account_in_use"


async def test_delete_account_clears_material(account_service, connectivity_sessions):
    value = await account_service.create_account(
        actor=actor(), workspace_id=WORKSPACE_ID, idempotency_key="delete-me", request=request()
    )
    await account_service.delete_account(actor=actor(), account_id=value.id, expected_version=1)
    async with transaction(connectivity_sessions) as session:
        row = await session.get(AccountRecord, value.id)
        assert (
            row.deleted_at is not None
            and row.ciphertext is None
            and row.nonce is None
            and row.encryption_key_id is None
        )
    with pytest.raises(NativeError):
        await account_service.get_account(actor=actor(), account_id=value.id)
    replacement = await account_service.create_account(
        actor=actor(), workspace_id=WORKSPACE_ID, idempotency_key="recreate-account", request=request()
    )
    assert replacement.id != value.id


async def test_proactive_send_requires_exact_target_and_keeps_unknown_outcome():
    requests = []

    def send(req):
        requests.append(json.loads(req.content))
        if len(requests) > 1:
            raise httpx2.ReadTimeout("lost after dispatch")
        return httpx2.Response(200, json={"ok": True, "channel": "C1", "ts": "1.0"})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(send)) as http:
        tools = account_actions("slack", {}, {"bot_token": "private"}, {"channel_ids": ["C1"]}, http, EndpointPolicy())
        tool = tools["slack.send_message"]
        with pytest.raises(ValueError, match="target_not_authorized"):
            await tool.call({"channel_id": "C2", "text": "denied"})
        with pytest.raises(ValidationError):
            await tool.call({"channel_id": "C1", "text": "denied", "account_id": "other"})
        assert requests == []
        assert await tool.call({"channel_id": "C1", "text": "hello"}) == {"kind": "succeeded"}
        assert requests == [{"channel": "C1", "text": "hello"}]
        assert (await tool.call({"channel_id": "C1", "text": "next"}))["kind"] == "outcome_unknown"


async def test_account_selection_reauthorizes_current_availability(connectivity_sessions, account_service):
    async with transaction(connectivity_sessions) as session:
        row = await session.get(AccountRecord, ACCOUNT_ID)
        row.provider_key = "slack"
    resolver = ConnectivitySelectionResolver(connectivity_sessions)
    selected = AccountToolSelection(
        account_id=ACCOUNT_ID, tools=("slack.send_message",), target_scope={"channel_ids": ["C1"]}
    )
    prepared = await resolver.prepare_revision_creation(
        actor=actor(),
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        connector_tools=(),
        mcp_tools=(),
        account_tools=(selected,),
    )
    assert prepared.selections.account_selections == (selected,)
    await account_service.set_status(
        actor=actor(),
        account_id=ACCOUNT_ID,
        status=AccountStatus.disabled,
        expected_version=1,
        idempotency_key="disable",
    )
    with pytest.raises(ValueError):
        AccountToolSelection(
            account_id=ACCOUNT_ID,
            tools=("slack.send_message", "slack.send_message"),
            target_scope={"channel_ids": ["C1"]},
        )
    from a13n_service.connectivity.selection_resolution import ConnectivitySelectionError

    async with transaction(connectivity_sessions) as session:
        with pytest.raises(ConnectivitySelectionError, match="account_unavailable"):
            await resolver.freeze_revision_creation(session, prepared=prepared)


async def test_paused_ingress_keeps_accepted_reply_but_disabled_account_blocks_it(
    ingress_service, account_service, connectivity_sessions, credential_protector
):
    from a13n_service.connectivity.providers.slack.adapter import CONTEXT_VERSION

    ingress = await ingress_service.create_ingress(
        actor=actor(), workspace_id=WORKSPACE_ID, idempotency_key="reply-ingress", request=ingress_request()
    )
    async with transaction(connectivity_sessions) as session:
        account = await session.get(AccountRecord, ACCOUNT_ID)
        account.provider_key = "slack"
        account.replace_credential('{"bot_token":"private"}', credential_protector)
    principal = PrincipalRef(principal_type="service_account", principal_id=SERVICE_ACCOUNT_ID)
    context = IngressRunContext(
        ingress_id=ingress.id,
        account_id=ACCOUNT_ID,
        execution_principal_ref=principal,
        provider_key="slack",
        provider_context_version=CONTEXT_VERSION,
        provider_context={"channel_id": "C1", "root_thread_ts": "1.0", "conversation_kind": "channel"},
        action_policy={"reply_mode": "thread"},
        allowed_actions=("slack.reply",),
    )
    scope = AttemptToolScope(
        AuthenticatedActor(
            principal=principal,
            auth_method="internal",
            credential_id="account-test",
            boundary_workspace_id=WORKSPACE_ID,
        ),
        ORG_ID,
        WORKSPACE_ID,
        FrozenRunConnectivity((), ()),
        context.model_dump(mode="json"),
    )

    async def guard():
        pass

    await ingress_service.set_status(
        actor=actor(), ingress_id=ingress.id, status=IngressStatus.disabled, expected_version=1, idempotency_key="pause"
    )
    assert (
        await native_capability(connectivity_sessions, credential_protector, scope, guard, EndpointPolicy()) is not None
    )
    await account_service.set_status(
        actor=actor(),
        account_id=ACCOUNT_ID,
        status=AccountStatus.disabled,
        expected_version=1,
        idempotency_key="stop-account",
    )
    with pytest.raises(ValueError, match="native_source_unavailable"):
        await native_capability(connectivity_sessions, credential_protector, scope, guard, EndpointPolicy())


async def test_rotation_changes_webhook_auth_without_changing_admission_identity(
    account_service, ingress_service, ingress_event_service
):
    from .test_admission import _request

    ingress = await ingress_service.create_ingress(
        actor=actor(), workspace_id=WORKSPACE_ID, idempotency_key="rotation-ingress", request=ingress_request()
    )
    delivery = _request("same-delivery")
    first = await ingress_event_service.receive(ingress_id=ingress.id, request=delivery)
    assert first.status_code == 202
    await account_service.replace_credentials(
        actor=actor(),
        account_id=ACCOUNT_ID,
        idempotency_key="rotate-webhook",
        request=ReplaceAccountCredentialsRequest(expected_version=1, credentials={"token": "replacement"}),
    )
    assert (await ingress_event_service.receive(ingress_id=ingress.id, request=delivery)).status_code == 401
    delivery = delivery.model_copy(update={"headers": {**delivery.headers, "authorization": "Bearer replacement"}})
    replay = await ingress_event_service.receive(ingress_id=ingress.id, request=delivery)
    assert replay.status_code == first.status_code
    assert json.loads(replay.body)["admission_id"] == json.loads(first.body)["admission_id"]
    assert json.loads(replay.body)["duplicate"] is True
    await account_service.set_status(
        actor=actor(),
        account_id=ACCOUNT_ID,
        status=AccountStatus.disabled,
        expected_version=2,
        idempotency_key="disable-webhook",
    )
    with pytest.raises(NativeError, match="Ingress was not found"):
        await ingress_event_service.receive(ingress_id=ingress.id, request=delivery)


async def test_github_proactive_scope_binds_repository_token_and_target(github_private_key_pem):
    from datetime import timedelta

    from a13n_service.temporal import utc_now

    from .test_github_client import _AllowEndpoint

    requests = []

    def respond(req):
        requests.append(req)
        if req.url.path.endswith("access_tokens"):
            assert json.loads(req.content)["repository_ids"] == [42]
            return httpx2.Response(
                201, json={"token": "github-token", "expires_at": (utc_now() + timedelta(hours=1)).isoformat()}
            )
        assert req.url.path == "/repos/acme/repo/issues/7/comments"
        return httpx2.Response(200, json=[])

    config = {
        "api_origin": "https://api.github.com",
        "web_origin": "https://github.com",
        "app_id": 1,
        "installation_id": 2,
        "installation_account_id": 3,
        "bot_account_id": 4,
    }
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http:
        tools = account_actions(
            "github",
            config,
            {"app_private_key_pem": github_private_key_pem},
            {"repositories": [{"repository_id": 42, "owner": "acme", "repository": "repo"}]},
            http,
            _AllowEndpoint(),
        )
        arguments = {"repository_id": 43, "number": 7, "target_kind": "issue"}
        with pytest.raises(ValueError, match="target_not_authorized"):
            await tools["github.read_comments"].call(arguments)
        assert not requests
        arguments["repository_id"] = 42
        result = await tools["github.read_comments"].call(arguments)
        assert result["items"] == [] and len(requests) == 2
        with pytest.raises(ValueError, match="action_unavailable"):
            await tools["github.list_pr_files"].call(arguments)
        assert len(requests) == 2


async def test_lark_proactive_send_requires_scope_without_inbound_context():
    from .test_github_client import _AllowEndpoint
    from .test_lark import _config

    requests = []

    def respond(req):
        requests.append(req)
        if "tenant_access_token" in req.url.path:
            return httpx2.Response(200, json={"code": 0, "tenant_access_token": "lark-token", "expire": 7200})
        assert req.url.path == "/open-apis/im/v1/messages"
        assert req.url.params["receive_id_type"] == "chat_id"
        assert json.loads(req.content)["receive_id"] == "C1"
        return httpx2.Response(200, json={"code": 0, "data": {"message_id": "message-1"}})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http:
        tools = account_actions(
            "lark", _config(), {"app_secret": "private"}, {"chat_ids": ["C1"]}, http, _AllowEndpoint()
        )
        args = {"chat_id": "C2", "content": {"kind": "text", "text": "hello"}}
        with pytest.raises(ValueError, match="target_not_authorized"):
            await tools["lark.send_message"].call(args)
        assert not requests
        args["chat_id"] = "C1"
        assert await tools["lark.send_message"].call(args) == {"kind": "succeeded"}
        assert len(requests) == 2


async def test_account_metadata_does_not_grant_use_or_management(account_service, connectivity_sessions):
    from a13n_service.connectivity.selection_resolution import ConnectivitySelectionError
    from a13n_service.iam.models import RoleBindingRecord

    async with transaction(connectivity_sessions) as session:
        role = await session.get(RoleBindingRecord, "rb_connectivity_admin")
        role.role_key = "viewer"
    metadata = await account_service.get_account(actor=actor(), account_id=ACCOUNT_ID)
    assert metadata.credential_configured and "ciphertext" not in metadata.model_dump()
    with pytest.raises(NativeError) as denied:
        await account_service.replace_credentials(
            actor=actor(),
            account_id=ACCOUNT_ID,
            idempotency_key="viewer-rotate",
            request=ReplaceAccountCredentialsRequest(expected_version=1, credentials={"token": "replacement"}),
        )
    assert denied.value.status_code == 404
    resolver = ConnectivitySelectionResolver(connectivity_sessions)
    selection = AccountToolSelection(
        account_id=ACCOUNT_ID, tools=("slack.send_message",), target_scope={"channel_ids": ["C1"]}
    )
    with pytest.raises(ConnectivitySelectionError, match="connection_not_eligible"):
        await resolver.prepare_revision_creation(
            actor=actor(),
            organization_id=ORG_ID,
            workspace_id=WORKSPACE_ID,
            connector_tools=(),
            mcp_tools=(),
            account_tools=(selection,),
        )
    async with transaction(connectivity_sessions) as session:
        role = await session.get(RoleBindingRecord, "rb_connectivity_admin")
        role.role_key = "admin"
    with pytest.raises(ConnectivitySelectionError, match="account_unavailable"):
        await resolver.prepare_revision_creation(
            actor=actor(),
            organization_id="org_other1234567890",
            workspace_id=WORKSPACE_ID,
            connector_tools=(),
            mcp_tools=(),
            account_tools=(selection,),
        )
