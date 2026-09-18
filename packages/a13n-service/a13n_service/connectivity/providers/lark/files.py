"""Message-scoped Feishu resources and native file delivery."""

import json
from collections.abc import Awaitable, Callable
from urllib.parse import quote

import httpx2

from a13n_service.connectivity.domain import JsonObject
from a13n_service.connectivity.file_content import FileContent, media_type, read_file, safe_filename
from a13n_service.connectivity.http import ConnectivityHttpError, EndpointValidator
from a13n_service.ids import new_object_id

from .api import read_lark_response
from .token import LarkTenantTokenProvider


class LarkFiles:
    def __init__(
        self, http: httpx2.AsyncClient, endpoints: EndpointValidator, *, origin: str, app_id: str, secret: str
    ) -> None:
        self.http = http
        self.endpoints = endpoints
        self.origin = origin
        self.tokens = LarkTenantTokenProvider(http, endpoints, open_api_origin=origin, app_id=app_id, app_secret=secret)

    async def _source(self) -> tuple[str, dict[str, str]]:
        try:
            origin = await self.endpoints.validate(self.origin, resolve_dns=True)
        except ValueError as error:
            raise ConnectivityHttpError("endpoint_denied") from error
        return origin, {"Authorization": f"Bearer {await self.tokens.token()}"}

    async def download(self, *, message_id: str, key: str, kind: str, filename: object) -> FileContent:
        if kind not in {"image", "file"}:
            raise ConnectivityHttpError("unsupported_attachment_type")
        name = safe_filename(filename, "image.png" if kind == "image" else "attachment")
        origin, headers = await self._source()
        path = f"/open-apis/im/v1/messages/{quote(message_id, safe='')}/resources/{quote(key, safe='')}"
        async with self.http.stream(
            "GET", origin + path, params={"type": kind}, headers=headers, follow_redirects=False
        ) as response:
            body = await read_file(response)
            mime = media_type(name, response.headers.get("content-type"))
        return FileContent(name, mime, body)

    async def send(
        self,
        file: FileContent,
        *,
        chat_id: str,
        message_id: str,
        reply_in_thread: bool,
        guard: Callable[[], Awaitable[None]] | None = None,
    ) -> str:
        origin, headers = await self._source()
        async with self.http.stream(
            "POST",
            origin + "/open-apis/im/v1/files",
            headers=headers,
            data={"file_type": "pdf" if file.media_type == "application/pdf" else "stream", "file_name": file.filename},
            files={"file": (file.filename, file.body, file.media_type)},
            follow_redirects=False,
        ) as response:
            result = await read_lark_response(response, max_bytes=64 * 1024)
        data = result.get("data")
        key = data.get("file_key") if isinstance(data, dict) else None
        if not isinstance(key, str) or not 0 < len(key) <= 256:
            raise ConnectivityHttpError("invalid_provider_response")
        payload: JsonObject = {
            "msg_type": "file",
            "content": json.dumps({"file_key": key}),
            "uuid": new_object_id("file"),
        }
        params = None
        if reply_in_thread:
            path = f"/open-apis/im/v1/messages/{quote(message_id, safe='')}/reply"
            payload["reply_in_thread"] = True
        else:
            path = "/open-apis/im/v1/messages"
            payload["receive_id"] = chat_id
            params = {"receive_id_type": "chat_id"}
        if guard is not None:
            await guard()
        async with self.http.stream(
            "POST", origin + path, headers=headers, json=payload, params=params, follow_redirects=False
        ) as response:
            result = await read_lark_response(response, max_bytes=64 * 1024)
        data = result.get("data")
        sent = data.get("message_id") if isinstance(data, dict) else None
        if not isinstance(sent, str) or not sent:
            raise ConnectivityHttpError("invalid_provider_response")
        return sent
