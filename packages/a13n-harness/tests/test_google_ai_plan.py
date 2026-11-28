"""Google AI Plan protocol and stateless stream fixtures; no external requests."""

import base64
import hashlib
import json
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx2
import jwt
import pytest
from a13n_harness.models.google_ai_plan import (
    INTERACTIONS_URL,
    GoogleAIPlanError,
    GoogleAIPlanModel,
    GoogleAIPlanProvider,
)
from a13n_harness.providers.model.oauth.google_ai_plan import (
    JWKS_URL,
    QUOTA_SCOPE,
    TOKEN_URL,
    DPoPKey,
    GoogleAIPlanClient,
    GoogleAIPlanCredentials,
    GoogleAIPlanFlow,
    callback_code,
    refresh_google_ai_plan,
)
from a13n_harness.providers.model.oauth.models import ModelAuthenticationError
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic_ai.messages import (
    BinaryContent,
    ModelRequest,
    SystemPromptPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestParameters, override_allow_model_requests
from pydantic_ai.tools import ToolDefinition

pytestmark = pytest.mark.anyio
REDIRECT = "http://127.0.0.1:8765/oauth2callback"
MODEL = "gemini-4-hydrogen"


def proof(request: httpx2.Request) -> dict:
    token = request.headers["DPoP"]
    header = jwt.get_unverified_header(token)
    assert header["typ"] == "dpop+jwt" and header["alg"] == "ES256" and "d" not in header["jwk"]
    claims = jwt.decode(
        token, jwt.PyJWK.from_dict(header["jwk"]).key, algorithms=["ES256"], options={"verify_iat": False}
    )
    assert claims["htm"] == "POST" and claims["htu"] == TOKEN_URL and "ath" not in claims
    return claims


@pytest.mark.parametrize("bad_subject", [False, True])
async def test_google_login_pkce_oauth_proof_and_verified_subject(bad_subject):
    registration = GoogleAIPlanClient.configured("fixture-client", "fixture-secret")
    rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = {**jwt.algorithms.RSAAlgorithm.to_jwk(rsa_key.public_key(), as_dict=True), "kid": "fixture"}
    expected_challenge = base64.urlsafe_b64encode(hashlib.sha256(b"fixture-verifier").digest()).decode().rstrip("=")
    expected_jti = base64.urlsafe_b64encode(hashlib.sha256(b"fixture-code").digest()).decode().rstrip("=")

    def server(request: httpx2.Request) -> httpx2.Response:
        assert request.headers["user-agent"].startswith("agent-foundation/")
        if str(request.url) == JWKS_URL:
            return httpx2.Response(200, json={"keys": [public_key]})
        assert str(request.url) == TOKEN_URL
        assert parse_qs(request.content.decode()) == {
            "client_id": ["fixture-client"],
            "client_secret": ["fixture-secret"],
            "redirect_uri": [REDIRECT],
            "code_verifier": ["fixture-verifier"],
            "code": ["fixture-code"],
            "grant_type": ["authorization_code"],
        }
        assert proof(request)["jti"] == expected_jti
        now = int(time.time())
        id_token = jwt.encode(
            {
                "iss": "https://accounts.google.com",
                "aud": "fixture-client",
                "iat": now,
                "exp": now + 120,
                "sub": "" if bad_subject else "fixture-subject",
                "nonce": "fixture-nonce",
            },
            rsa_key,
            algorithm="RS256",
            headers={"kid": "fixture"},
        )
        return httpx2.Response(
            200,
            json={
                "access_token": "fixture-access",
                "refresh_token": "fixture-refresh",
                "id_token": id_token,
                "expires_in": 3600,
                "scope": QUOTA_SCOPE,
            },
            headers={"DPoP-Nonce": "initial-server-nonce"},
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(server)) as client:
        flow = GoogleAIPlanFlow(
            registration,
            client,
            redirect_uri=REDIRECT,
            state="fixture-state",
            code_verifier="fixture-verifier",
            nonce="fixture-nonce",
        )
        params = parse_qs(urlsplit(flow.authorization_url()).query)
        assert params["code_challenge"] == [expected_challenge] and params["code_challenge_method"] == ["S256"]
        assert QUOTA_SCOPE in params["scope"][0].split()
        callback = f"{REDIRECT}?{urlencode({'state': 'fixture-state', 'code': 'fixture-code'})}"
        if bad_subject:
            with pytest.raises(ModelAuthenticationError, match="unverifiable"):
                await flow.exchange_callback(callback)
        else:
            credentials = await flow.exchange_callback(callback)
            assert credentials.account_id == "fixture-subject" and credentials.dpop_nonce == "initial-server-nonce"
            assert "fixture-refresh" not in repr(credentials) and credentials.dpop_jwk.d not in repr(credentials)


@pytest.mark.parametrize(
    "callback",
    [
        "http://localhost:8765/oauth2callback?state=valid&code=code",
        "http://127.0.0.1:8766/oauth2callback?state=valid&code=code",
        f"{REDIRECT}?state=invalid&code=code",
        f"{REDIRECT}?state=valid&code=code&code=second",
        f"{REDIRECT}?state=valid&code=code#fragment",
        f"{REDIRECT}?state=valid&code=%FA",
        f"{REDIRECT}?state=valid&code=code&error=access_denied",
    ],
)
async def test_google_callback_rejects_unmatched_or_ambiguous_urls(callback):
    with pytest.raises(ModelAuthenticationError, match="callback"):
        callback_code(callback, REDIRECT, "valid")


async def test_google_refresh_challenge_and_clock_skew_replace_the_entire_grant():
    registration = GoogleAIPlanClient.configured("fixture-client", "fixture-secret")
    key = DPoPKey.generate()
    old = GoogleAIPlanCredentials(
        account_id="account",
        client_id=registration.client_id,
        access_token="old-access",
        refresh_token="old-refresh",
        expires_at=datetime.now(UTC),
        dpop_jwk=key,
    )
    attempts = []

    def server(request: httpx2.Request) -> httpx2.Response:
        claims = proof(request)
        attempts.append(claims)
        if len(attempts) == 1:
            return httpx2.Response(400, json={"error": "use_dpop_nonce"}, headers={"DPoP-Nonce": "challenge"})
        if len(attempts) == 2:
            assert claims["nonce"] == "challenge"
            return httpx2.Response(
                400, json={"error": "invalid_dpop_proof"}, headers={"Date": "Fri, 27 Nov 2026 12:00:00 GMT"}
            )
        assert claims["iat"] == int(datetime(2026, 11, 27, 12, tzinfo=UTC).timestamp())
        return httpx2.Response(
            200, json={"access_token": "new-access", "refresh_token": "new-refresh"}, headers={"DPoP-Nonce": "latest"}
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(server)) as client:
        updated = await refresh_google_ai_plan(registration, client, old)
    assert len({item["jti"] for item in attempts}) == 3
    assert updated.dpop_jwk == key and updated.account_id == old.account_id
    assert updated.refresh_token == "new-refresh" and updated.dpop_nonce == "latest"
    assert updated.expires_at > datetime.now(UTC) + timedelta(minutes=30)


def _frame(kind: str, **fields) -> str:
    return f"event: {kind}\r\ndata: {json.dumps({'event_type': kind, **fields})}\r\n\r\n"


async def test_google_stream_multimodal_thinking_usage_and_signed_tool_replay():
    requests = []
    stream = "".join(
        [
            _frame("step.start", index=0, step={"type": "thought"}),
            _frame(
                "step.delta", index=0, delta={"type": "thought_summary", "content": {"type": "text", "text": "Plan"}}
            ),
            _frame("step.delta", index=0, delta={"type": "thought_signature", "signature": "sig"}),
            _frame("step.delta", index=0, delta={"type": "thought_signature", "signature": "nature"}),
            _frame("step.stop", index=0),
            _frame("step.start", index=1, step={"type": "function_call", "id": "call_fixture", "name": "lookup"}),
            _frame("step.delta", index=1, delta={"type": "arguments_delta", "arguments": '{"id":'}),
            _frame("step.delta", index=1, delta={"type": "arguments_delta", "arguments": "1}"}),
            _frame("step.stop", index=1),
            _frame(
                "interaction.completed",
                interaction={
                    "id": "response",
                    "status": "requires_action",
                    "usage": {
                        "total_input_tokens": 20,
                        "total_output_tokens": 5,
                        "total_thought_tokens": 2,
                        "total_cached_tokens": 3,
                    },
                },
            ),
        ]
    )

    def server(request: httpx2.Request) -> httpx2.Response:
        assert str(request.url) == INTERACTIONS_URL and request.headers["Authorization"] == "Bearer fixture"
        assert "DPoP" not in request.headers and "x-goog-api-key" not in request.headers
        requests.append(json.loads(request.content))
        return httpx2.Response(200, text=stream, headers={"Content-Type": "text/event-stream"})

    def auth(request):
        request.headers["Authorization"] = "Bearer fixture"
        return request

    prompt = ModelRequest(
        parts=[
            SystemPromptPart("system"),
            UserPromptPart(["question", BinaryContent(b"image", media_type="image/png")]),
        ]
    )
    parameters = ModelRequestParameters(
        function_tools=[ToolDefinition(name="lookup", parameters_json_schema={"type": "object"})]
    )
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(server)) as client:
        model = GoogleAIPlanModel(MODEL, provider=GoogleAIPlanProvider(client, auth=auth))
        with override_allow_model_requests(True):
            response = await model.request([prompt], {"thinking": "high"}, parameters)
            assert response.finish_reason == "tool_call" and response.provider_response_id == "response"
            assert response.usage.input_tokens == 20 and response.usage.output_tokens == 7
            assert response.usage.cache_read_tokens == 3
            thinking, tool = response.parts
            assert isinstance(thinking, ThinkingPart) and thinking.signature == "signature"
            assert isinstance(tool, ToolCallPart) and tool.args_as_dict() == {"id": 1}
            reply = ModelRequest(parts=[ToolReturnPart("lookup", "found", tool_call_id=tool.tool_call_id)])
            await model.request([prompt, response, reply], None, parameters)
            await model.request([prompt, replace(response, model_name="different-model"), reply], None, parameters)
    assert requests[0]["store"] is False and requests[0]["stream"] is True
    assert requests[0]["system_instruction"] == "system"
    assert requests[0]["generation_config"]["thinking_level"] == "high"
    assert requests[0]["input"][0]["content"][1] == {"type": "image", "mime_type": "image/png", "data": "aW1hZ2U="}
    assert [step["type"] for step in requests[1]["input"]] == [
        "user_input",
        "thought",
        "function_call",
        "function_result",
    ]
    assert requests[1]["input"][1]["signature"] == "signature"
    assert requests[2]["input"][1]["signature"] == "skip_thought_signature_validator"


