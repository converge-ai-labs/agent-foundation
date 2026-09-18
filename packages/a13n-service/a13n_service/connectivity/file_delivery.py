"""Deliver only current-run published Assets to the accepted inbound conversation."""

from collections.abc import Awaitable, Callable
from typing import Literal

import anyio
import httpx2
from pydantic import BaseModel, ConfigDict

from a13n_service.assets.catalog import AssetCatalog
from a13n_service.assets.domain import RunOutputAssetSource
from a13n_service.connectivity.domain import JsonObject
from a13n_service.connectivity.file_content import MAX_FILE_BYTES, FileContent
from a13n_service.connectivity.http import ConnectivityHttpError, EndpointValidator
from a13n_service.connectivity.native_context import InboundRunContext
from a13n_service.connectivity.providers.lark.files import LarkFiles
from a13n_service.connectivity.providers.slack.files import SlackFiles
from a13n_service.iam import AuthenticatedActor
from a13n_service.ids import ObjectId


class SendFileArguments(BaseModel):
    """Send a file published by service.publish_asset in this run to the current conversation.

    Supply its asset_id, not a URL, local path, platform file key, or channel.
    Maximum file size is 20 MiB. Do not retry an outcome_unknown result: delivery may have succeeded.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    asset_id: ObjectId


class FileDeliveryOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["succeeded", "outcome_unknown"]
    asset_id: ObjectId


class FileDelivery:
    def __init__(self, assets: AssetCatalog) -> None:
        self.assets = assets

    async def send(
        self,
        arguments: SendFileArguments,
        *,
        actor: AuthenticatedActor,
        run_id: str,
        context: InboundRunContext,
        configuration: JsonObject,
        credentials: JsonObject,
        http: httpx2.AsyncClient,
        endpoints: EndpointValidator,
        guard: Callable[[], Awaitable[None]],
    ) -> FileDeliveryOutcome:
        asset = await self.assets.get(actor=actor, asset_id=arguments.asset_id)
        if (
            asset.workspace_id != actor.workspace_id
            or not isinstance(asset.source, RunOutputAssetSource)
            or asset.source.run_id != run_id
        ):
            raise ConnectivityHttpError("file_not_from_current_run")
        if asset.size_bytes > MAX_FILE_BYTES:
            raise ConnectivityHttpError("file_too_large")
        prepared = await self.assets.prepare_content(actor=actor, asset_id=asset.id)
        try:
            body = bytearray()
            async for chunk in prepared.content.chunks():
                body.extend(chunk)
                if len(body) > MAX_FILE_BYTES:
                    raise ConnectivityHttpError("file_too_large")
            file = FileContent(asset.filename, asset.media_type, bytes(body))
            await guard()
            # Recheck deletion and permissions after reading the bounded file.
            await self.assets.get(actor=actor, asset_id=asset.id)
            source = context.provider_context
            mode = context.action_policy.get("reply_mode", "thread")
            try:
                with anyio.fail_after(60):
                    if context.provider_key == "slack":
                        threaded = mode == "thread" or (mode == "auto" and source.get("conversation_kind") != "im")
                        await SlackFiles(http, endpoints, str(credentials["bot_token"])).send(
                            file,
                            channel_id=str(source["channel_id"]),
                            thread_ts=str(source["root_thread_ts"]) if threaded else None,
                            guard=guard,
                        )
                    else:
                        await LarkFiles(
                            http,
                            endpoints,
                            origin=str(configuration["open_api_origin"]),
                            app_id=str(configuration["app_id"]),
                            secret=str(credentials["app_secret"]),
                        ).send(
                            file,
                            chat_id=str(source["chat_id"]),
                            message_id=str(source["message_id"]),
                            reply_in_thread=source.get("chat_type") != "p2p" and mode != "main",
                            guard=guard,
                        )
            except (httpx2.HTTPError, TimeoutError):
                return FileDeliveryOutcome(kind="outcome_unknown", asset_id=asset.id)
            except ConnectivityHttpError as error:
                if error.code in {"provider_unavailable", "invalid_provider_response", "response_too_large"}:
                    return FileDeliveryOutcome(kind="outcome_unknown", asset_id=asset.id)
                raise
            return FileDeliveryOutcome(kind="succeeded", asset_id=asset.id)
        finally:
            await prepared.content.remove()
