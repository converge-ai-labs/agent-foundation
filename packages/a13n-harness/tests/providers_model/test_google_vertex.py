import json
from unittest.mock import patch

import httpx2
import pytest
from a13n_harness.providers.model.credentials import GoogleServiceAccount
from a13n_harness.providers.model.google_vertex import DEFINITION
from google.oauth2.credentials import Credentials
from pydantic_ai.messages import ModelRequest, TextPart, UserPromptPart
from pydantic_ai.models import ModelRequestParameters


@pytest.mark.anyio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("authentication", ["api_key", "service_account"])
async def test_vertex_authentication_preserves_native_paths_and_streams(authentication, streaming, monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "ambient-key")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "ambient-project")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "ambient-location")
    configuration = {"base_url": "https://gateway.example"}
    credential = {"api_key": "fixture-key"}
    if authentication == "service_account":
        configuration.update(project_id="fixture-project", location="us-central1")
        credential = {
            "project_id": "fixture-project",
            "client_email": "fixture@example.com",
            "private_key": "fixture-pem",
        }
    requests = []

    async def handle(request):
        requests.append(request)
        action = "streamGenerateContent" if streaming else "generateContent"
        prefix = "projects/fixture-project/locations/us-central1/" if authentication == "service_account" else ""
        assert request.url.path == f"/v1beta1/{prefix}publishers/google/models/gemini-test:{action}"
        assert request.headers["x-routing-key"] == "team"
        if authentication == "api_key":
            assert request.headers["x-goog-api-key"] == "fixture-key"
            assert "authorization" not in request.headers
        else:
            assert request.headers["authorization"] == "Bearer fixture-service-account-token"
            assert "x-goog-api-key" not in request.headers
        assert json.loads(request.content)["contents"]
        response = {
            "candidates": [{"content": {"role": "model", "parts": [{"text": "OK"}]}, "finishReason": "STOP"}],
            "modelVersion": "gemini-test",
            "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 1},
        }
        if streaming:
            assert request.url.params["alt"] == "sse"
            return httpx2.Response(
                200, headers={"content-type": "text/event-stream"}, content="data: " + json.dumps(response) + "\n\n"
            )
        return httpx2.Response(200, json=response)

    with patch.object(
        GoogleServiceAccount, "native_credentials", return_value=Credentials("fixture-service-account-token")
    ):
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
            model = await DEFINITION.build(
                "gemini-test",
                configuration=configuration,
                credential=credential,
                extra_headers={"x-routing-key": "team"},
                http_client=client,
            )
            async with model:
                messages = [ModelRequest(parts=[UserPromptPart("Hi")])]
                if streaming:
                    async with model.request_stream(messages, None, ModelRequestParameters()) as stream:
                        async for _ in stream:
                            pass
                        response = stream.get()
                else:
                    response = await model.request(messages, None, ModelRequestParameters())
            assert response.parts == [TextPart("OK")]
            assert not client.is_closed
    assert len(requests) == 1


@pytest.mark.parametrize(
    "configuration",
    [{"project_id": "project"}, {"location": "us-central1"}, {"project_id": "project", "location": "us-central1"}],
)
def test_vertex_api_key_rejects_service_account_routing(configuration):
    with pytest.raises(ValueError, match="does not accept project_id or location"):
        DEFINITION.bind(configuration, {"api_key": "fixture-key"})


@pytest.mark.parametrize("configuration", [{}, {"project_id": "project"}, {"location": "us-central1"}])
def test_vertex_service_account_keeps_its_required_routing(configuration):
    with patch.object(GoogleServiceAccount, "native_credentials", return_value=Credentials("token")):
        with pytest.raises(ValueError, match="requires project_id and location"):
            DEFINITION.bind(
                configuration,
                {"project_id": "project", "client_email": "fixture@example.com", "private_key": "fixture-pem"},
            )


def test_vertex_credentials_are_exclusive_and_remain_secret():
    connection = DEFINITION.bind({}, {"api_key": "fixture-key"})
    assert "fixture-key" not in repr(connection.credential)
    with pytest.raises(ValueError):
        DEFINITION.bind({}, {"api_key": "fixture-key", "private_key": "fixture-pem"})
    with pytest.raises(ValueError):
        DEFINITION.bind({}, {"api_key": " "})


@pytest.mark.parametrize("header", ["authorization", "x-goog-api-key", "x-goog-user-project"])
def test_vertex_authentication_headers_remain_provider_owned(header):
    with pytest.raises(ValueError, match="authentication"):
        DEFINITION.bind({}, {"api_key": "fixture-key"}, extra_headers={header: "other"})
    with pytest.raises(ValueError, match="authentication"):
        DEFINITION.bind({"session_affinity_header": header}, {"api_key": "fixture-key"})
