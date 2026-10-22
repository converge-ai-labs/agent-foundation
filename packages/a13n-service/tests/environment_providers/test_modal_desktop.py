"""Modal readiness, desktop assistance and snapshot-retirement boundaries."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from a13n_harness.environment.errors import EnvironmentProviderError
from a13n_harness.environment.models import EnvironmentState
from a13n_harness.providers.environment.modal.shared import ModalConnectionConfiguration, ModalCredential
from a13n_harness.providers.environment.native.environment import NativeRuntime
from a13n_service.providers.environment import desktop_access as access
from a13n_service.providers.environments.modal import management as module
from a13n_service.providers.environments.modal.configuration import ModalRecipe


def management(*, assistance=True, snapshot=False):
    recipe = ModalRecipe(image="im-envd", desktop=True, desktop_assistance=assistance)
    state = EnvironmentState(
        provider_key="modal",
        state_version="1",
        state={
            "target_id": "sb-fixed",
            "environment_id": "env-fixed",
            "configuration_fingerprint": recipe.fingerprint,
            "snapshot_id": "im-snapshot" if snapshot else None,
            "snapshot_source_id": "sb-previous" if snapshot else None,
        },
    )
    return module.ModalManagement(
        recipe,
        "env-fixed",
        state,
        NativeRuntime(
            ModalConnectionConfiguration(workspace="test", app_name="test"),
            ModalCredential(token_id="id", token_secret="secret"),
        ),
        "op-test",
    )


def test_assistance_requires_desktop():
    with pytest.raises(ValueError):
        ModalRecipe(image="im-envd", desktop_assistance=True)


@pytest.mark.anyio
async def test_desktop_description_exposes_only_supervisor_route(monkeypatch):
    target = SimpleNamespace(
        tunnels=SimpleNamespace(aio=AsyncMock(return_value={6081: SimpleNamespace(url="https://desktop.example/")}))
    )
    monkeypatch.setattr(module.ModalManagement, "lookup", AsyncMock(return_value=target))
    command = AsyncMock(
        return_value=access.Envelope(
            result=access.Description(
                protocol_version=1,
                generation="a" * 32,
                port=6081,
                max_ttl_seconds=60,
                read_only_enforced=True,
                revocation_fences_grants=True,
            )
        ).model_dump_json()
    )
    monkeypatch.setattr(module, "execute_command", command)
    result = await management().desktop_access(access.Describe())
    assert isinstance(result, access.Description)
    assert result.websocket_url == "wss://desktop.example/websockify"
    assert command.call_args.args[1] == access.command(access.Describe())
    target.tunnels.aio.return_value[6081].url = "http://desktop.example"
    with pytest.raises(EnvironmentProviderError):
        await management().desktop_access(access.Describe())


@pytest.mark.anyio
async def test_access_disabled_does_not_contact_target(monkeypatch):
    lookup = AsyncMock()
    monkeypatch.setattr(module.ModalManagement, "lookup", lookup)
    with pytest.raises(EnvironmentProviderError):
        await management(assistance=False).desktop_access(access.Describe())
    lookup.assert_not_awaited()


@pytest.mark.anyio
async def test_failed_readiness_keeps_resume_snapshot(monkeypatch):
    ready = AsyncMock(side_effect=TimeoutError)
    cleanup = AsyncMock()
    monkeypatch.setattr(module.ModalManagement, "_check_envd", ready)
    monkeypatch.setattr(module.ModalManagement, "cleanup_snapshots", cleanup)
    manager = management(snapshot=True)
    before = manager.state
    with pytest.raises(TimeoutError):
        await manager._finish_start(SimpleNamespace())
    assert manager.state == before
    cleanup.assert_not_awaited()


@pytest.mark.anyio
async def test_readiness_requires_actual_computer_and_closes_connection(monkeypatch):
    manager = management()
    manager.sandbox = SimpleNamespace()
    command = AsyncMock(return_value="")
    monkeypatch.setattr(module, "execute_command", command)
    computer = SimpleNamespace(
        describe=AsyncMock(
            side_effect=[
                SimpleNamespace(observe_ready=True, input_ready=False, targets=["display"]),
                SimpleNamespace(observe_ready=True, input_ready=True, targets=["display"]),
            ]
        )
    )
    execution = SimpleNamespace(
        open=AsyncMock(), check_ready=AsyncMock(), close=AsyncMock(), operations=SimpleNamespace(computer=computer)
    )
    monkeypatch.setattr(module, "http_execution", lambda reference: execution)
    await manager._check_envd()
    assert execution.open.await_count == execution.close.await_count == 2
    execution.check_ready.assert_awaited_with(frozenset({"computer"}))
    command.assert_awaited_once()


@pytest.mark.anyio
async def test_grant_revocation_uses_supervisor_protocol(monkeypatch):
    target = SimpleNamespace()
    monkeypatch.setattr(module.ModalManagement, "lookup", AsyncMock(return_value=target))
    request = access.Inspect(operation="revoke", generation="a" * 32, id="da_example", epoch=1)
    grant = access.AccessGrant(
        id=request.id, generation=request.generation, mode="control", epoch=1, state="revoked", remaining_seconds=0
    )
    command = AsyncMock(return_value=access.Envelope(result=grant).model_dump_json())
    monkeypatch.setattr(module, "execute_command", command)
    assert await management().desktop_access(request) == grant
    command.assert_awaited_once_with(target, access.command(request), 25)


def test_modal_aliases_share_target_lock_and_resume_gets_new_lock():
    from a13n_service.runs.desktop_assistance.admission import target_key

    manager = management()
    original = manager.state
    assert original is not None
    key = target_key(original)
    assert key is not None
    alias = original.model_copy(update={"state": {**original.state, "environment_id": "env-alias"}})
    assert target_key(alias) == key
    resumed = original.model_copy(update={"state": {**original.state, "target_id": "sb-resumed"}})
    assert target_key(resumed) != key


@pytest.mark.anyio
@pytest.mark.parametrize("operation", ["management", "execution"])
async def test_service_policy_denies_discovered_envd_before_reading_token(monkeypatch, operation):
    from a13n_harness.configuration import RunConfiguration
    from a13n_harness.providers.endpoint_policy import EndpointPolicy
    from a13n_harness.providers.environment.modal import http
    from a13n_harness.providers.environment.modal.shared import ModalReference
    from a13n_service.providers.environments.modal import MODAL
    from a13n_service.runs.environments import adapters

    manager = management()
    sandbox = SimpleNamespace(
        tunnels=SimpleNamespace(aio=AsyncMock(return_value={8888: SimpleNamespace(url="https://denied.example")})),
        detach=SimpleNamespace(aio=AsyncMock()),
    )
    monkeypatch.setattr(ModalReference, "lookup", AsyncMock(return_value=sandbox))
    monkeypatch.setattr(module, "execute_command", AsyncMock(return_value=""))
    token = AsyncMock(return_value="guest-secret")
    monkeypatch.setattr(http, "execute_command", token)
    configuration = RunConfiguration(allowed_hosts=frozenset({"allowed.example"}))
    runtime = SimpleNamespace(
        keys=None,
        endpoint_policy=EndpointPolicy(configuration=configuration),
        registry=SimpleNamespace(check_environment_endpoint=AsyncMock(), get=lambda *args: MODAL),
    )
    account = SimpleNamespace(
        type="modal",
        config={"workspace": "test", "app_name": "test"},
        reveal_credential=lambda keys: {"token_id": "id", "token_secret": "secret"},
    )
    target = adapters.Target("env-fixed", account, manager.recipe.model_dump(), manager.state)
    with pytest.raises(EnvironmentProviderError) as failure:
        if operation == "execution":
            connector = await adapters.execution_connector(runtime, target, configuration=configuration)
            await connector.open()
        else:
            provider = await adapters.open_provider(runtime, target)
            try:
                await provider.create(
                    target.recipe, environment_id=target.environment_id, operation_id="op-policy", state=target.state
                )
            finally:
                await provider.close()
    assert failure.value.code == "envd_endpoint_denied"
    token.assert_not_awaited()
    sandbox.detach.aio.assert_awaited()
