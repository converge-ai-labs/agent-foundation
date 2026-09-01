from typing import Any

import httpx2
import pytest
from a13n_service.iam import AuthenticatedActor
from a13n_service.model_configs.connection_test import NativeModelConnectionTester
from a13n_service.model_configs.domain import PrincipalRef, WorkspaceSecretCredential
from a13n_service.model_configs.endpoint_policy import EndpointPolicy
from a13n_service.model_configs.providers import built_in_provider_registry

ORG_ID = "org_1234567890abcdef"
WORKSPACE_ID = "ws_1234567890abcdef"
USER_ID = "usr_1234567890abcdef"
SECRET_ID = "sec_1234567890abcdef"


class StaticSecretResolver:
    async def resolve(self, **_: object) -> str:
        return "test-api-key"


@pytest.mark.anyio
async def test_candidate_connection_test_makes_one_minimal_request_and_discards_output() -> None:
    requests: list[httpx2.Request] = []

    async def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 1,
                "model": "custom-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "O"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 4, "completion_tokens": 1, "total_tokens": 5},
            },
        )

    registry = built_in_provider_registry()
    credential = WorkspaceSecretCredential(secret_id=SECRET_ID)
    selection = registry.validate(
        provider_type="openai_compatible",
        model_name="custom-model",
        provider_config={"base_url": "https://8.8.8.8/v1"},
        credential=credential,
        capabilities=None,
    )
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond), follow_redirects=False) as client:
        tester = NativeModelConnectionTester(
            secret_resolver=StaticSecretResolver(),
            endpoint_policy=EndpointPolicy(),
            http_client=client,
        )
        await tester(
            actor=AuthenticatedActor(
                principal=PrincipalRef(principal_type="user", principal_id=USER_ID),
                auth_method="session",
                credential_id="ses_1234567890abcdef",
                boundary_workspace_id=WORKSPACE_ID,
            ),
            organization_id=ORG_ID,
            workspace_id=WORKSPACE_ID,
            provider_type="openai_compatible",
            model_name="custom-model",
            credential=credential,
            selection=selection,
        )

    assert len(requests) == 1
    body: dict[str, Any] = __import__("json").loads(requests[0].content)
    assert body.get("max_tokens", body.get("max_completion_tokens")) == 1
    assert requests[0].headers["authorization"] == "Bearer test-api-key"
