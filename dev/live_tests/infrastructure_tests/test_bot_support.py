"""The Bot fixture forwards actual native requests only to its owned TLS peer."""

import asyncio
import json
import ssl
from contextlib import suppress

import httpx2
import pytest
from a13n_service.connectivity.providers.slack.client import SlackNativeClient

from ..bots.host import BotPeerTransport
from ..infrastructure.fixture_peer import create_certificate

pytestmark = pytest.mark.anyio


async def test_slack_transport_preserves_native_get_and_uses_local_host(tmp_path):
    config = create_certificate(tmp_path)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(config["peer_certificate"], config["peer_key"])
    requests = []
    payload = json.dumps(
        {
            "ok": True,
            "channel": {
                "id": "C_LIVE",
                "name": "Engineering",
                "is_member": True,
                "is_private": True,
                "is_archived": False,
                "is_im": False,
                "is_mpim": False,
                "is_ext_shared": False,
            },
        }
    ).encode()

    async def respond(reader, writer):
        try:
            requests.append(await reader.readuntil(b"\r\n\r\n"))
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Length: "
                + str(len(payload)).encode()
                + b"\r\nConnection: close\r\n\r\n"
                + payload
            )
            await writer.drain()
        finally:
            writer.close()
            with suppress(ConnectionResetError):
                await writer.wait_closed()

    async with await asyncio.start_server(respond, "127.0.0.1", 0, ssl=context) as server:
        port = server.sockets[0].getsockname()[1]
        config["peer_url"] = f"https://127.0.0.1:{port}"
        async with httpx2.AsyncClient(transport=BotPeerTransport(config), trust_env=False) as http:
            result = await SlackNativeClient(http).inspect_conversation("C_LIVE", bot_token="fictional-token")
            assert result.id == "C_LIVE" and result.is_member and result.audience == "private"
            with pytest.raises(AssertionError, match="unexpected upstream"):
                await http.get("https://example.com/api/conversations.info")
    assert len(requests) == 1
    assert requests[0].startswith(b"GET /api/conversations.info?channel=C_LIVE HTTP/1.1\r\n")
    assert f"host: 127.0.0.1:{port}".encode() in requests[0].lower()
    assert b"authorization: Bearer fictional-token" in requests[0]


async def test_bot_model_reads_case_from_actual_structured_input():
    from ..bots.model import event_cases

    case = {"case_id": "a" * 32, "scenario": "bot_memory", "token": "b" * 32}
    text = "btest_" + "c" * 32 + " LIVE_TEST " + json.dumps(case)
    envelope = json.dumps({"events": [{"text": text, "type": "slack.app_mention"}]})
    assert [json.loads(value) for value in event_cases([envelope])] == [case]
    assert event_cases([json.dumps({"events": [{"text": "no marker"}]})]) == []
