from unittest.mock import AsyncMock

import httpx2
import pytest
from a13n_harness.models import codex_dictation
from a13n_harness.models.transport import create_model_http_client
from pydantic_ai.providers.openai_codex import OpenAICodexCredentials


@pytest.fixture
def upload(monkeypatch):
    source = AsyncMock()
    source.load.return_value = OpenAICodexCredentials(
        access_token="test-access", refresh_token="test-refresh", account_id="account-test"
    )
    requests = []
    response = {"status": 200, "body": b'{"text":" hello "}', "headers": {}}

    async def handle(request):
        await request.aread()
        requests.append(request)
        return httpx2.Response(response["status"], content=response["body"], headers=response["headers"])

    def client(**kwargs):
        assert kwargs["retry"] is None and kwargs["timeout"] == 60
        return create_model_http_client(**kwargs, transport=httpx2.MockTransport(handle))

    monkeypatch.setattr(codex_dictation, "create_model_http_client", client)
    return source, requests, response


@pytest.mark.anyio
async def test_native_authenticated_file_only_upload(upload):
    source, requests, _ = upload
    assert await codex_dictation.transcribe(b"wav-bytes", source) == "hello"
    assert len(requests) == 1
    request = requests[0]
    assert str(request.url) == codex_dictation.TRANSCRIBE_URL
    assert request.headers["authorization"] == "Bearer test-access"
    assert request.headers["chatgpt-account-id"] == "account-test"
    assert b'name="file"; filename="audio.wav"' in request.content
    assert b"Content-Type: audio/wav" in request.content
    assert b"wav-bytes" in request.content
    assert request.content.count(b"Content-Disposition:") == 1
    source.load.assert_awaited_once()
    source.save.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "status,headers,message",
    [
        (401, {}, "authentication was rejected"),
        (429, {}, "rate limited"),
        (403, {"cf-mitigated": "challenge"}, "transport challenge"),
        (302, {"location": "https://example.com/"}, "HTTP 302"),
    ],
)
async def test_upload_error_is_sanitized_and_not_replayed(upload, status, headers, message):
    source, requests, response = upload
    response.update(status=status, headers=headers, body=b"sensitive upstream body")
    with pytest.raises(codex_dictation.CodexDictationError, match=message) as error:
        await codex_dictation.transcribe(b"audio", source)
    assert "sensitive" not in str(error.value)
    assert len([r for r in requests if str(r.url) == codex_dictation.TRANSCRIBE_URL]) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("body", [b"not-json", b"[]", b'{"text":3}', b"x" * (512 * 1024 + 1)])
async def test_invalid_or_oversized_response(upload, body):
    source, _, response = upload
    response["body"] = body
    with pytest.raises(codex_dictation.CodexDictationError, match="invalid"):
        await codex_dictation.transcribe(b"audio", source)


@pytest.mark.anyio
async def test_native_refresh_persists_and_replays_same_upload_on_authenticated_401(monkeypatch):
    source = AsyncMock()
    source.load.return_value = OpenAICodexCredentials(
        access_token="old-access", refresh_token="old-refresh", account_id="account-test"
    )
    uploads = []

    async def handle(request):
        await request.aread()
        if request.url.host == "auth.openai.com":
            return httpx2.Response(
                200, json={"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 3600}
            )
        uploads.append((request.headers["authorization"], request.content))
        return httpx2.Response(401) if len(uploads) == 1 else httpx2.Response(200, json={"text": "refreshed"})

    monkeypatch.setattr(
        codex_dictation,
        "create_model_http_client",
        lambda **kwargs: create_model_http_client(**kwargs, transport=httpx2.MockTransport(handle)),
    )
    assert await codex_dictation.transcribe(b"same-wav", source) == "refreshed"
    assert [auth for auth, _ in uploads] == ["Bearer old-access", "Bearer new-access"]
    assert uploads[0][1] == uploads[1][1]
    source.save.assert_awaited_once()
    assert source.save.call_args.args[0].refresh_token == "new-refresh"
