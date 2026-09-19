"""Bot file transports, persisted inputs, and current-run-only result delivery."""

import json
from dataclasses import replace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs

import httpx2
import pytest
from a13n_harness.http import ProviderHttpError
from a13n_service.assets.catalog import AssetCatalog, PreparedAssetContent
from a13n_service.assets.domain import Asset, RunOutputAssetSource, UploadedAssetSource
from a13n_service.assets.models import AssetRecord
from a13n_service.assets.objects import AssetObjectStore
from a13n_service.assets.staging import AssetStaging
from a13n_service.assets.uploads import AssetUploadService
from a13n_service.connectivity.accounts.models import AccountRecord
from a13n_service.connectivity.accounts.reception import InputBatchingPolicy
from a13n_service.connectivity.file_content import MAX_FILE_BYTES, FileContent
from a13n_service.connectivity.file_delivery import FileDelivery, SendFileArguments
from a13n_service.connectivity.ingress.admission_domain import BatchConfiguration, PreparedIngressBatch
from a13n_service.connectivity.ingress.attachments import AttachmentInputs
from a13n_service.connectivity.ingress.provider import ExternalRef, ProviderEventDecision
from a13n_service.connectivity.native_context import InboundRunContext
from a13n_service.connectivity.providers.lark.files import LarkFiles
from a13n_service.connectivity.providers.slack.adapter import SlackAccountConfig, normalize_payload
from a13n_service.connectivity.providers.slack.files import SlackFiles
from a13n_service.iam import PrincipalRef
from a13n_service.interactions.input import AgentInput, BinaryContent, TextContent
from a13n_service.storage import short_session, transaction
from a13n_service.storage.object_store import LocalObjectStore
from pydantic import ValidationError
from sqlalchemy import func, select

from .conftest import ACCOUNT_ID, NOW, ORG_ID, SERVICE_ACCOUNT_ID, WORKSPACE_ID, actor
from .test_lark_client import _AllowEndpoint, _token_response
from .test_slack import _config, _event_payload

pytestmark = pytest.mark.anyio
_FILE = FileContent("report.md", "text/markdown", b"# Report\nVerified result")
_RUN = "run_aaaaaaaaaaaaaaaa"
_ASSET = "ast_aaaaaaaaaaaaaaaa"


async def test_slack_file_share_keeps_only_identifiers_and_preserves_empty_caption():
    payload = json.loads(_event_payload(text="<@UBOT>"))
    payload["event"].update(subtype="file_share", files=[{"id": "F1", "url_private": "https://bad.invalid/token"}])
    decision = normalize_payload(payload, SlackAccountConfig.model_validate(_config()), NOW)
    assert isinstance(decision, ProviderEventDecision)
    assert decision.event.data == {"attachments": [{"id": "F1"}]}
    assert "bad.invalid" not in decision.event.model_dump_json()


async def test_slack_download_uses_file_info_not_event_url():
    requests = []

    def respond(request):
        requests.append(request)
        assert request.headers["authorization"] == "Bearer private"
        if request.url.path == "/api/files.info":
            assert request.url.params["file"] == "F1"
            return httpx2.Response(
                200,
                json={
                    "ok": True,
                    "file": {
                        "id": "F1",
                        "name": "note.txt",
                        "mimetype": "text/plain",
                        "url_private_download": "https://files.slack.com/files-pri/F1",
                    },
                },
            )
        return httpx2.Response(200, content=b"hello")

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http:
        file = await SlackFiles(http, _AllowEndpoint(), "private").download("F1")
    assert file.body == b"hello" and file.filename == "note.txt"
    assert len(requests) == 2


