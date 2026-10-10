"""The real SDK attachment path only reads control state and constructs local clients."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import httpx
import pytest
from a13n_environment.e2b.connection import close_sandbox, open_sandbox
from a13n_environment.errors import EnvironmentProviderError
from e2b import AsyncSandbox
from e2b.api.client.models.sandbox_detail import SandboxDetail
from e2b.api.client.models.sandbox_lifecycle import SandboxLifecycle
from e2b.api.client.models.sandbox_on_timeout import SandboxOnTimeout
from e2b.api.client.models.sandbox_state import SandboxState
from e2b.api.client_async import AsyncApiClient
from e2b.connection_config import ApiParams

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("case", ["running", "paused", "auto_resume", "missing_token", "absent"])
async def test_read_only_attachment_preserves_secure_token_and_rejects_unsafe_targets(monkeypatch, case):
    now = datetime.now(UTC)
    detail = SandboxDetail(
        template_id="template-test",
        sandbox_id="sandbox-test",
        client_id="client-test",
        started_at=now,
        end_at=now + timedelta(hours=1),
        envd_version="0.6.2",
        cpu_count=2,
        memory_mb=512,
        disk_size_mb=512,
        state=SandboxState.PAUSED if case == "paused" else SandboxState.RUNNING,
        envd_access_token="" if case == "missing_token" else "execution-token",
        lifecycle=SandboxLifecycle(auto_resume=case == "auto_resume", on_timeout=SandboxOnTimeout.KILL),
    )
    calls = []

    def request(request):
        calls.append((request.method, request.url.path))
        assert request.headers["X-API-Key"] == "account-token"
        return (
            httpx.Response(404, json={"code": 404, "message": "not found"})
            if case == "absent"
            else httpx.Response(200, json=detail.to_dict())
        )

    def client(config):
        return AsyncApiClient(config, transport=httpx.MockTransport(request))

    monkeypatch.setattr("e2b.api.client_async.get_api_client", client)
    forbidden = AsyncMock(side_effect=AssertionError("Lifecycle mutation during attachment"))
    for name in ("create", "connect", "set_timeout", "pause", "kill"):
        monkeypatch.setattr(AsyncSandbox, name, forbidden)
    observed = []
    options = ApiParams(api_key="account-token", api_url="https://api.fixture", domain="sandbox.fixture")
    if case == "running":
        sandbox = await open_sandbox("sandbox-test", options, lambda info: observed.append(info.sandbox_id))
        try:
            assert sandbox.sandbox_id == "sandbox-test"
            assert sandbox.connection_config.sandbox_headers["X-Access-Token"] == "execution-token"
        finally:
            await close_sandbox(sandbox)
        assert observed == ["sandbox-test"]
    else:
        with pytest.raises(EnvironmentProviderError) as failure:
            await open_sandbox("sandbox-test", options, lambda info: observed.append(info.sandbox_id))
        assert (
            failure.value.code
            == {
                "paused": "provider_target_stopped",
                "auto_resume": "provider_auto_resume_unsupported",
                "missing_token": "provider_execution_token_missing",
                "absent": "provider_target_missing",
            }[case]
        )
    assert calls == [("GET", "/sandboxes/sandbox-test")]
    forbidden.assert_not_called()
