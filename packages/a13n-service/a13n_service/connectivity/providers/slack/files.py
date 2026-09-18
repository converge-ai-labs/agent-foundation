"""Authenticated Slack file acquisition and external-upload completion."""

from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

import httpx2

from a13n_service.connectivity.domain import JsonObject
from a13n_service.connectivity.file_content import MAX_FILE_BYTES, FileContent, media_type, read_file, safe_filename
from a13n_service.connectivity.http import ConnectivityHttpError, EndpointValidator, bounded_response_body

from .client import SlackNativeClient


class SlackFiles:
    def __init__(self, http: httpx2.AsyncClient, endpoints: EndpointValidator, token: str) -> None:
        self.http = http
        self.endpoints = endpoints
        self.token = token
        self.api = SlackNativeClient(http)

    async def _url(self, value: object) -> str:
        if not isinstance(value, str) or len(value) > 8192:
            raise ConnectivityHttpError("invalid_provider_response")
        try:
            url = urlsplit(value)
            port = url.port
        except ValueError as error:
            raise ConnectivityHttpError("endpoint_denied") from error
        if (
            url.scheme != "https"
            or url.hostname != "files.slack.com"
            or port not in {None, 443}
            or url.username
            or url.password
            or url.fragment
        ):
            raise ConnectivityHttpError("endpoint_denied")
        try:
            await self.endpoints.validate(value, resolve_dns=True)
        except ValueError as error:
            raise ConnectivityHttpError("endpoint_denied") from error
        return value

    async def download(self, identifier: str) -> FileContent:
        result = await self.api.file_request("files.info", {"file": identifier}, bot_token=self.token)
        file = result.get("file")
        if not isinstance(file, dict) or file.get("id") != identifier or file.get("is_external") is True:
            raise ConnectivityHttpError("invalid_provider_response")
        size = file.get("size")
        if isinstance(size, int) and size > MAX_FILE_BYTES:
            raise ConnectivityHttpError("response_too_large")
        filename = safe_filename(file.get("name"), "attachment")
        mime = media_type(filename, file.get("mimetype"))
        url = await self._url(file.get("url_private_download") or file.get("url_private"))
        async with self.http.stream(
            "GET", url, headers={"Authorization": f"Bearer {self.token}"}, follow_redirects=False
        ) as response:
            body = await read_file(response)
        return FileContent(filename, mime, body)

    async def send(
        self,
        file: FileContent,
        *,
        channel_id: str,
        thread_ts: str | None,
        guard: Callable[[], Awaitable[None]] | None = None,
    ) -> str:
        ticket = await self.api.file_request(
            "files.getUploadURLExternal", {"filename": file.filename, "length": len(file.body)}, bot_token=self.token
        )
        identifier = ticket.get("file_id")
        if not isinstance(identifier, str) or not 0 < len(identifier) <= 256:
            raise ConnectivityHttpError("invalid_provider_response")
        url = await self._url(ticket.get("upload_url"))
        # The upload URL is itself the capability; never send the bot token here.
        async with self.http.stream(
            "POST", url, content=file.body, headers={"Content-Type": "application/octet-stream"}, follow_redirects=False
        ) as response:
            await bounded_response_body(response, max_bytes=64 * 1024)
            if response.status_code != 200:
                raise ConnectivityHttpError("provider_rejected")
        payload: JsonObject = {"files": [{"id": identifier, "title": file.filename}], "channel_id": channel_id}
        if thread_ts is not None:
            payload["thread_ts"] = thread_ts
        if guard is not None:
            await guard()
        completed = await self.api.file_request("files.completeUploadExternal", payload, bot_token=self.token)
        entries = completed.get("files")
        if not isinstance(entries, list) or not any(
            isinstance(entry, dict) and entry.get("id") == identifier for entry in entries
        ):
            raise ConnectivityHttpError("invalid_provider_response")
        return identifier