@pytest.mark.parametrize(
    ("status", "body", "reason", "expected_attempts"),
    [
        (429, {"error": {"message": "rate limit exceeded (limit: 8 requests per day)"}}, "daily_limit", 1),
        (
            429,
            {"details": [{"violations": [{"quotaId": "GeneratePerDay", "quotaValue": "0"}]}]},
            "model_not_on_plan",
            1,
        ),
        (400, {"error": {"message": "model not supported for per-user quota"}}, "model_not_on_plan", 1),
        (403, [{"details": [{"reason": "SERVICE_DISABLED"}]}], "disabled", 1),
        (403, {"details": [{"reason": "ACCESS_TOKEN_SCOPE_INSUFFICIENT"}]}, "sign_in", 1),
        (402, {}, "ineligible", 1),
        (429, {}, "rate_limit", 2),
    ],
)
async def test_google_quota_retries_only_transient_errors(status, body, reason, expected_attempts):
    calls = 0

    def server(request):
        nonlocal calls
        calls += 1
        return httpx2.Response(status, json=body, headers={"Retry-After": "0"})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(server)) as client:
        model = GoogleAIPlanModel(
            MODEL, provider=GoogleAIPlanProvider(client, auth=httpx2.BasicAuth("fixture", "fixture"))
        )
        with override_allow_model_requests(True), pytest.raises(GoogleAIPlanError) as exc:
            await model.request([ModelRequest(parts=[UserPromptPart("question")])], None, ModelRequestParameters())
    assert exc.value.reason == reason and calls == expected_attempts


