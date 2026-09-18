"""Bounded asynchronous Slack Socket Mode and Feishu long-connection clients.

Feishu framing follows larksuite/oapi-sdk-python's ws client (v2_main).
Platform envelope acknowledgements happen only after the admission callback returns.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from collections.abc import Awaitable, Callable
from urllib.parse import parse_qs, urlsplit

import httpx2
from pydantic import TypeAdapter
from websockets.asyncio.client import connect

from a13n_service.connectivity.domain import JsonObject
from a13n_service.connectivity.providers.lark.frame_pb2 import Frame, Header
from a13n_service.endpoint_policy import EndpointPolicy

EventHandler = Callable[[JsonObject], Awaitable[JsonObject | None]]
ConnectedHandler = Callable[[], Awaitable[None]]
_OBJECT = TypeAdapter(JsonObject)
MAX_BYTES = 1024 * 1024
# The handshake URL contains credentials. Library wire logging must never expose it.
_SOCKET_LOGGER = logging.Logger("a13n_service.connectivity.socket_wire", level=logging.CRITICAL + 1)


class FixedEndpointConnect(connect):
    def process_redirect(self, exc: Exception) -> Exception:
        return TransportError("handshake_failed")


async def _validate_endpoint(url: str) -> None:
    parsed = urlsplit(url)
    await EndpointPolicy(require_https=True).validate("https://" + parsed.netloc + "/")


class TransportError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def socket_url(value: object, provider: str) -> str:
    if not isinstance(value, str) or len(value) > 8192:
        raise TransportError("invalid_endpoint")
    parsed = urlsplit(value)
    domains = ("slack.com",) if provider == "slack" else ("feishu.cn", "larksuite.com", "larkoffice.com")
    host = parsed.hostname or ""
    if (
        parsed.scheme != "wss"
        or parsed.fragment
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
        or not any(host == domain or host.endswith("." + domain) for domain in domains)
    ):
        raise TransportError("invalid_endpoint")
    return value


async def _post(
    client: httpx2.AsyncClient, url: str, *, body: JsonObject | None = None, bearer: str | None = None
) -> JsonObject:
    headers = {"Authorization": f"Bearer {bearer}"} if bearer else {}
    await _validate_endpoint(url)
    async with client.stream("POST", url, json=body, headers=headers) as response:
        if response.status_code != 200:
            raise TransportError("discovery_failed")
        data = bytearray()
        async for chunk in response.aiter_bytes():
            data.extend(chunk)
            if len(data) > MAX_BYTES:
                raise TransportError("invalid_endpoint")
    return _OBJECT.validate_json(data)


async def slack_connection(
    client: httpx2.AsyncClient, *, token: str, app_id: str, admit: EventHandler, connected: ConnectedHandler
) -> None:
    result = await _post(client, "https://slack.com/api/apps.connections.open", bearer=token)
    if result.get("ok") is not True:
        raise TransportError("credentials_or_permissions_invalid")
    url = socket_url(result.get("url"), "slack")
    await _validate_endpoint(url)
    async with FixedEndpointConnect(
        url, max_size=MAX_BYTES, max_queue=16, open_timeout=15, close_timeout=3, proxy=True, logger=_SOCKET_LOGGER
    ) as socket:
        hello = _OBJECT.validate_json(await asyncio.wait_for(socket.recv(), timeout=15))
        info = hello.get("connection_info")
        if hello.get("type") != "hello" or not isinstance(info, dict) or info.get("app_id") != app_id:
            raise TransportError("app_identity_mismatch")
        await connected()
        async for raw in socket:
            envelope = _OBJECT.validate_json(raw)
            kind = envelope.get("type")
            if kind == "disconnect":
                return
            identifier = envelope.get("envelope_id")
            if not isinstance(identifier, str) or not identifier:
                continue
            if kind in {"events_api", "interactive"}:
                payload = _OBJECT.validate_python(envelope.get("payload"))
                async with asyncio.timeout(2.5):
                    await admit(payload)
            await socket.send(json.dumps({"envelope_id": identifier}))


class Fragments:
    """Bound fragmented events by count, bytes and monotonic expiry."""

    def __init__(self) -> None:
        self.pending: dict[str, tuple[float, int, dict[int, bytes]]] = {}

    def assemble(self, frame: Frame) -> bytes | None:
        now = time.monotonic()
        self.pending = {key: value for key, value in self.pending.items() if value[0] > now}
        headers = {header.key: header.value for header in frame.headers}
        total, sequence = int(headers.get("sum", "1")), int(headers.get("seq", "0"))
        if not 1 <= total <= 128 or not 0 <= sequence < total:
            raise TransportError("invalid_frame")
        if total == 1:
            return frame.payload
        identifier = headers.get("message_id")
        if not identifier:
            raise TransportError("invalid_frame")
        if identifier not in self.pending and len(self.pending) >= 32:
            raise TransportError("fragment_capacity")
        _expires, expected, parts = self.pending.setdefault(identifier, (now + 5, total, {}))
        if expected != total or (sequence in parts and parts[sequence] != frame.payload):
            raise TransportError("invalid_frame")
        parts[sequence] = frame.payload
        if sum(len(part) for _, _, group in self.pending.values() for part in group.values()) > MAX_BYTES:
            raise TransportError("fragment_capacity")
        if len(parts) != total:
            return None
        del self.pending[identifier]
        return b"".join(parts[index] for index in range(total))


async def lark_connection(
    client: httpx2.AsyncClient,
    *,
    origin: str,
    app_id: str,
    secret: str,
    admit: EventHandler,
    connected: ConnectedHandler,
) -> None:
    if origin not in {"https://open.feishu.cn", "https://open.larksuite.com"}:
        raise TransportError("unsupported_transport_origin")
    result = await _post(client, origin + "/callback/ws/endpoint", body={"AppID": app_id, "AppSecret": secret})
    if result.get("code") != 0:
        raise TransportError("credentials_or_permissions_invalid")
    data = _OBJECT.validate_python(result.get("data"))
    url = socket_url(data.get("URL"), "lark")
    service = int(parse_qs(urlsplit(url).query)["service_id"][0])
    configuration = data.get("ClientConfig")
    interval = _ping_interval(configuration)
    fragments = Fragments()
    await _validate_endpoint(url)
    async with FixedEndpointConnect(
        url, max_size=MAX_BYTES, max_queue=16, open_timeout=15, close_timeout=3, proxy=True, logger=_SOCKET_LOGGER
    ) as socket:
        await connected()
        deadline = time.monotonic()
        while True:
            if time.monotonic() >= deadline:
                ping = Frame(SeqID=0, LogID=0, service=service, method=0, headers=[Header(key="type", value="ping")])
                await socket.send(ping.SerializeToString())
                deadline = time.monotonic() + interval
            try:
                raw = await asyncio.wait_for(socket.recv(), timeout=max(0.01, deadline - time.monotonic()))
            except TimeoutError:
                continue
            if not isinstance(raw, bytes):
                raise TransportError("invalid_frame")
            frame = Frame.FromString(raw)
            if not frame.IsInitialized():
                raise TransportError("invalid_frame")
            headers = {header.key: header.value for header in frame.headers}
            if frame.method == 0:
                if headers.get("type") == "pong" and frame.payload:
                    interval = _ping_interval(_OBJECT.validate_json(frame.payload))
                continue
            if frame.method != 1 or headers.get("type") != "event":
                continue
            payload = fragments.assemble(frame)
            if payload is None:
                continue
            async with asyncio.timeout(2.5):
                result = await admit(_OBJECT.validate_json(payload))
            response: JsonObject = {"code": 200}
            if result is not None:
                response["data"] = base64.b64encode(json.dumps(result).encode()).decode()
            frame.payload = json.dumps(response).encode()
            await socket.send(frame.SerializeToString())


def _ping_interval(value: object) -> float:
    if isinstance(value, dict):
        interval = value.get("PingInterval")
        if isinstance(interval, (int, float)) and not isinstance(interval, bool) and 1 <= interval <= 300:
            return float(interval)
    return 30
