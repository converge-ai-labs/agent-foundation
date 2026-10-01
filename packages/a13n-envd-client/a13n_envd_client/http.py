from __future__ import annotations

import asyncio
import ipaddress
import json
import ssl
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

import httpx2

from a13n_envd_client._timeouts import response_timeout
from a13n_envd_client._transfer_window import TransferWindow
from a13n_envd_client.eip.v1 import DataFrame, DataFrameKind
from a13n_envd_client.errors import (
    EIPConnectionError,
    EIPProtocolError,
    EIPTransferTransportError,
    EIPTransportClosedError,
    EIPTransportError,
)
from a13n_envd_client.transport import ControlFrame, EIPTransportFrame, TransferDirection

_CONTROL_PATH = "/eip/control"
_TRANSFER_PATH = "/eip/transfer"
_SESSION_HEADER = "EIP-Session"
_HANDLE_HEADER = "EIP-Transfer-Handle"
_DIRECTION_HEADER = "EIP-Transfer-Direction"
_CLOSE_GRACE_SECONDS = 1.0


@dataclass(slots=True)
class _TransferState:
    direction: TransferDirection
    window: TransferWindow = field(default_factory=TransferWindow)
    upload: asyncio.Queue[bytes | None] | None = None
    task: asyncio.Task[None] | None = None
    offset: int = 0
    failure: EIPTransferTransportError | None = None


