"""Subscription-authenticated WebRTC bootstrap and server-side Codex Live control."""

from __future__ import annotations

import asyncio
import logging
import re
import ssl
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Literal, get_args
from urllib.parse import urlsplit

from anyio import move_on_after
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import InvalidStatus, WebSocketException

from a13n_harness.configuration import RunConfiguration
from a13n_harness.http import bounded_response_body, outbound_tls_verify
from a13n_harness.models.transport import create_model_http_client

from .codec import MAX_EVENT_BYTES, EventDecoder, context_messages, session_payload
from .types import CodexLiveError, CodexLiveEvent, CodexLiveVoice, ConversationItem

if TYPE_CHECKING:
    from pydantic_ai.providers.openai_codex import OpenAICodexCredentialSource

CALLS_URL = "https://chatgpt.com/backend-api/codex/realtime/calls?intent=quicksilver&architecture=avas"
SIDEBAND_URL = "wss://api.openai.com/v1/live/"
MAX_SDP_BYTES = 128 * 1024
# A dedicated non-propagating logger prevents wire debug logs from exposing headers or speech.
_WIRE_LOGGER = logging.Logger("a13n.codex_live.transport", level=logging.CRITICAL + 1)
_WIRE_LOGGER.addHandler(logging.NullHandler())
_WIRE_LOGGER.propagate = False


class _NoRedirectConnect(connect):
    def process_redirect(self, exc: Exception) -> Exception:
        return exc


def _call_id(location: str) -> str:
    candidate = urlsplit(location).path.rstrip("/").rsplit("/", 1)[-1]
    if not re.fullmatch(
        r"(?:rtc_[A-Za-z0-9_-]{1,160}|[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})", candidate
    ):
        raise CodexLiveError("The Codex Live response has no valid call ID")
    return candidate


class CodexLiveSession:
    """One call's sideband. The caller owns its WebRTC media peer and execution loop."""

    def __init__(self, *, answer_sdp: str, call_id: str, socket: ClientConnection) -> None:
        self.answer_sdp = answer_sdp
        self.call_id = call_id
        self._socket = socket
        self._closed = False
        self._send_lock = asyncio.Lock()
        self._decoder = EventDecoder()

    async def events(self) -> AsyncIterator[CodexLiveEvent]:
        try:
            async for raw in self._socket:
                event = self._decoder.decode(raw)
                if event is not None:
                    yield event
        except (WebSocketException, OSError):
            raise CodexLiveError("Codex Live disconnected; start a new voice session") from None

    async def append_speech(self, text: str, *, delegation_id: str | None = None) -> None:
        """Append selected user-facing speech, optionally correlated with a delegation."""
        await self._append(text, "speakable", delegation_id)

    async def append_status(self, text: str, *, delegation_id: str | None = None) -> None:
        """Append explicit status context. Do not pass private model reasoning."""
        await self._append(text, "commentary", delegation_id)

    async def _append(self, text: str, channel: Literal["speakable", "commentary"], delegation_id: str | None) -> None:
        async with self._send_lock:
            if self._closed:
                raise CodexLiveError("Codex Live is closed")
            try:
                for message in context_messages(text, channel, delegation_id):
                    await self._socket.send(message)
            except (WebSocketException, OSError):
                raise CodexLiveError("Codex Live context delivery failed; delivery is uncertain") from None

    async def close(self) -> None:
        async with self._send_lock:
            if self._closed:
                return
            self._closed = True
            try:
                await self._socket.send('{"type":"session.close"}')
            except (WebSocketException, OSError):
                pass
            finally:
                await self._socket.close()


class CodexLiveModel:
    """Reusable Codex voice-session factory using native subscription credentials.

    The provider owns OAuth refresh and storage. The successful bootstrap request's
    auth pair is handed only to the fixed OpenAI sideband endpoint. Calls and context
    writes are never retried after an ambiguous transport failure.
    """

    def __init__(
        self,
        credential_source: OpenAICodexCredentialSource,
        *,
        voice: CodexLiveVoice = "cove",
        configuration: RunConfiguration | None = None,
    ) -> None:
        if voice not in get_args(CodexLiveVoice.__value__):
            raise CodexLiveError("Unsupported Codex V3 voice; choose cove or another V3 voice")
        self._credential_source = credential_source
        self._voice = voice
        self._configuration = configuration or RunConfiguration()

    @asynccontextmanager
    async def connect(
        self,
        offer_sdp: str,
        *,
        instructions: str,
        on_answer: Callable[[str], Awaitable[None]],
        initial_items: Sequence[ConversationItem] = (),
    ) -> AsyncGenerator[CodexLiveSession]:
        """Create one call, deliver its SDP, then attach controls.

        ``on_answer`` must apply the SDP to the media peer and await its connection.
        The sideband is not routable until media activates the call.
        """
        from pydantic_ai.providers.openai_codex import OpenAICodexProvider

        if not offer_sdp.startswith("v=0") or len(offer_sdp.encode("utf-8")) > MAX_SDP_BYTES:
            raise CodexLiveError("Invalid WebRTC offer")
        session: CodexLiveSession | None = None
        async with create_model_http_client(retry=None, timeout=30, configuration=self._configuration) as client:
            # Installing the native provider installs its HTTPS-only credential lifecycle.
            OpenAICodexProvider(credential_source=self._credential_source, http_client=client)
            try:
                async with client.stream(
                    "POST",
                    CALLS_URL,
                    json={"sdp": offer_sdp, "session": session_payload(instructions, self._voice, initial_items)},
                    headers={"openai-alpha": "quicksilver=v2"},
                    follow_redirects=False,
                ) as response:
                    if not response.is_success:
                        raise CodexLiveError(f"Codex Live call creation failed (HTTP {response.status_code})")
                    answer = (await bounded_response_body(response, max_bytes=MAX_SDP_BYTES)).decode("utf-8")
                    if not answer.startswith("v=0"):
                        raise CodexLiveError("The Codex Live response is not an SDP answer")
                    call_id = _call_id(response.headers.get("location", ""))
                    headers = {
                        "Authorization": response.request.headers["authorization"],
                        "chatgpt-account-id": response.request.headers["chatgpt-account-id"],
                        "openai-alpha": "quicksilver=v2",
                    }
                await on_answer(answer)
                current = await self._credential_source.load()
                if current.account_id != headers["chatgpt-account-id"]:
                    raise CodexLiveError("The Codex account changed while starting Live")
                uri = SIDEBAND_URL + call_id
                self._configuration.authorize_url(uri.replace("wss://", "https://", 1))
                tls = ssl.create_default_context()
                if not outbound_tls_verify():
                    tls.check_hostname = False
                    tls.verify_mode = ssl.CERT_NONE
                socket = await _NoRedirectConnect(
                    uri,
                    additional_headers=headers,
                    ssl=tls,
                    open_timeout=15,
                    close_timeout=5,
                    max_size=MAX_EVENT_BYTES,
                    max_queue=16,
                    logger=_WIRE_LOGGER,
                )
                session = CodexLiveSession(answer_sdp=answer, call_id=call_id, socket=socket)
            except CodexLiveError:
                raise
            except InvalidStatus as exc:
                raise CodexLiveError(
                    f"Codex Live sideband rejected the connection (HTTP {exc.response.status_code})"
                ) from None
            except Exception:
                raise CodexLiveError("Unable to connect Codex Live; check the selected subscription account") from None
            try:
                yield session
            finally:
                with move_on_after(10, shield=True):
                    await session.close()
