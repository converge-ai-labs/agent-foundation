"""Sprites installation, identity and desktop authority are independent of native execution."""

import asyncio
import base64
import hashlib
import json
import sys
from unittest.mock import AsyncMock

import httpx2
import pytest
from a13n_harness.environment.errors import EnvironmentProviderError
from a13n_harness.environment.models import EnvironmentState
from a13n_harness.providers.environment.native.configuration import TokenCredential
from a13n_harness.providers.environment.native.environment import NativeRuntime
from a13n_harness.providers.environment.native.http import NativeHTTP
from a13n_harness.providers.environment.sprites.shared import (
    SpriteInfo,
    SpritesConnectionConfiguration,
    SpritesEnvironmentConfiguration,
    SpritesTargetState,
)
from a13n_service.providers.environment import desktop_access as access
from a13n_service.providers.environment.recipes.sprites import SpritesRecipe
from a13n_service.providers.environments.sprites import management
from a13n_service.providers.environments.sprites.desktop_install import _LAUNCH, revision
from a13n_service.providers.environments.sprites.management import SpritesManagement

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def manager(*, phase="ready", operation="op-first", token="fixture-secret", desktop=True):
    recipe = SpritesRecipe(desktop=desktop, execution="envd_http")
    state = EnvironmentState(
        provider_key="sprites",
        state_version="1",
        state=SpritesTargetState(
            target_id="a13n-fixture",
            backing_id="native-1",
            environment_id="env-fixture",
            configuration_fingerprint=recipe.fingerprint,
            desktop_phase=phase,
            install_revision=revision("desktop" if desktop else "headless"),
        ).model_dump(mode="json"),
    )
    return SpritesManagement(
        recipe,
        "env-fixture",
        state,
        NativeRuntime(SpritesConnectionConfiguration(organization="fixture"), TokenCredential(api_key=token)),
        operation,
    )


def install_transport(monkeypatch, handler):
    def init(self, key, url, token, timeout, **kwargs):
        self.key = key
        self.client = httpx2.AsyncClient(
            base_url=url, headers={"Authorization": "Bearer " + token}, transport=httpx2.MockTransport(handler)
        )

    monkeypatch.setattr(NativeHTTP, "__init__", init)


def sprite(manage):
    return {
        "id": "native-1",
        "name": "a13n-fixture",
        "status": "warm",
        "labels": [f"{k}={v}" for k, v in manage.labels.items()],
        "url": "https://a13n-fixture-xyz.sprites.app",
    }


async def test_native_identity_and_automatic_desktop_defaults():
    config = SpritesEnvironmentConfiguration()
    previous = config.model_dump_json(exclude={"execution", "desktop", "environment"})
    assert config.fingerprint == hashlib.sha256(previous.encode()).hexdigest()
    assert SpritesRecipe().fingerprint == config.fingerprint
    recipe = SpritesRecipe(desktop=True)
    assert recipe.execution == "envd_http" and recipe.desktop_assistance
    assert (
        recipe.fingerprint
        == SpritesEnvironmentConfiguration.model_validate(
            recipe.model_dump(exclude={"desktop_assistance", "envd_management"})
        ).fingerprint
    )
    assert not SpritesRecipe(execution="envd_http").desktop
    assert not SpritesRecipe(execution="envd_ws").desktop
    for fields in ({"desktop_assistance": True}, {"desktop": True, "execution": "native"}):
        with pytest.raises(ValueError):
            SpritesRecipe(**fields)


@pytest.mark.parametrize("fault", ["owner", "replacement", "absent"])
async def test_access_validates_before_commands(monkeypatch, fault):
    manage = manager()
    calls = []

    def handler(request):
        calls.append(request.method)
        data = sprite(manage)
        if fault == "owner":
            data["labels"] = []
        if fault == "replacement":
            data["id"] = "native-other"
        return httpx2.Response(404) if fault == "absent" else httpx2.Response(200, json=data)

    install_transport(monkeypatch, handler)

    async def forbidden(*args, **kwargs):
        pytest.fail("must not command an unvalidated Sprite")

    monkeypatch.setattr(management, "command", forbidden)
    try:
        with pytest.raises(EnvironmentProviderError):
            await manage.desktop_access(access.Describe())
        assert calls == ["GET"]
    finally:
        await manage.close()