@pytest.mark.parametrize(
    "url",
    [
        "https://bad.invalid/file",
        "http://files.slack.com/file",
        "https://files.slack.com:bad/file",
        "https://user@files.slack.com/file",
        "https://files.slack.com/file#token",
    ],
)
async def test_slack_denies_untrusted_download_hosts_without_sending_credentials(url):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx2.Response(
            200, json={"ok": True, "file": {"id": "F1", "name": "a.txt", "mimetype": "text/plain", "url_private": url}}
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http:
        with pytest.raises(ProviderHttpError, match="endpoint_denied"):
            await SlackFiles(http, _AllowEndpoint(), "private").download("F1")
    assert len(requests) == 1


@pytest.mark.parametrize(
    "status,headers,reason",
    [
        (302, {"location": "https://bad.invalid/file"}, "attachment_download_failed"),
        (200, {"content-length": str(MAX_FILE_BYTES + 1)}, "response_too_large"),
    ],
)
async def test_slack_download_rejects_redirects_and_oversized_content(status, headers, reason):
    requests = []

    def respond(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx2.Response(
                200,
                json={
                    "ok": True,
                    "file": {
                        "id": "F1",
                        "name": "a.txt",
                        "mimetype": "text/plain",
                        "url_private": "https://files.slack.com/F1",
                    },
                },
            )
        return httpx2.Response(status, headers=headers)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond), follow_redirects=True) as http:
        with pytest.raises(ProviderHttpError, match=reason):
            await SlackFiles(http, _AllowEndpoint(), "private").download("F1")
    assert len(requests) == 2


async def test_slack_upload_has_no_bot_token_and_completes_in_bound_thread():
    requests = []

    def respond(request):
        requests.append(request)
        if request.url.path.endswith("getUploadURLExternal"):
            assert request.headers["content-type"] == "application/x-www-form-urlencoded"
            assert parse_qs(request.content.decode()) == {
                "filename": [_FILE.filename],
                "length": [str(len(_FILE.body))],
            }
            return httpx2.Response(
                200, json={"ok": True, "file_id": "F1", "upload_url": "https://files.slack.com/upload/test"}
            )
        if request.url.host == "files.slack.com":
            assert "authorization" not in request.headers
            assert request.content == _FILE.body
            return httpx2.Response(200, content=b"OK")
        assert json.loads(request.content) == {
            "files": [{"id": "F1", "title": "report.md"}],
            "channel_id": "C-bound",
            "thread_ts": "1.0",
        }
        return httpx2.Response(200, json={"ok": True, "files": [{"id": "F1"}]})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http:
        assert (
            await SlackFiles(http, _AllowEndpoint(), "private").send(_FILE, channel_id="C-bound", thread_ts="1.0")
            == "F1"
        )
    assert len(requests) == 3


async def test_lark_resource_download_and_file_reply_use_authenticated_source():
    requests = []

    def respond(request):
        requests.append(request)
        if request.url.path.endswith("tenant_access_token/internal"):
            return _token_response()
        assert request.headers["authorization"] == "Bearer tenant-token"
        if request.method == "GET":
            assert request.url.path == "/open-apis/im/v1/messages/om_source/resources/file_source"
            assert request.url.params["type"] == "file"
            return httpx2.Response(200, content=_FILE.body, headers={"content-type": "application/octet-stream"})
        if request.url.path.endswith("/files"):
            assert _FILE.body in request.content and b"report.md" in request.content
            return httpx2.Response(200, json={"code": 0, "data": {"file_key": "file_result"}})
        assert request.url.path == "/open-apis/im/v1/messages/om_source/reply"
        payload = json.loads(request.content)
        assert payload["reply_in_thread"] is True and payload["msg_type"] == "file"
        assert json.loads(payload["content"]) == {"file_key": "file_result"}
        return httpx2.Response(200, json={"code": 0, "data": {"message_id": "om_reply"}})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http:
        files = LarkFiles(http, _AllowEndpoint(), origin="https://open.feishu.cn", app_id="cli_app", secret="private")
        downloaded = await files.download(message_id="om_source", key="file_source", kind="file", filename="report.md")
        assert downloaded == _FILE
        assert (
            await files.send(downloaded, chat_id="oc_bound", message_id="om_source", reply_in_thread=True) == "om_reply"
        )
    assert len(requests) == 4


def _batch(attachments):
    return PreparedIngressBatch(
        batch_id="ib_aaaaaaaaaaaaaaaa",
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        binding_id="binding-test",
        external_ref=ExternalRef(kind="conversation", id="C-bound"),
        claim_owner="worker",
        claim_generation=1,
        configuration=BatchConfiguration(
            account_id=ACCOUNT_ID,
            account_version=1,
            target_id=None,
            target_version=None,
            target_kind="conversation",
            external_target_id="C-bound",
            provider_key="slack",
            provider_context_version="slack_v1",
            provider_context={},
            provider_policy={},
            native_actions=(),
            input_batching=InputBatchingPolicy(min_interval_ms=100, max_batch_events=10),
        ),
        agent_input=AgentInput(
            schema_version="2",
            content=(),
            structured_content={
                "events": [{"context": {"channel_id": "C-bound"}, "data": {"attachments": attachments}}]
            },
        ),
    )


