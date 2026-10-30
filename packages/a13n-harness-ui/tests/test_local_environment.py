from unittest.mock import AsyncMock

import pytest
import yaml
from a13n_harness.environment.models import COMPUTER_ACTIONS
from a13n_harness.providers.environment.local_envd.configuration import local_envd_path
from a13n_harness_ui import setup
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.composition import ResolvedEnvironmentProfile
from a13n_harness_ui.environment_profiles import BUILT_IN_ENVIRONMENT_PROFILES, environment_profile_id_for_mode
from a13n_harness_ui.environment_runtime import _local_actions
from a13n_harness_ui.errors import RuntimeResolutionError
from a13n_harness_ui.extensions.environment_adapters import LocalEnvdProfileConfiguration
from a13n_harness_ui.managed_runtime import ManagedEnvdRuntime
from a13n_harness_ui.settings import EnvdRuntimeSettings, HarnessUiSettings, StorageSettings

pytestmark = pytest.mark.anyio


def test_local_profiles_preserve_direct_default_and_explicit_desktop():
    assert environment_profile_id_for_mode("full-control") == "environment-native"
    for item in BUILT_IN_ENVIRONMENT_PROFILES:
        profile = ResolvedEnvironmentProfile(
            profile_id=item.profile_id,
            behavior_digest="0" * 64,
            provider_key=item.provider_key,
            provider_configuration=item.provider_configuration,
            adapter_key=item.adapter_key,
            adapter_configuration={},
        )
        assert bool(_local_actions(profile) & COMPUTER_ACTIONS) == (item.profile_id == "environment-desktop")
        if item.profile_id in {"environment-envd", "environment-desktop"}:
            launch = LocalEnvdProfileConfiguration.model_validate(item.provider_configuration).launch
            assert launch.full_control and launch.sandbox.mode == "disabled"
            assert launch.computer_use == (item.profile_id == "environment-desktop")


async def test_runtime_inspection_never_downloads_or_executes(tmp_path, monkeypatch):
    from a13n_harness_ui import managed_runtime

    def forbidden(*args, **kwargs):
        pytest.fail("Status must be inert")

    monkeypatch.setattr(managed_runtime, "_matches_version", forbidden)
    monkeypatch.setattr(managed_runtime, "_download_archive", forbidden)
    runtime = ManagedEnvdRuntime(
        cache_root=tmp_path / "cache",
        staging_root=tmp_path / "staging",
        settings=EnvdRuntimeSettings(),
        version="1.2.3",
    )
    status = await runtime.status()
    assert status.required_version == "1.2.3" and not status.installed
    assert status.verified_version is None and status.phase == "idle"
    assert not (tmp_path / "cache").exists()


async def test_envd_preflight_does_not_request_desktop_unless_selected(tmp_path, monkeypatch):
    resolve = AsyncMock(return_value=tmp_path / "envd")
    probe = AsyncMock(return_value=None)
    monkeypatch.setattr(setup, "validate_local_envd_runtime", probe)
    assert (await setup.preflight_environment("environment-envd", tmp_path, resolve_executable=resolve)).ready
    probe.assert_awaited_once_with(tmp_path / "envd", tmp_path, computer_use=False)


