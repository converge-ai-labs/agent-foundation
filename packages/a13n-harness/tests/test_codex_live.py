import json
from unittest.mock import AsyncMock

import httpx2
import pytest
from a13n_harness.configuration import RunConfiguration
from a13n_harness.models.codex_live import CodexLiveError, CodexLiveModel, ConversationItem, Delegation, Transcript
from a13n_harness.models.codex_live import session as transport
from a13n_harness.models.codex_live.codec import EventDecoder, context_messages, session_payload
from a13n_harness.models.transport import create_model_http_client
from pydantic_ai.providers.openai_codex import OpenAICodexCredentials


def test_codec_reconciles_roles_and_preserves_task_text():
    decoder = EventDecoder()
    assert decoder.decode('{"type":"input_transcript.added","item":{"text":"hel"}}') == Transcript("user", "hel", False)
    decoder.decode('{"type":"output_transcript.added","item":{"text":"working"}}')
    assert decoder.decode('{"type":"turn.done","turn":{"role":"user","transcript":"hello"}}') == Transcript(
        "user", "hello", True
    )
    event = decoder.decode(
        json.dumps(
            {
                "type": "delegation.created",
                "item": {
                    "type": "delegation",
                    "target": "client",
                    "id": "del_1",
                    "content": [{"type": "input_text", "text": "Find my project"}],
                },
            }
        )
    )
    assert event == Delegation(
        "del_1", "Find my project", (ConversationItem("user", "hello"), ConversationItem("assistant", "working"))
    )
    assert decoder.decode('{"type":"future.event"}') is None


@pytest.mark.parametrize(
    "event", ["not json", "[]", '{"type":"turn.done"}', '{"type":"input_transcript.added","item":{"text":42}}']
)
def test_codec_rejects_known_malformed_events(event):
    with pytest.raises(CodexLiveError, match="Malformed"):
        EventDecoder().decode(event)


def test_context_utf8_chunks_and_channels():
    text = "你好🙂" * 400
    messages = [json.loads(item) for item in context_messages(text, "speakable", "del_1")]
    assert "".join(item["content"][0]["text"] for item in messages) == text
    assert all(len(item["content"][0]["text"].encode()) <= 500 for item in messages)
    assert all(item["delegation_item_id"] == "del_1" and item["channel"] == "speakable" for item in messages)
    message = json.loads(next(context_messages("status", "commentary", None)))
    assert message["type"] == "session.context.append"
    assert "delegation_item_id" not in message
    assert (
        session_payload("instructions", "cove", [ConversationItem("assistant", "hello")])["initial_items"][0][
            "content"
        ][0]["type"]
        == "output_text"
    )


@pytest.mark.parametrize("voice", ["marin", "cedar", "unknown"])
def test_rejects_incompatible_voices_before_loading_credentials(voice):
    source = AsyncMock()
    with pytest.raises(CodexLiveError, match="V3 voice"):
        CodexLiveModel(source, voice=voice)
    source.load.assert_not_called()


@pytest.fixture
def live_transport(monkeypatch):
    requests = []
    connections = []
    source = AsyncMock()
    source.load.return_value = OpenAICodexCredentials(
        access_token="test-access", refresh_token="test-refresh", account_id="account-test"
    )
    socket = AsyncMock()
    response = {"status": 201, "body": "v=0\r\nanswer", "location": "/v1/live/rtc_test"}

    def handle(request):
        requests.append(request)
        return httpx2.Response(response["status"], text=response["body"], headers={"location": response["location"]})

    def client(**kwargs):
        assert kwargs["retry"] is None
        return create_model_http_client(**kwargs, transport=httpx2.MockTransport(handle))

    async def connect(uri, **kwargs):
        connections.append((uri, kwargs))
        return socket

    monkeypatch.setattr(transport, "create_model_http_client", client)
    monkeypatch.setattr(transport, "_NoRedirectConnect", connect)
    return source, socket, requests, connections, response


@pytest.mark.anyio
async def test_bootstrap_native_auth_and_single_sideband_initialization(live_transport):
    source, socket, requests, connections, _ = live_transport
    async with CodexLiveModel(source).connect(
        "v=0\r\noffer", on_answer=AsyncMock(), instructions="Delegate tasks"
    ) as session:
        assert session.answer_sdp == "v=0\r\nanswer"
        assert "answer" not in repr(session)
        assert session.call_id == "rtc_test"
        socket.send.assert_not_awaited()
        await session.append_speech("Done")
        await session.close()
    assert len(requests) == len(connections) == 1
    assert str(requests[0].url) == transport.CALLS_URL
    assert requests[0].headers["authorization"] == "Bearer test-access"
    payload = json.loads(requests[0].content)["session"]
    assert payload["delegation"] == {"type": "client"}
    assert payload["model"] == "gpt-live-1-codex"
    assert payload["audio"]["output"]["voice"] == "cove"
    uri, options = connections[0]
    assert uri == "wss://api.openai.com/v1/live/rtc_test"
    assert options["additional_headers"] == {
        "Authorization": "Bearer test-access",
        "chatgpt-account-id": "account-test",
        "openai-alpha": "quicksilver=v2",
    }
    assert socket.send.await_count == 2
    socket.close.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.parametrize("status", [302, 403, 429, 500])
async def test_bootstrap_does_not_retry_or_expose_error_body(live_transport, status):
    source, _, requests, connections, response = live_transport
    response.update(status=status, body="secret provider payload")
    with pytest.raises(CodexLiveError, match=f"HTTP {status}") as error:
        async with CodexLiveModel(source).connect("v=0\r\noffer", on_answer=AsyncMock(), instructions=""):
            pytest.fail("should reject")
    assert "secret" not in str(error.value)
    assert len(requests) == 1
    assert not connections


