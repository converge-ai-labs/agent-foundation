"""Daytona owns resource boot; Agent execution and readiness use shared EIP."""

from unittest.mock import AsyncMock

import httpx2
import pytest
from a13n_harness.environment.errors import EnvironmentProviderError, EnvironmentProviderErrorCategory, provider_error
from a13n_harness.providers.environment.native.http import NativeHTTP
from a13n_service.providers.environment.errors import observed_environment_state
from a13n_service.providers.environments.daytona import DAYTONA, management

from .test_native_http_providers import BACKENDS, NativeCloud


@pytest.mark.anyio
@pytest.mark.parametrize("fault", [None, "boot", "eip", "deletion", "lost_create"])
async def test_boot_readiness_lifecycle_and_retained_identity(monkeypatch, fault):
    cloud = NativeCloud("daytona")
    cloud.lose_create_response = fault == "lost_create"

    def transport_init(self, key, url, token, timeout, **kwargs):
        self.key = key
        self.client = httpx2.AsyncClient(base_url=url, transport=httpx2.MockTransport(cloud.request))

    monkeypatch.setattr(NativeHTTP, "__init__", transport_init)
    execution = AsyncMock()
    if fault in {"boot", "eip"}:
        execution.open.side_effect = provider_error(
            "daytona",
            "envd_bootstrap_failed" if fault == "boot" else "provider_device_mismatch",
            EnvironmentProviderErrorCategory.INVALID,
        )
    references = []

    def connect(ref):
        references.append(ref)
        return execution

    monkeypatch.setattr(management, "http_execution", connect)
    recipe = DAYTONA.validate_environment(
        {
            "snapshot": "envd-snapshot",
            "environment": {"APP_MODE": "agent-default"},
            "desktop": True,
            "request_timeout_seconds": 1,
        }
    )
    async with await DAYTONA.open_provider(
        configuration=BACKENDS["daytona"], credential={"api_key": "fixture"}
    ) as provider:

        async def manage(operation, state=None):
            return await getattr(provider, operation)(
                recipe, environment_id="env-fixture", state=state, operation_id="op-test"
            )

        if fault in {"boot", "eip"}:
            with pytest.raises(EnvironmentProviderError) as error:
                await manage("create")
            state = observed_environment_state(error.value, None)
            assert state is not None
            assert state.state["target_id"] == "target-1"
            assert cloud.count == 1
            if fault == "eip":
                execution.close.assert_awaited_once()
            await manage("destroy", state)
            return
        if fault == "lost_create":
            with pytest.raises(EnvironmentProviderError):
                await manage("create")
            assert cloud.count == 1
        state = await manage("create")
        assert cloud.target["env"]["A13N_ENVD_DEVICE_ID"] == "env-fixture"
        assert cloud.target["env"]["APP_MODE"] == "agent-default"
        assert cloud.target["env"]["A13N_DESKTOP"] == "true"
        assert cloud.target["env"]["A13N_DESKTOP_ACCESS"] == "false"
        assert not any("computeruse" in path or "/process/execute" in path for _, path in cloud.calls)
        assert references[0].state == state
        execution.check_ready.assert_awaited_with(frozenset({"computer"}))
        cloud.target["autoDeleteInterval"] = 0
        with pytest.raises(EnvironmentProviderError) as error:
            await manage("stop", state)
        assert error.value.code == "provider_stop_retention_unsupported"
        cloud.target["autoDeleteInterval"] = -1
        await manage("stop", state)
        assert cloud.target["state"] == "stopped"
        assert await manage("start", state) == state
        assert cloud.target["env"]["APP_MODE"] == "agent-default"
        assert cloud.count == 1
        if fault == "deletion":
            cloud.delete_pending = 100
            with pytest.raises(EnvironmentProviderError) as error:
                await manage("destroy", state)
            assert error.value.code == "provider_delete_unconfirmed"
            assert observed_environment_state(error.value, None) == state
            assert sum(method == "DELETE" for method, _ in cloud.calls) == 1
        else:
            await manage("destroy", state)
            assert cloud.target is None