async def test_inputs_persist_assets_deduplicate_retries_and_report_unread_files(
    connectivity_sessions, credential_protector, tmp_path
):
    runner = replace(
        actor(),
        principal=PrincipalRef(principal_type="service_account", principal_id=SERVICE_ACCOUNT_ID),
        auth_method="internal",
    )
    async with transaction(connectivity_sessions) as session:
        account = await session.get(AccountRecord, ACCOUNT_ID)
        account.provider_key = "slack"
        account.replace_credential('{"bot_token":"private"}', credential_protector)
    staging = await AssetStaging.create(tmp_path)
    objects = AssetObjectStore(await LocalObjectStore.create(tmp_path / "objects"), staging)
    uploads = AssetUploadService(connectivity_sessions, objects, staging, max_size_bytes=MAX_FILE_BYTES)
    requests = []

    def respond(request):
        requests.append(request)
        if request.url.path == "/api/files.info":
            identifier = request.url.params["file"]
            if identifier == "F-bad":
                return httpx2.Response(200, json={"ok": False, "error": "missing_scope"})
            name, mime = ("note.txt", "text/plain") if identifier == "F-text" else ("note.pdf", "application/pdf")
            return httpx2.Response(
                200,
                json={
                    "ok": True,
                    "file": {
                        "id": identifier,
                        "name": name,
                        "mimetype": mime,
                        "url_private": f"https://files.slack.com/{identifier}",
                    },
                },
            )
        return httpx2.Response(200, content=b"remember 42" if request.url.path == "/F-text" else b"%PDF-1.4\n%%EOF\n")

    batch = _batch([{"id": "F-text"}, {"id": "F-pdf"}, {"id": "F-bad"}])
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http:
        inputs = AttachmentInputs(connectivity_sessions, uploads, credential_protector, http, _AllowEndpoint())
        first = await inputs.materialize(batch, runner)
        second = await inputs.materialize(batch, runner)
        assert first == second
        assert isinstance(first.content[0], TextContent) and "remember 42" in first.content[0].text
        assert isinstance(first.content[1], BinaryContent)
        assert isinstance(first.content[2], TextContent) and "was not analyzed" in first.content[2].text
        async with short_session(connectivity_sessions) as session:
            assert await session.scalar(select(func.count()).select_from(AssetRecord)) == 2
        # Revocation is checked before fetching bytes, even for a prior successful import.
        async with transaction(connectivity_sessions) as session:
            account = await session.get(AccountRecord, ACCOUNT_ID)
            account.status = "disabled"
        requests.clear()
        denied = await inputs.materialize(batch, runner)
        assert not requests
        assert all(
            isinstance(item, TextContent) and "attachment_source_changed" in item.text for item in denied.content
        )


def _asset(**updates):
    return Asset(
        id=_ASSET,
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        filename=_FILE.filename,
        media_type=_FILE.media_type,
        size_bytes=len(_FILE.body),
        content_sha256="a" * 64,
        source=RunOutputAssetSource(run_id=_RUN),
        created_at=NOW,
        deleted_at=None,
    ).model_copy(update=updates)


def _context():
    return InboundRunContext(
        binding_id="binding-test",
        account_id=ACCOUNT_ID,
        execution_principal_ref=actor().principal,
        provider_key="slack",
        provider_context_version="slack_v1",
        provider_context={"channel_id": "C-bound", "root_thread_ts": "1.0", "conversation_kind": "channel"},
        action_policy={"reply_mode": "thread"},
        allowed_actions=("slack.send_file",),
    )