async def test_configure_failure_preserves_default_and_unrelated_fields(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    original = (
        'schema_version: "1"\ndefaults: {environment_profile: environment-native}\ntools: {enable_codeact: false}\n'
    )
    path.write_text(original)
    settings = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"))
    async with open_harness_ui_app(settings, configuration_path=path) as app:
        app._resolve_sandbox_executable = AsyncMock(side_effect=RuntimeResolutionError("Offline", code="offline"))
        result = await app.configure_local_environment("environment-envd")
        assert not result.ready and path.read_text() == original
        result = await app.configure_local_environment("environment-native")
        assert result.ready
        assert yaml.safe_load(path.read_text())["tools"] == {"enable_codeact": False}
        app._resolve_sandbox_executable.assert_awaited_once()


async def test_envd_check_reports_desktop_readiness_without_claiming_execution_failure(tmp_path, monkeypatch):
    from a13n_harness.environment.computer import ComputerDescription

    monkeypatch.setattr(
        setup,
        "validate_local_envd_runtime",
        AsyncMock(return_value=ComputerDescription(targets=(), observe_ready=True, input_ready=False, scroll_units=())),
    )
    result = await setup.preflight_environment(
        "environment-desktop", tmp_path, resolve_executable=AsyncMock(return_value=tmp_path / "envd")
    )
    assert not result.ready and result.observe_ready and result.input_ready is False


def _profile(profile_id):
    from a13n_harness_ui.environment_profiles import built_in_environment_profile

    item = built_in_environment_profile(profile_id)
    assert item is not None
    return ResolvedEnvironmentProfile(
        profile_id=profile_id,
        behavior_digest="0" * 64,
        provider_key=item.provider_key,
        adapter_key=item.adapter_key,
        provider_configuration=item.provider_configuration,
    )


async def test_manager_shares_full_control_but_never_merges_sandbox_grants(tmp_path, monkeypatch):
    import sys

    from a13n_harness_ui.envd_runtime import EnvdRuntimeManager

    manager = EnvdRuntimeManager(settings=EnvdRuntimeSettings(), runtime_parent=tmp_path / "runtimes")
    monkeypatch.setattr(manager, "resolve_executable", AsyncMock(return_value=tmp_path / "a13n-envd"))
    roots = (tmp_path / "first", tmp_path / "second")
    for root in roots:
        root.mkdir()
    try:
        first = await manager.runtime((roots[0],), profile=_profile("environment-envd"))
        second = await manager.runtime((roots[1],), profile=_profile("environment-envd"))
        assert first is second
        assert first.running_device() is None
        if sys.platform in {"linux", "darwin", "win32"}:
            first_sandbox = await manager.runtime((roots[0],), profile=_profile("environment-sandbox"))
            second_sandbox = await manager.runtime((roots[1],), profile=_profile("environment-sandbox"))
            assert first_sandbox is not second_sandbox
            assert [grant.path for grant in first_sandbox.configuration.sandbox.grants] == [local_envd_path(roots[0])]
            assert [grant.path for grant in second_sandbox.configuration.sandbox.grants] == [local_envd_path(roots[1])]
    finally:
        await manager.close()
    assert not (await manager.status()).instances


async def test_desktop_requires_host_opt_in_before_resolving_executable(tmp_path, monkeypatch):
    from a13n_harness_ui.envd_runtime import EnvdRuntimeManager
    from a13n_harness_ui.errors import EnvironmentLifecycleError

    manager = EnvdRuntimeManager(settings=EnvdRuntimeSettings(), runtime_parent=tmp_path / "runtimes")
    resolve = AsyncMock()
    monkeypatch.setattr(manager, "resolve_executable", resolve)
    with pytest.raises(EnvironmentLifecycleError, match="Enable Computer Use"):
        await manager.runtime((tmp_path,), profile=_profile("environment-desktop"))
    resolve.assert_not_awaited()
    assert not (tmp_path / "runtimes").exists()


async def test_envd_configuration_is_captured_on_app_start_without_launch(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text('schema_version: "1"\nenvd: {executable: null, computer_use: true}\n')
    settings = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"), pricing_auto_update=False)
    async with open_harness_ui_app(settings, configuration_path=path) as app:
        status = await app.envd_runtime_status()
        assert status.computer_use and not status.instances
        assert not status.restart_required
        assert not (tmp_path / "data" / "runtimes" / "host").exists()


async def test_explicit_start_keeps_app_owned_runtime_and_stop_clears_it(tmp_path, monkeypatch):
    from a13n_harness_ui import envd_runtime

    manager = envd_runtime.EnvdRuntimeManager(settings=EnvdRuntimeSettings(), runtime_parent=tmp_path / "runtimes")
    monkeypatch.setattr(manager, "resolve_executable", AsyncMock(return_value=tmp_path / "a13n-envd"))
    probe = AsyncMock(return_value=None)
    monkeypatch.setattr(envd_runtime, "probe_local_envd_runtime", probe)
    owner = await manager.runtime((tmp_path,), profile=_profile("environment-envd"))
    device = AsyncMock()
    device.is_closed = False
    acquire = AsyncMock(return_value=device)
    monkeypatch.setattr(owner, "acquire_device", acquire)
    checked = await manager.start("environment-envd", tmp_path)
    acquire.assert_awaited_once()
    assert checked.ready
    owner = probe.call_args.args[0]
    assert owner is await manager.runtime((tmp_path,), profile=_profile("environment-envd"))
    close = AsyncMock(wraps=owner.close)
    monkeypatch.setattr(owner, "close", close)
    assert (await manager.status()).readiness == (checked,)
    await manager.stop()
    close.assert_awaited_once()
    status = await manager.status()
    assert status.instances == () and status.readiness == ()
    assert await manager.runtime((tmp_path,), profile=_profile("environment-envd")) is not owner
    await manager.close()


async def test_explicit_start_replaces_only_a_disconnected_owner(tmp_path, monkeypatch):
    from a13n_harness_ui import envd_runtime

    manager = envd_runtime.EnvdRuntimeManager(settings=EnvdRuntimeSettings(), runtime_parent=tmp_path / "runtimes")
    monkeypatch.setattr(manager, "resolve_executable", AsyncMock(return_value=tmp_path / "a13n-envd"))
    owner = await manager.runtime((tmp_path,), profile=_profile("environment-envd"))
    device = AsyncMock()
    device.is_closed = True
    monkeypatch.setattr(owner, "acquire_device", AsyncMock(return_value=device))
    close = AsyncMock(wraps=owner.close)
    monkeypatch.setattr(owner, "close", close)
    probe = AsyncMock(return_value=None)
    monkeypatch.setattr(envd_runtime, "probe_local_envd_runtime", probe)
    try:
        assert (await manager.start("environment-envd", tmp_path)).ready
        replacement = probe.call_args.args[0]
        assert replacement is not owner
        assert replacement is await manager.runtime((tmp_path,), profile=_profile("environment-envd"))
        close.assert_awaited_once()
    finally:
        await manager.close()


async def test_restart_replaces_owned_runtime_and_clears_old_readiness(tmp_path, monkeypatch):
    from a13n_harness_ui import envd_runtime

    manager = envd_runtime.EnvdRuntimeManager(settings=EnvdRuntimeSettings(), runtime_parent=tmp_path / "runtimes")
    monkeypatch.setattr(manager, "resolve_executable", AsyncMock(return_value=tmp_path / "a13n-envd"))
    device = AsyncMock()
    device.is_closed = False
    monkeypatch.setattr(envd_runtime.LocalEnvdProviderRuntime, "acquire_device", AsyncMock(return_value=device))
    monkeypatch.setattr(envd_runtime, "probe_local_envd_runtime", AsyncMock(return_value=None))
    try:
        assert (await manager.start("environment-envd", tmp_path)).ready
        owner = await manager.runtime((tmp_path,), profile=_profile("environment-envd"))
        close = AsyncMock(wraps=owner.close)
        monkeypatch.setattr(owner, "close", close)
        assert (await manager.start("environment-envd", tmp_path, restart=True)).ready
        close.assert_awaited_once()
        assert await manager.runtime((tmp_path,), profile=_profile("environment-envd")) is not owner
        assert len((await manager.status()).readiness) == 1
    finally:
        await manager.close()


async def test_custom_reinstall_fails_before_stopping_and_download(tmp_path, monkeypatch):
    from a13n_harness_ui.envd_runtime import EnvdRuntimeManager

    manager = EnvdRuntimeManager(
        settings=EnvdRuntimeSettings(executable=tmp_path / "a13n-envd"), runtime_parent=tmp_path / "runtimes"
    )
    stop = AsyncMock()
    monkeypatch.setattr(manager, "_stop_owned", stop)
    result = await manager.start("environment-envd", tmp_path, reinstall=True)
    assert not result.ready and "Custom executables" in result.message
    stop.assert_not_awaited()
    assert not (await manager.status()).preparing


async def test_pending_configuration_blocks_restart_without_stopping(tmp_path, monkeypatch):
    from a13n_harness_ui.errors import EnvironmentLifecycleError

    path = tmp_path / "config.yaml"
    path.write_text('schema_version: "1"\n')
    settings = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"), pricing_auto_update=False)
    async with open_harness_ui_app(settings, configuration_path=path) as app:
        start = AsyncMock()
        monkeypatch.setattr(app._envd, "start", start)
        path.write_text('schema_version: "1"\nenvd: {computer_use: true}\n')
        await app.reload_configuration()
        with pytest.raises(EnvironmentLifecycleError, match="Restart Harness UI"):
            await app.restart_envd()
        start.assert_not_awaited()


@pytest.mark.parametrize("endpoint", ["restart", "reinstall"])
async def test_runtime_mutations_require_authentication_and_confirmation(tmp_path, monkeypatch, endpoint):
    import httpx
    from a13n_harness_ui.setup import EnvironmentReadiness
    from a13n_harness_ui.webui import create_webui

    path = tmp_path / "config.yaml"
    path.write_text('schema_version: "1"\n')
    settings = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"), pricing_auto_update=False)
    async with open_harness_ui_app(settings, configuration_path=path) as app:
        restart = AsyncMock(
            return_value=EnvironmentReadiness(
                profile_id="environment-envd", ready=True, code="envd_ready", message="Runtime ready"
            )
        )
        monkeypatch.setattr(app, "restart_envd", restart)
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def factory():
            yield app

        server = create_webui(factory, api_key="test-only-key")
        async with server.router.lifespan_context(server):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=server), base_url="http://127.0.0.1"
            ) as client:
                url = f"/api/envd/{endpoint}"
                assert (await client.post(url, json={"confirm": True})).status_code >= 400
                headers = {"Authorization": "Bearer test-only-key"}
                for body in ({}, {"confirm": False}):
                    assert (await client.post(url, json=body, headers=headers)).status_code >= 400
                restart.assert_not_awaited()
                response = await client.post(url, json={"confirm": True}, headers=headers)
                assert response.status_code == 200, response.text
                assert response.json()["ready"]
                restart.assert_awaited_once_with(**({"reinstall": True} if endpoint == "reinstall" else {}))