@pytest.mark.parametrize(
    "url",
    [
        "http://x.sprites.app",
        "https://x.sprites.app.evil.test",
        "https://u:p@x.sprites.app",
        "https://x.sprites.app/?token=x",
        "https://x.sprites.app:8443",
        "https://x.sprites.app:invalid",
        "https://[bad",
    ],
)
async def test_route_validation(url):
    manage = manager()
    manage._info = SpriteInfo.model_validate({**sprite(manage), "url": url})
    with pytest.raises(EnvironmentProviderError, match="Native environment"):
        manage.desktop_endpoint()


async def test_pending_installation_retains_identity_and_never_allocates(monkeypatch):
    manage = manager(phase="installing")
    calls = []

    def handler(request):
        calls.append(request.method)
        return httpx2.Response(200, json=sprite(manage))

    install_transport(monkeypatch, handler)

    async def installing(reference, operation):
        assert reference.target.backing_id == "native-1" and operation == "op-first"
        return "running"

    monkeypatch.setattr(management, "install", installing)
    try:
        with pytest.raises(EnvironmentProviderError) as error:
            await manage.create()
        assert error.value.code == "sprites_desktop_pending"
        assert manage.target.desktop_phase == "installing"
        assert calls == ["GET"]
    finally:
        await manage.close()


@pytest.mark.parametrize("desktop", [False, True])
async def test_readiness_uses_authenticated_eip_and_native_service(monkeypatch, desktop):
    manage = manager(desktop=desktop)
    manage.recipe = manage.recipe.model_copy(update={"desktop_assistance": False})
    wait = AsyncMock()
    commands = AsyncMock()
    install_transport(monkeypatch, lambda request: httpx2.Response(404))
    monkeypatch.setattr(management, "command", commands)
    monkeypatch.setattr(management, "wait_until_ready", wait)
    try:
        await manage._ready()
        wait.assert_awaited_once()
        assert wait.call_args.kwargs["desktop"] is desktop
        script = commands.call_args_list[0].kwargs["stdin"]
        assert ("a13n-sandbox-desktop" in script) is desktop
        assert "python3" not in script
        assert "a13n-envd daemon" in script and "supervise" not in script
        args = commands.call_args_list[1].args[1]
        assert args[3] == ("a13n-desktop" if desktop else "a13n-envd")
        assert args[args.index("--http-port") + 1] == ("6081" if desktop else "8888")
        assert commands.await_count == 2
    finally:
        await manage.close()


async def test_credentials_are_instance_scoped(monkeypatch):
    first, second = manager(token="first-private"), manager(token="second-private")
    observed = []

    def handler(request):
        observed.append(request.headers["authorization"])
        return httpx2.Response(200, json=sprite(first))

    install_transport(monkeypatch, handler)
    try:
        await first.lookup()
        await second.lookup()
        assert observed == ["Bearer first-private", "Bearer second-private"]
        assert first.http is not second.http
    finally:
        await first.close()
        await second.close()


