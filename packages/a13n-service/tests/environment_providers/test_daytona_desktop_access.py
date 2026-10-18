"""Daytona access composes a port-scoped route with the supervisor's authority."""

import asyncio
import base64
import json
import shlex

import httpx2
import pytest
from a13n_harness.environment.errors import EnvironmentProviderError
from a13n_harness.environment.models import EnvironmentState
from a13n_harness.providers.environment.daytona.shared import DaytonaEnvironmentConfiguration
from a13n_harness.providers.environment.native.http import NativeHTTP
from a13n_service.providers.environment import desktop_access as access
from a13n_service.providers.environments.daytona import DAYTONA
from a13n_service.providers.environments.daytona.configuration import DaytonaRecipe
from a13n_service.runs.desktop_assistance.admission import target_key

from .test_native_http_providers import BACKENDS, NativeCloud


def test_recipe_execution_identity_and_native_target_aliases():
    for desktop in [False, True]:
        config = DaytonaRecipe(desktop=desktop, snapshot="custom")
        assert config.fingerprint == DaytonaEnvironmentConfiguration(desktop=desktop, snapshot="custom").fingerprint
    config = DaytonaRecipe(desktop=True, desktop_assistance=True, snapshot="custom")
    assert DAYTONA.execution_environment(config).fingerprint == config.fingerprint
    for config in [{"desktop_assistance": True}, {"desktop_assistance": True, "snapshot": "custom"}]:
        with pytest.raises(ValueError):
            DaytonaRecipe.model_validate(config)
    key = target_key(EnvironmentState(provider_key="daytona", state_version="1", state={"target_id": "same"}))
    assert key is not None
    assert key == target_key(
        EnvironmentState(
            provider_key="daytona",
            state_version="1",
            state={"target_id": "same", "environment_id": "alias"},
        )
    )
    assert key != target_key(EnvironmentState(provider_key="e2b", state_version="1", state={"sandbox_id": "same"}))


@pytest.mark.parametrize("fault", [None, "missing", "uncertain_revoke", "unsafe_url", "denied_preview"])
def test_access_is_attach_only_and_revoke_does_not_depend_on_preview(fault, monkeypatch):
    generation = "a" * 32

    class Cloud(NativeCloud):
        async def request(self, request):
            path = request.url.path
            if path.endswith("/signed-preview-url"):
                self.calls.append((request.method, path))
                assert "/ports/6081/" in path and request.url.params["expiresInSeconds"] == "120"
                return httpx2.Response(
                    403 if fault == "denied_preview" else 200,
                    json={
                        "url": "https://6081-port-scoped.example" + ("?token=unsafe" if fault == "unsafe_url" else ""),
                        "token": "port-only-token",
                    },
                )
            if path.endswith("/process/execute"):
                self.calls.append((request.method, path))
                body = json.loads(request.content)
                command = shlex.split(body["command"])
                assert command[:4] == ["sudo", "-n", "sh", "-c"]
                invocation = shlex.split(command[-1])
                payload = json.loads(base64.b64decode(invocation[invocation.index("--request") + 1]))
                if fault == "missing":
                    reply = {"error": "endpoint_unavailable"}
                elif payload["operation"] == "describe":
                    reply = {
                        "result": {
                            "protocol_version": 1,
                            "generation": generation,
                            "port": 6081,
                            "max_ttl_seconds": 60,
                            "read_only_enforced": True,
                            "revocation_fences_grants": True,
                        }
                    }
                elif fault == "uncertain_revoke":
                    return httpx2.Response(503, json={})
                else:
                    reply = {
                        "result": {
                            "id": payload["id"],
                            "generation": generation,
                            "epoch": 1,
                            "state": "revoked",
                            "mode": "control",
                            "remaining_seconds": 0,
                        }
                    }
                return httpx2.Response(200, json={"exitCode": 0, "result": json.dumps(reply)})
            return await super().request(request)

    async def scenario():
        cloud = Cloud("daytona")

        def transport_init(self, key, url, token, timeout, **kwargs):
            self.key = key
            self.client = httpx2.AsyncClient(base_url=url, transport=httpx2.MockTransport(cloud.request))

        monkeypatch.setattr(NativeHTTP, "__init__", transport_init)
        async with await DAYTONA.open_provider(
            configuration=BACKENDS["daytona"], credential={"api_key": "unused"}
        ) as provider:
            recipe = DaytonaRecipe(snapshot="custom")
            state = await provider.create(recipe, environment_id="env-fixture", operation_id="op-create")
            # Supply the existing target's compatible execution identity and opt in.
            recipe = DaytonaRecipe(snapshot="custom", desktop=True, desktop_assistance=True)
            assert state is not None
            state = state.model_copy(update={"state": {**state.state, "configuration_fingerprint": recipe.fingerprint}})
            cloud.target["labels"]["a13n_configuration"] = recipe.fingerprint
            before = len(cloud.calls)

            async def call(request):
                return await provider.desktop_access(
                    recipe, environment_id="env-fixture", state=state, operation_id="op-access", request=request
                )

            if fault in {"missing", "unsafe_url", "denied_preview"}:
                with pytest.raises(EnvironmentProviderError):
                    await call(access.Describe())
            else:
                description = await call(access.Describe())
                assert isinstance(description, access.Description)
                assert description.websocket_url == "wss://6081-port-scoped.example/websockify"
                assert "port-scoped" not in repr(description)
                request = access.Inspect(operation="revoke", generation=generation, id="dgrant_test", epoch=1)
                before_revoke = len(cloud.calls)
                if fault == "uncertain_revoke":
                    with pytest.raises(EnvironmentProviderError) as error:
                        await call(request)
                    assert error.value.certainty.value == "unknown"
                else:
                    result = await call(request)
                    assert isinstance(result, access.AccessGrant) and result.state == "revoked"
                assert not any("preview" in path for _, path in cloud.calls[before_revoke:])
            assert not any(path.endswith("/start") for _, path in cloud.calls[before:])

    asyncio.run(scenario())
