"""Resolve authenticated message attachments into the existing Asset input protocol."""

import hashlib
import json
from collections.abc import AsyncIterator
from time import monotonic

import anyio
import httpx2
from a13n_harness.providers.http import EndpointValidator, ProviderHttpError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.assets.errors import AssetError
from a13n_service.assets.uploads import AssetUploadService
from a13n_service.connectivity.accounts.models import AccountRecord
from a13n_service.connectivity.domain import JsonObject
from a13n_service.connectivity.file_content import MAX_ATTACHMENTS, FileContent
from a13n_service.connectivity.providers.lark.files import LarkFiles
from a13n_service.connectivity.providers.slack.files import SlackFiles
from a13n_service.iam import AuthenticatedActor
from a13n_service.iam.authorization import AuthorizationError, WorkspaceAction, authorize_workspace
from a13n_service.interactions.input import AgentInput, AssetBinarySource, BinaryContent, TextContent
from a13n_service.secrets import SecretProtector
from a13n_service.storage import short_session

from .admission_domain import PreparedIngressBatch


class AttachmentInputs:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        uploads: AssetUploadService,
        protector: SecretProtector,
        http: httpx2.AsyncClient,
        endpoints: EndpointValidator,
    ) -> None:
        self.sessions = sessions
        self.uploads = uploads
        self.protector = protector
        self.http = http
        self.endpoints = endpoints

    async def materialize(self, batch: PreparedIngressBatch, actor: AuthenticatedActor) -> AgentInput:
        config = batch.configuration
        if config.provider_key not in {"slack", "lark"}:
            return batch.agent_input
        structured = batch.agent_input.structured_content
        events = structured.get("events") if isinstance(structured, dict) else None
        if not isinstance(events, list):
            return batch.agent_input
        content = list(batch.agent_input.content)
        count = 0
        deadline = monotonic() + 15
        for event in events:
            if not isinstance(event, dict):
                continue
            data, context = event.get("data"), event.get("context")
            if not isinstance(data, dict) or not isinstance(context, dict):
                continue
            attachments = data.get("attachments", [])
            if isinstance(data.get("attachment"), dict):
                attachments = [data["attachment"]]
            if not isinstance(attachments, list):
                continue
            for attachment in attachments:
                count += 1
                if count > MAX_ATTACHMENTS:
                    content.append(
                        TextContent(
                            text="Attachment limit exceeded: only the first five attachments were processed. Ask the sender to resend the remaining files."
                        )
                    )
                    return batch.agent_input.model_copy(update={"content": tuple(content)})
                if not isinstance(attachment, dict):
                    continue
                try:
                    with anyio.fail_after(max(0, deadline - monotonic())):
                        file = await self._download(batch, actor, context, attachment)
                        text = None
                        if file.media_type.startswith("text/") or file.media_type == "application/json":
                            text = file.body.decode("utf-8-sig")
                            if len(text) > 100_000:
                                raise ProviderHttpError("text_attachment_too_large")
                        identity = hashlib.sha256(f"{batch.batch_id}:{count}".encode()).hexdigest()
                        asset = await self.uploads.upload(
                            actor=actor,
                            workspace_id=batch.workspace_id,
                            idempotency_key=f"bot-attachment:{identity}",
                            filename=file.filename,
                            media_type=file.media_type,
                            body=_chunks(file.body),
                            content_length=len(file.body),
                        )
                    # Plain text is readable even by models without document support.
                    if text is not None:
                        content.append(TextContent(text=f"Attached file {file.filename} (asset {asset.id}):\n{text}"))
                    else:
                        content.append(BinaryContent(source=AssetBinarySource(asset_id=asset.id)))
                except (
                    ProviderHttpError,
                    AssetError,
                    AuthorizationError,
                    httpx2.HTTPError,
                    TimeoutError,
                    UnicodeDecodeError,
                ) as error:
                    reason = (
                        error.code
                        if isinstance(error, (ProviderHttpError, AssetError, AuthorizationError))
                        else "attachment_unavailable"
                    )
                    content.append(
                        TextContent(
                            text=f"Attachment {count} could not be read ({reason}). Tell the sender this file was not analyzed; ask them to resend a supported image, PDF, or UTF-8 text file."
                        )
                    )
        return batch.agent_input.model_copy(update={"content": tuple(content)})

    async def _download(
        self, batch: PreparedIngressBatch, actor: AuthenticatedActor, context: JsonObject, attachment: JsonObject
    ) -> FileContent:
        config = batch.configuration
        async with short_session(self.sessions) as session:
            for permission in (WorkspaceAction.asset_create, WorkspaceAction.asset_use):
                await authorize_workspace(session, actor=actor, workspace_id=batch.workspace_id, action=permission)
            account = await session.get(AccountRecord, config.account_id)
            if (
                account is None
                or account.status != "active"
                or account.deleted_at is not None
                or account.version != config.account_version
                or account.provider_key != config.provider_key
                or account.organization_id != batch.organization_id
                or account.workspace_id != batch.workspace_id
                or account.execution_service_account_id != actor.principal.principal_id
            ):
                raise ProviderHttpError("attachment_source_changed")
            configuration = dict(account.provider_config_json)
            credentials = json.loads(account.credential_snapshot().decrypt(self.protector))
        if config.provider_key == "slack":
            identifier = attachment.get("id")
            if not isinstance(identifier, str) or not identifier:
                raise ProviderHttpError("invalid_attachment")
            return await SlackFiles(self.http, self.endpoints, credentials["bot_token"]).download(identifier)
        kind = attachment.get("type")
        key = attachment.get("image_key" if kind == "image" else "file_key")
        message = context.get("message_id")
        if not isinstance(key, str) or not isinstance(message, str) or not isinstance(kind, str):
            raise ProviderHttpError("unsupported_attachment_type")
        return await LarkFiles(
            self.http,
            self.endpoints,
            origin=configuration["open_api_origin"],
            app_id=configuration["app_id"],
            secret=credentials["app_secret"],
        ).download(message_id=message, key=key, kind=kind, filename=attachment.get("file_name"))


async def _chunks(body: bytes) -> AsyncIterator[bytes]:
    yield body