class HttpTransport:
    """Authenticated EIP HTTP control and raw-transfer transport."""

    def __init__(
        self,
        endpoint: str,
        credential: str,
        *,
        verify: ssl.SSLContext | str | bool = True,
        request_timeout: float = 30.0,
        allow_plaintext_private_link: bool = False,
        max_request_bytes: int = 1024 * 1024,
        max_response_bytes: int = 1024 * 1024,
        max_transfer_frame_bytes: int = 1024 * 1024,
    ) -> None:
        if not credential or any(character.isspace() or ord(character) < 32 for character in credential):
            raise ValueError("HTTP attachment credential must be non-empty and contain no whitespace")
        if request_timeout <= 0:
            raise ValueError("request_timeout must be positive")
        _validate_limit("max_request_bytes", max_request_bytes)
        _validate_limit("max_response_bytes", max_response_bytes)
        _validate_limit("max_transfer_frame_bytes", max_transfer_frame_bytes)
        self._endpoint = normalize_http_endpoint(
            endpoint,
            allow_plaintext_private_link=allow_plaintext_private_link,
        )
        self._credential = credential
        self._request_timeout = request_timeout
        self._client = httpx2.AsyncClient(
            verify=httpx2.create_ssl_context(verify=verify, trust_env=False),
            timeout=request_timeout,
            follow_redirects=False,
            # Plaintext attachment relies on a local/provider-private link.
            # A process proxy must not move its bearer credential off that link.
            trust_env=urlsplit(self._endpoint).scheme == "https",
        )
        self._max_request_bytes = max_request_bytes
        self._max_response_bytes = max_response_bytes
        self._max_transfer_frame_bytes = max_transfer_frame_bytes
        self._received: asyncio.Queue[EIPTransportFrame | BaseException] = asyncio.Queue(128)
        self._transfers: dict[tuple[str, str], _TransferState] = {}
        self._close_task: asyncio.Task[None] | None = None
        self._closed = False

    def set_limits(
        self,
        *,
        max_request_bytes: int,
        max_response_bytes: int,
        max_transfer_frame_bytes: int,
    ) -> None:
        _validate_limit("max_request_bytes", max_request_bytes)
        _validate_limit("max_response_bytes", max_response_bytes)
        _validate_limit("max_transfer_frame_bytes", max_transfer_frame_bytes)
        self._max_request_bytes = min(self._max_request_bytes, max_request_bytes)
        self._max_response_bytes = min(self._max_response_bytes, max_response_bytes)
        self._max_transfer_frame_bytes = min(self._max_transfer_frame_bytes, max_transfer_frame_bytes)

    def register_transfer(self, session_id: str, handle: str, direction: TransferDirection) -> None:
        key = (session_id, handle)
        if key in self._transfers:
            raise EIPProtocolError("HTTP transfer handle is already registered")
        self._transfers[key] = _TransferState(direction=direction)

    def unregister_transfer(self, session_id: str, handle: str) -> None:
        state = self._transfers.pop((session_id, handle), None)
        if state is not None and state.task is not None and not state.task.done():
            state.task.cancel()

    async def send(self, frame: EIPTransportFrame) -> None:
        if self._closed:
            raise EIPTransportClosedError("HTTP transport is closed")
        if isinstance(frame, ControlFrame):
            await self._send_control(frame)
            return
        if not isinstance(frame, DataFrame):
            raise TypeError("unsupported EIP transport frame")
        await self._send_transfer_frame(frame)

    async def receive(self) -> EIPTransportFrame:
        if self._closed and self._received.empty():
            raise EIPTransportClosedError("HTTP transport is closed")
        item = await self._received.get()
        if isinstance(item, BaseException):
            raise item
        return item

    async def close(self) -> None:
        if self._close_task is None:
            self._closed = True
            self._close_task = asyncio.create_task(self._finish_close(), name="eip-http-close")
        await asyncio.shield(self._close_task)

    async def _send_control(self, frame: ControlFrame) -> None:
        if len(frame.payload) > self._max_request_bytes:
            raise EIPTransportError("EIP control request exceeds its negotiated byte limit")
        headers = self._headers("application/json")
        try:
            params = json.loads(frame.payload).get("params", {})
            async with self._client.stream(
                "POST",
                f"{self._endpoint}{_CONTROL_PATH}",
                headers=headers,
                content=frame.payload,
                timeout=httpx2.Timeout(
                    self._request_timeout,
                    read=response_timeout(params, self._request_timeout),
                ),
            ) as response:
                if response.status_code != 200:
                    raise EIPTransportError(f"EIP HTTP control request failed with status {response.status_code}")
                _validate_response_headers(response, "application/json")
                payload = await _read_response_bounded(response, self._max_response_bytes)
        except EIPTransportError:
            raise
        except (httpx2.NetworkError, httpx2.ConnectTimeout, httpx2.RemoteProtocolError) as error:
            raise EIPConnectionError("EIP HTTP connection failed") from error
        except httpx2.HTTPError as error:
            raise EIPTransportError("EIP HTTP control request failed") from error
        await self._received.put(ControlFrame(payload))

    async def _send_transfer_frame(self, frame: DataFrame) -> None:
        state = self._transfers.get((frame.session_id, frame.handle))
        if state is None:
            raise EIPProtocolError("HTTP transfer frame has no registered handle")
        if frame.kind is DataFrameKind.ATTACH:
            if state.task is not None:
                raise EIPProtocolError("HTTP transfer attached more than once")
            if state.direction == "read":
                state.task = asyncio.create_task(
                    self._download(frame.session_id, frame.handle, state), name="eip-http-download"
                )
            else:
                state.upload = asyncio.Queue(8)
                state.task = asyncio.create_task(
                    self._upload(frame.session_id, frame.handle, state), name="eip-http-upload"
                )
                await self._received.put(
                    DataFrame(kind=DataFrameKind.ATTACHED, session_id=frame.session_id, handle=frame.handle)
                )
            return
        if frame.kind is DataFrameKind.RESET:
            if state.task is not None:
                state.task.cancel()
                await asyncio.gather(state.task, return_exceptions=True)
            # Closing a body is insufficient when the peer already produced END
            # into network buffers. Explicit RESET also retires that reader.
            try:
                async with self._client.stream(
                    "DELETE",
                    f"{self._endpoint}{_TRANSFER_PATH}",
                    headers=self._transfer_headers(frame.session_id, frame.handle, state.direction),
                    content=b"",
                ) as response:
                    if response.status_code != 204:
                        raise EIPTransferTransportError(
                            f"EIP HTTP transfer reset failed with status {response.status_code}",
                            session_id=frame.session_id,
                            handle=frame.handle,
                        )
            except httpx2.HTTPError as error:
                raise EIPTransferTransportError(
                    "EIP HTTP transfer reset failed", session_id=frame.session_id, handle=frame.handle
                ) from error
            self._transfers.pop((frame.session_id, frame.handle), None)
            return
        if state.direction == "read" and frame.kind is DataFrameKind.CREDIT:
            state.window.credit(frame.offset)
            return
        if state.direction != "write" or state.upload is None:
            raise EIPProtocolError("HTTP reader accepts only ATTACH, CREDIT or RESET")
        if frame.kind is DataFrameKind.CHUNK:
            if frame.offset != state.offset:
                raise EIPProtocolError("HTTP writer offset is not contiguous")
            state.offset += len(frame.payload)
            await _put_upload(state, frame.payload)
            return
        if frame.kind is DataFrameKind.END:
            if frame.offset != state.offset:
                raise EIPProtocolError("HTTP writer END offset is not contiguous")
            await _put_upload(state, None)
            return
        raise EIPProtocolError("unsupported HTTP writer frame")

    async def _download(self, session_id: str, handle: str, state: _TransferState) -> None:
        try:
            async with self._client.stream(
                "POST",
                f"{self._endpoint}{_TRANSFER_PATH}",
                headers=self._transfer_headers(session_id, handle, "read"),
                content=b"",
            ) as response:
                if response.status_code != 200:
                    raise EIPTransferTransportError(
                        f"EIP HTTP reader failed with status {response.status_code}",
                        session_id=session_id,
                        handle=handle,
                    )
                _validate_response_headers(response, "application/octet-stream")
                await self._received.put(DataFrame(kind=DataFrameKind.ATTACHED, session_id=session_id, handle=handle))
                async for chunk in response.aiter_bytes(self._max_transfer_frame_bytes):
                    if not chunk:
                        continue
                    async with asyncio.timeout(self._request_timeout):
                        await state.window.wait()
                    offset = state.window.sent(len(chunk))
                    await self._received.put(
                        DataFrame(
                            kind=DataFrameKind.CHUNK,
                            session_id=session_id,
                            handle=handle,
                            offset=offset,
                            payload=chunk,
                        )
                    )
                    state.offset += len(chunk)
                async with asyncio.timeout(self._request_timeout):
                    await state.window.wait(drained=True)
                await self._received.put(
                    DataFrame(kind=DataFrameKind.END, session_id=session_id, handle=handle, offset=state.offset)
                )
        except asyncio.CancelledError:
            raise
        except EIPTransferTransportError as error:
            state.failure = error
            await self._received.put(error)
        except (httpx2.HTTPError, TimeoutError):
            # One HTTP exchange is not the logical EIP session. Control requests
            # can still abort/close the resource after transfer connection loss.
            state.failure = EIPTransferTransportError(
                "EIP HTTP transfer exchange failed", session_id=session_id, handle=handle
            )
            await self._received.put(state.failure)
        except BaseException as error:
            await self._fail_background(error)

    async def _upload(self, session_id: str, handle: str, state: _TransferState) -> None:
        upload = state.upload
        assert upload is not None

        async def content():
            consumed = 0
            while True:
                chunk = await upload.get()
                if chunk is None:
                    break
                yield chunk
                consumed += len(chunk)
                await self._received.put(
                    DataFrame(kind=DataFrameKind.CREDIT, session_id=session_id, handle=handle, offset=consumed)
                )

        try:
            response = await self._client.post(
                f"{self._endpoint}{_TRANSFER_PATH}",
                headers=self._transfer_headers(session_id, handle, "write"),
                content=content(),
            )
            if response.status_code != 204:
                raise EIPTransferTransportError(
                    f"EIP HTTP writer failed with status {response.status_code}", session_id=session_id, handle=handle
                )
            _validate_response_headers(response, "application/octet-stream")
            await self._received.put(
                DataFrame(kind=DataFrameKind.END_ACK, session_id=session_id, handle=handle, offset=state.offset)
            )
        except asyncio.CancelledError:
            raise
        except EIPTransferTransportError as error:
            state.failure = error
            await self._received.put(error)
        except (httpx2.HTTPError, TimeoutError):
            # One HTTP exchange is not the logical EIP session. Control requests
            # can still abort/close the resource after transfer connection loss.
            state.failure = EIPTransferTransportError(
                "EIP HTTP transfer exchange failed", session_id=session_id, handle=handle
            )
            await self._received.put(state.failure)
        except BaseException as error:
            await self._fail_background(error)

    async def _fail_background(self, error: BaseException) -> None:
        if not isinstance(error, (EIPProtocolError, EIPTransportError)):
            error = EIPTransportError("EIP HTTP transfer failed")
        await self._received.put(error)

    async def _finish_close(self) -> None:
        tasks = [state.task for state in self._transfers.values() if state.task is not None]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._transfers.clear()
        try:
            async with asyncio.timeout(_CLOSE_GRACE_SECONDS):
                await self._client.aclose()
        except TimeoutError:
            pass
        self._credential = ""

    def _headers(self, content_type: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._credential}",
            "Content-Type": content_type,
            "Accept-Encoding": "identity",
        }

    def _transfer_headers(self, session_id: str, handle: str, direction: Literal["read", "write"]) -> dict[str, str]:
        headers = self._headers("application/octet-stream")
        headers[_SESSION_HEADER] = _validate_header_value(session_id, "Session selector")
        headers[_HANDLE_HEADER] = _validate_header_value(handle, "transfer handle")
        headers[_DIRECTION_HEADER] = direction
        return headers


