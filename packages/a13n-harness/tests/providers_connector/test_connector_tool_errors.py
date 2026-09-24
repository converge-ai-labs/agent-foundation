"""Provider refusal results preserve useful codes without exposing upstream data."""

import httpx2
import pytest
from a13n_harness.providers.connector.contracts import ConnectionBinding, ConnectorProviderError
from a13n_harness.providers.connector.http import ConnectorHttpClient
from a13n_harness.providers.connector.tool_errors import rejected_tool_outcome

from .test_connector_adapters import _AllowEndpoint, _composio


@pytest.mark.parametrize(
    ("status", "body", "code"),
    [
        (403, {"errorCode": "secret", "errorMessage": "secret"}, "permission_denied"),
        (404, {"message": "secret"}, "not_found"),
        (401, {"message": "secret"}, "authentication_required"),
        (429, {"message": "secret"}, "rate_limited"),
        (400, {"message": "secret"}, "tool_rejected"),
    ],
)
@pytest.mark.anyio
async def test_http_refusals_become_safe_tool_results(status, body, code):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx2.Response(status, json=body)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http:
        transport = ConnectorHttpClient(http, _AllowEndpoint(), response_max_bytes=4096)
        with pytest.raises(ConnectorProviderError) as raised:
            await transport.request(
                "POST",
                endpoint="https://connector.example",
                path="/v1/saas/actions/test",
                api_key="private",
                write=True,
            )
    outcome = rejected_tool_outcome(raised.value, request_id="tool_test")
    assert outcome is not None and outcome.kind == "failed" and outcome.error.code == code
    assert "secret" not in outcome.model_dump_json() and "private" not in outcome.model_dump_json()
    assert len(calls) == 1


@pytest.mark.parametrize(
    "error",
    [
        ConnectorProviderError("provider_unavailable", outcome_unknown=True),
        ConnectorProviderError("connection_substitution"),
        ConnectorProviderError("incompatible_tool_version"),
    ],
)
def test_unknown_and_binding_failures_are_not_known_refusals(error):
    assert rejected_tool_outcome(error, request_id="tool_test") is None


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (401, "authentication_required"),
        (403, "permission_denied"),
        (404, "not_found"),
        (429, "rate_limited"),
        ("404", "tool_rejected"),
        (True, "tool_rejected"),
        (None, "tool_rejected"),
    ],
)
@pytest.mark.anyio
async def test_composio_http_success_with_tool_failure_keeps_only_safe_status(status, code):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx2.Response(
            200,
            json={
                "successful": False,
                "data": {"status_code": status, "http_error": "private credential"},
                "error": "secret upstream content",
            },
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http:
        connection = _composio(http).connect(
            ConnectionBinding(
                external_ref="ca_external", external_user_correlation="usrh_opaque", connector_key="github"
            )
        )
        with pytest.raises(ConnectorProviderError) as raised:
            await connection.execute_tool(
                tool_key="GITHUB_GET_A_REPOSITORY",
                provider_version="20260903_01",
                arguments={},
                request_id="tool_test",
            )
    outcome = rejected_tool_outcome(raised.value, request_id="tool_test")
    assert outcome is not None and outcome.kind == "failed" and outcome.error.code == code
    assert "secret" not in outcome.model_dump_json() and "private" not in outcome.model_dump_json()
    assert len(calls) == 1


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(b"", id="empty"),
        pytest.param(b"null", id="null"),
        pytest.param(b"[]", id="array"),
        pytest.param(b"{}", id="missing-success"),
        pytest.param(b'{"successful": "false"}', id="invalid-success-type"),
        pytest.param(b"invalid json", id="invalid-json"),
        # Keep the payload out of verbose CI node IDs and runner log processing.
        pytest.param(b"x" * (1024 * 1024 + 1), id="oversized-response"),
    ],
)
@pytest.mark.anyio
async def test_composio_invalid_execution_evidence_remains_unknown_without_retry(body):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx2.Response(200, content=body)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http:
        connection = _composio(http).connect(
            ConnectionBinding(
                external_ref="ca_external",
                external_user_correlation="usrh_opaque",
                connector_key="github",
            )
        )
        outcome = await connection.execute_tool(
            tool_key="GITHUB_GET_A_REPOSITORY",
            provider_version="20260903_01",
            arguments={},
            request_id="tool_test",
        )
    assert outcome.kind == "outcome_unknown" and outcome.request_id == "tool_test"
    assert outcome.result is None and len(calls) == 1


def test_generic_scope_missing_remains_a_safe_known_refusal():
    outcome = rejected_tool_outcome(ConnectorProviderError("scope_missing", http_status=403), request_id="tool_test")
    assert outcome.kind == "failed" and outcome.error.code == "scope_missing"
    assert outcome.request_id == "tool_test"