async def test_google_local_loopback_rejects_duplicate_callback_before_exchange(free_tcp_port, monkeypatch):
    import anyio

    registration = GoogleAIPlanClient.configured("fixture-client", "fixture-secret")
    redirect = f"http://127.0.0.1:{free_tcp_port}/oauth2callback"
    flow = GoogleAIPlanFlow(registration, None, redirect_uri=redirect, state="valid")
    calls = []

    async def exchange(self, code):
        calls.append(code)
        return code

    monkeypatch.setattr(GoogleAIPlanFlow, "exchange_code", exchange)

    async def wait_for_callback():
        assert await flow.exchange_code_from_callback(timeout_seconds=5) == "fixture-code"

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(wait_for_callback)
        async with httpx2.AsyncClient(trust_env=False) as client:
            with anyio.fail_after(4):
                while True:
                    try:
                        bad = await client.get(redirect + "?state=valid&code=x&code=y")
                    except httpx2.ConnectError:
                        await anyio.sleep(0.02)
                    else:
                        break
            assert bad.status_code == 400 and calls == []
            assert (await client.get(redirect + "?state=valid&code=fixture-code")).status_code == 200
    assert calls == ["fixture-code"]


async def test_google_does_not_retry_truncated_model_stream():
    from pydantic_ai.exceptions import ModelAPIError

    attempts = 0

    def respond(request):
        nonlocal attempts
        attempts += 1
        return httpx2.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            text=_frame(
                "step.start",
                index=0,
                step={"type": "model_output", "content": [{"type": "text", "text": "partial"}]},
            ),
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        model = GoogleAIPlanModel(MODEL, provider=GoogleAIPlanProvider(client, auth=lambda request: request))
        with override_allow_model_requests(True), pytest.raises(ModelAPIError, match=r"before interaction\.completed"):
            await model.request([ModelRequest(parts=[UserPromptPart("Hello")])], None, ModelRequestParameters())
    assert attempts == 1