async def _put_upload(state: _TransferState, item: bytes | None) -> None:
    upload = state.upload
    task = state.task
    if upload is None or task is None:
        raise EIPTransportClosedError("HTTP writer transfer is not active")
    put = asyncio.create_task(upload.put(item))
    try:
        done, _ = await asyncio.wait({put, task}, return_when=asyncio.FIRST_COMPLETED)
        if task in done and state.failure is not None:
            raise state.failure
        if task in done and put not in done:
            raise EIPTransportClosedError("HTTP writer transfer ended before accepting the body")
        await put
    finally:
        if not put.done():
            put.cancel()
            await asyncio.gather(put, return_exceptions=True)


async def _read_response_bounded(response: httpx2.Response, maximum: int) -> bytes:
    output = bytearray()
    async for chunk in response.aiter_bytes():
        if len(output) + len(chunk) > maximum:
            raise EIPProtocolError("EIP HTTP response exceeds its negotiated byte limit")
        output.extend(chunk)
    return bytes(output)


def normalize_http_endpoint(endpoint: str, *, allow_plaintext_private_link: bool = False) -> str:
    """Validate an EIP HTTP origin without creating a client or performing I/O."""
    if (
        not endpoint
        or len(endpoint) > 2048
        or any(character.isspace() or ord(character) < 32 for character in endpoint)
    ):
        raise ValueError("HTTP endpoint must be bounded and contain no whitespace")
    parsed = urlsplit(endpoint)
    if parsed.port == 0:
        raise ValueError("HTTP endpoint port must be positive")
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("HTTP endpoint must use http or https and include a host")
    if parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
        raise ValueError("HTTP endpoint cannot contain user info, query, or fragment")
    if parsed.path not in {"", "/"}:
        raise ValueError("HTTP endpoint must not contain a path")
    if parsed.scheme == "http" and not _is_loopback_host(parsed.hostname) and not allow_plaintext_private_link:
        raise ValueError("plaintext HTTP requires loopback or an explicit provider-private-link opt-in")
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", "")).rstrip("/")


def _validate_response_headers(response: httpx2.Response, expected_content_type: str) -> None:
    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
    if content_type != expected_content_type:
        raise EIPProtocolError("EIP HTTP response has an unexpected media type")
    if "Content-Encoding" in response.headers:
        raise EIPProtocolError("EIP HTTP response content encoding is forbidden")


def _is_loopback_host(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _validate_header_value(value: str, name: str) -> str:
    if not value or len(value) > 1024 or any(ord(character) < 33 or ord(character) > 126 for character in value):
        raise EIPProtocolError(f"invalid {name}")
    return value


def _validate_limit(name: str, value: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