@pytest.mark.parametrize(
    "source", [RunOutputAssetSource(run_id="run_bbbbbbbbbbbbbbbb"), UploadedAssetSource(principal=actor().principal)]
)
async def test_send_file_rejects_assets_not_published_in_current_run(source):
    catalog = AsyncMock(spec=AssetCatalog)
    catalog.get.return_value = _asset(source=source)
    async with httpx2.AsyncClient() as http:
        with pytest.raises(ProviderHttpError, match="file_not_from_current_run"):
            await FileDelivery(catalog).send(
                SendFileArguments(asset_id=_ASSET),
                actor=actor(),
                run_id=_RUN,
                context=_context(),
                configuration={},
                credentials={"bot_token": "private"},
                http=http,
                endpoints=_AllowEndpoint(),
                guard=AsyncMock(),
            )
    catalog.prepare_content.assert_not_awaited()


@pytest.mark.parametrize("lost_response", [False, True])
async def test_send_file_cleans_staging_and_never_retries_unknown_completion(tmp_path, lost_response):
    staging = AssetStaging(tmp_path)

    async def chunks():
        yield _FILE.body

    content = await staging.stage_upload(chunks(), max_size_bytes=MAX_FILE_BYTES, content_length=len(_FILE.body))
    catalog = AsyncMock(spec=AssetCatalog)
    catalog.get.return_value = _asset()
    catalog.prepare_content.return_value = PreparedAssetContent(_asset(), content)
    completions = []

    def respond(request):
        if request.url.path.endswith("getUploadURLExternal"):
            return httpx2.Response(
                200, json={"ok": True, "file_id": "F1", "upload_url": "https://files.slack.com/upload/test"}
            )
        if request.url.host == "files.slack.com":
            return httpx2.Response(200)
        completions.append(request)
        if lost_response:
            raise httpx2.ReadTimeout("lost")
        return httpx2.Response(200, json={"ok": True, "files": [{"id": "F1"}]})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http:
        result = await FileDelivery(catalog).send(
            SendFileArguments(asset_id=_ASSET),
            actor=actor(),
            run_id=_RUN,
            context=_context(),
            configuration={},
            credentials={"bot_token": "private"},
            http=http,
            endpoints=_AllowEndpoint(),
            guard=AsyncMock(),
        )
    assert result.kind == ("outcome_unknown" if lost_response else "succeeded")
    assert len(completions) == 1
    assert not list(tmp_path.iterdir())


def test_send_file_has_no_caller_selected_target_path_or_url():
    with pytest.raises(ValidationError):
        SendFileArguments.model_validate({"asset_id": _ASSET, "channel_id": "C-other"})


async def test_slack_revocation_after_upload_prevents_final_share():
    paths = []

    def respond(request):
        paths.append(request.url.path)
        if request.url.path.endswith("getUploadURLExternal"):
            return httpx2.Response(
                200, json={"ok": True, "file_id": "F1", "upload_url": "https://files.slack.com/upload/test"}
            )
        return httpx2.Response(200)

    async def revoked():
        raise ValueError("native_source_changed")

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http:
        with pytest.raises(ValueError, match="native_source_changed"):
            await SlackFiles(http, _AllowEndpoint(), "private").send(
                _FILE, channel_id="C-bound", thread_ts="1.0", guard=revoked
            )
    assert paths == ["/api/files.getUploadURLExternal", "/upload/test"]


async def test_lark_rich_post_projects_image_for_deferred_import():
    from a13n_service.connectivity.providers.lark import LarkIngressAdapter

    from .test_lark import _APP_SECRET, _ENCRYPT_KEY, _VERIFICATION_TOKEN, _encrypted_request, _payload
    from .test_lark import _config as lark_config

    payload = _payload(
        message_type="post",
        content={
            "zh_cn": {
                "title": "Screenshot",
                "content": [[{"tag": "text", "text": "@_user_1 summarize"}, {"tag": "img", "image_key": "img_source"}]],
            }
        },
    )
    decision = await LarkIngressAdapter().authenticate_and_normalize(
        _encrypted_request(payload),
        account_id=ACCOUNT_ID,
        account_config=lark_config(),
        credentials={"app_secret": _APP_SECRET, "encrypt_key": _ENCRYPT_KEY, "verification_token": _VERIFICATION_TOKEN},
        received_at=NOW,
    )
    assert isinstance(decision, ProviderEventDecision)
    assert decision.event.data["attachments"] == [{"type": "image", "image_key": "img_source"}]