async def test_installer_rejoins_live_child_and_retries_only_explicit_failure(tmp_path):
    # A fixture timeout executable keeps this test portable without running package installation.
    root = tmp_path / "install"
    # macOS lacks GNU timeout; use a test script that preserves the real launcher/locking behavior.
    code = _LAUNCH.replace("timeout --kill-after=10 900 sh", "sh").replace("/var/lib/a13n-sprites-install", str(root))

    async def run(script, attempt):
        payload = json.dumps([base64.b64encode(script.encode()).decode(), "revision", attempt])
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-I", "-c", code, payload, stdout=asyncio.subprocess.PIPE
        )
        output, _ = await process.communicate()
        assert process.returncode == 0
        return output.decode().strip()

    assert await run("sleep .2; exit 1", "first") == "running"
    assert await run("exit 0", "first") == "running"
    async with asyncio.timeout(5):
        while (root / "status").read_text() != "failed":
            await asyncio.sleep(0.02)
    assert await run("exit 0", "first") == "failed"
    assert await run("exit 0", "retry") == "running"
    async with asyncio.timeout(5):
        while (root / "status").read_text() != "ready":
            await asyncio.sleep(0.02)
    assert await run("exit 1", "later") == "ready"


async def test_headless_installer_has_no_desktop_or_python_dependency():
    from a13n_service.providers.environments.sprites.desktop_install import installation_script

    headless = installation_script("headless")
    for dependency in ("python3", "xvfb", "google-chrome", "desktop.py", "desktop_guest.py", "init-skills.sh"):
        assert dependency not in headless
        assert dependency in installation_script("desktop")
    assert revision("headless") != revision("desktop")


async def test_headless_shell_job_rejoins_and_only_explicitly_retries(tmp_path, monkeypatch):
    from a13n_service.providers.environments.sprites import desktop_install as install

    root = tmp_path / "job"
    script = "sleep .2; exit 1"
    monkeypatch.setattr(install, "installation_script", lambda _: script)

    async def dispatch(attempt):
        process = await asyncio.create_subprocess_exec(
            "sh", "-c", install.headless_dispatch(attempt, root=str(root)), stdout=asyncio.subprocess.PIPE
        )
        output, _ = await process.communicate()
        assert process.returncode == 0
        return output.decode().strip()

    assert await dispatch("first") == "running"
    assert await dispatch("first") == "running"
    async with asyncio.timeout(5):
        while (root / "status").read_text() != "failed":
            await asyncio.sleep(0.02)
    assert await dispatch("first") == "failed"
    assert await dispatch("retry") == "running"
    async with asyncio.timeout(5):
        while (root / "status").read_text() != "failed":
            await asyncio.sleep(0.02)
    assert (root / "attempt").read_text() == "retry"
    assert (root / "output").exists()
    # A different frozen script cannot replace this target's preparation.
    script = "exit 0"
    assert await dispatch("other-revision") == "conflict"


async def test_completed_installation_survives_a_new_service_preparation_revision(monkeypatch):
    manage = manager()
    monkeypatch.setattr(management, "revision", lambda profile: "a-new-build-revision")
    monkeypatch.setattr(manage, "lookup", AsyncMock(return_value=SpriteInfo.model_validate(sprite(manage))))
    ready = AsyncMock()
    monkeypatch.setattr(manage, "_ready", ready)
    monkeypatch.setattr(management, "install", AsyncMock(side_effect=AssertionError("must not reinstall")))
    try:
        await manage.create()
        ready.assert_awaited_once()
    finally:
        await manage.close()


async def test_preinstalled_create_and_start_only_check_readiness(monkeypatch):
    manage = manager(desktop=False)
    manage.recipe = manage.config = SpritesRecipe(execution="envd_http", envd_management="preinstalled")
    lookup = AsyncMock(return_value=SpriteInfo.model_validate(sprite(manage)))
    install = AsyncMock(side_effect=AssertionError("installed preinstalled runtime"))
    command = AsyncMock(side_effect=AssertionError("changed preinstalled startup"))
    ready = AsyncMock()
    monkeypatch.setattr(manage, "lookup", lookup)
    monkeypatch.setattr(management, "install", install)
    monkeypatch.setattr(management, "command", command)
    monkeypatch.setattr(management, "wait_until_ready", ready)
    await manage.create()
    await manage.start()
    assert ready.await_count == 2
    install.assert_not_awaited()
    command.assert_not_awaited()