@pytest.mark.anyio
@pytest.mark.parametrize("location", ["", "/v1/live/../../evil", "/v1/live/rtc_test%2Fother", "rtc_", "untrusted"])
async def test_bootstrap_rejects_invalid_call_id(live_transport, location):
    source, _, _, connections, response = live_transport
    response["location"] = location
    with pytest.raises(CodexLiveError, match="call ID"):
        async with CodexLiveModel(source).connect("v=0\r\noffer", on_answer=AsyncMock(), instructions=""):
            pytest.fail("should reject")
    assert not connections


@pytest.mark.anyio
async def test_sideband_enforces_host_policy(live_transport):
    source, _, requests, connections, _ = live_transport
    model = CodexLiveModel(source, configuration=RunConfiguration(allowed_hosts=frozenset({"chatgpt.com"})))
    with pytest.raises(CodexLiveError):
        async with model.connect("v=0\r\noffer", on_answer=AsyncMock(), instructions=""):
            pytest.fail("should reject")
    assert len(requests) == 1
    assert not connections


@pytest.mark.anyio
async def test_bootstrap_account_switch_rejected(live_transport):
    source, _, _, connections, _ = live_transport
    first = source.load.return_value
    source.load.side_effect = [
        first,
        OpenAICodexCredentials(access_token="changed", refresh_token="changed", account_id="other"),
    ]
    with pytest.raises(CodexLiveError, match="account changed"):
        async with CodexLiveModel(source).connect("v=0\r\noffer", on_answer=AsyncMock(), instructions=""):
            pytest.fail("should reject")
    assert not connections


def test_sideband_redirect_is_never_followed():
    error = RuntimeError("redirect")
    assert transport._NoRedirectConnect.process_redirect(None, error) is error


@pytest.mark.parametrize("completion_order", [("assistant", "user"), ("user", "assistant")])
def test_interleaved_finals_preserve_first_delta_order(completion_order):
    decoder = EventDecoder()
    for kind, text in (("input_transcript.added", "question"), ("output_transcript.added", "answer")):
        decoder.decode(json.dumps({"type": kind, "item": {"text": text}}))
    for role in completion_order:
        decoder.decode(json.dumps({"type": "turn.done", "turn": {"role": role, "transcript": role + " final"}}))
    event = decoder.decode(
        json.dumps(
            {
                "type": "delegation.created",
                "item": {
                    "type": "delegation",
                    "target": "client",
                    "id": "del_1",
                    "content": [{"type": "input_text", "text": "task"}],
                },
            }
        )
    )
    assert isinstance(event, Delegation)
    assert event.context == (ConversationItem("user", "user final"), ConversationItem("assistant", "assistant final"))


def test_delegation_consumes_pending_context():
    decoder = EventDecoder()
    for text in ("old request", "new request"):
        decoder.decode(json.dumps({"type": "input_transcript.added", "item": {"text": text}}))
        event = decoder.decode(
            json.dumps(
                {
                    "type": "delegation.created",
                    "item": {
                        "type": "delegation",
                        "target": "client",
                        "id": "del_1",
                        "content": [{"type": "input_text", "text": "task"}],
                    },
                }
            )
        )
        assert isinstance(event, Delegation)
        assert event.context == (ConversationItem("user", text),)


@pytest.mark.anyio
async def test_answer_activates_media_before_sideband_attach(live_transport, monkeypatch):
    source, socket, requests, connections, _ = live_transport
    media_connected = False

    async def answer(sdp):
        nonlocal media_connected
        assert sdp == "v=0\r\nanswer"
        assert len(requests) == 1
        assert not connections
        media_connected = True

    async def attach(uri, **kwargs):
        assert media_connected
        connections.append((uri, kwargs))
        return socket

    monkeypatch.setattr(transport, "_NoRedirectConnect", attach)
    async with CodexLiveModel(source).connect("v=0\r\noffer", instructions="", on_answer=answer):
        assert len(connections) == 1


@pytest.mark.anyio
async def test_cancelled_media_activation_never_attaches_or_replays(live_transport):
    from anyio import CancelScope, sleep_forever

    source, socket, requests, connections, _ = live_transport
    with CancelScope() as scope:

        async def answer(sdp):
            scope.cancel()
            await sleep_forever()

        async with CodexLiveModel(source).connect("v=0\r\noffer", instructions="", on_answer=answer):
            pytest.fail("cancelled startup must not yield")
    assert len(requests) == 1
    assert not connections
    socket.send.assert_not_awaited()


@pytest.mark.anyio
async def test_sideband_rejection_exposes_only_status(live_transport, monkeypatch):
    from websockets.datastructures import Headers
    from websockets.exceptions import InvalidStatus
    from websockets.http11 import Response

    source, _, requests, _, _ = live_transport

    async def reject(*args, **kwargs):
        raise InvalidStatus(Response(404, "Not Found", Headers(), b"private provider body"))

    monkeypatch.setattr(transport, "_NoRedirectConnect", reject)
    with pytest.raises(CodexLiveError, match=r"sideband.*HTTP 404") as error:
        async with CodexLiveModel(source).connect("v=0\r\noffer", instructions="", on_answer=AsyncMock()):
            pytest.fail("must reject")
    assert "private" not in str(error.value)
    assert len(requests) == 1
