"""Service owns release selection; guest jobs own idempotent install and restart."""

import os
import shlex
import subprocess
from unittest.mock import AsyncMock

import pytest
from a13n_harness.environment.errors import EnvironmentProviderError
from a13n_harness.providers.environment.docker.shared import _fingerprint as docker_fingerprint
from a13n_service.providers.environment import envd_policy, envd_upgrade
from a13n_service.providers.environment.recipes.daytona import DaytonaRecipe
from a13n_service.providers.environment.recipes.docker import DockerRecipe
from a13n_service.providers.environment.recipes.e2b import E2BRecipe
from a13n_service.providers.environment.recipes.modal import ModalRecipe
from a13n_service.providers.environment.recipes.sprites import SpritesRecipe
from a13n_service.providers.environment.recipes.vercel import VercelRecipe


@pytest.mark.parametrize(
    "model,automatic,custom",
    [
        (DockerRecipe, {"execution": "envd_http"}, {"execution": "envd_http", "image": "custom:1"}),
        (E2BRecipe, {"execution": "envd_http"}, {"execution": "envd_http", "template": "custom"}),
        (ModalRecipe, {}, {"image": "custom:1", "startup_command": ["/usr/local/bin/start-envd"]}),
        (DaytonaRecipe, {}, {"snapshot": "custom"}),
        (
            VercelRecipe,
            {"execution": "envd_http"},
            {"execution": "envd_http", "snapshot": "snap_custom", "startup_command": ["/usr/local/bin/start-envd"]},
        ),
        (SpritesRecipe, {"execution": "envd_http"}, {"execution": "native"}),
    ],
)
def test_management_defaults_are_frozen_and_explicit_policy_wins(model, automatic, custom):
    recipe = model.model_validate(automatic)
    assert envd_policy.managed(recipe)
    assert model.model_validate(recipe.model_dump()) == recipe
    assert not envd_policy.managed(model.model_validate(custom))
    if model in {DockerRecipe, SpritesRecipe}:
        assert not envd_policy.managed(model.model_validate({**automatic, "envd_management": "preinstalled"}))
    else:
        with pytest.raises(ValueError):
            model.model_validate({**automatic, "envd_management": "preinstalled"})
    if custom.get("execution") != "native":
        managed = model.model_validate(
            {
                **custom,
                **({"startup_command": None} if model in {ModalRecipe, VercelRecipe} else {}),
                "envd_management": "managed",
            }
        )
        preinstalled = model.model_validate({**custom, "envd_management": "preinstalled"})
        if isinstance(managed, DockerRecipe):
            assert docker_fingerprint(managed) == docker_fingerprint(preinstalled)
        else:
            assert managed.fingerprint == preinstalled.fingerprint


@pytest.mark.parametrize("model", [DockerRecipe, E2BRecipe, SpritesRecipe, VercelRecipe])
def test_native_execution_cannot_select_managed_envd(model):
    with pytest.raises(ValueError, match="Managed Envd requires"):
        model.model_validate({"execution": "native", "envd_management": "managed"})


def test_preinstalled_sprites_requires_http_and_keeps_operator_owned_boot():
    recipe = SpritesRecipe(execution="envd_http", envd_management="preinstalled")
    assert not envd_policy.managed(recipe)
    with pytest.raises(ValueError, match="Preinstalled Sprites requires envd_http"):
        SpritesRecipe(execution="envd_ws", envd_management="preinstalled")
    assert envd_policy.managed(SpritesRecipe(execution="envd_ws"))


@pytest.mark.parametrize(
    "applied,required,selected",
    [(None, True, "0.8.0"), ("0.7.0", True, "0.8.0"), ("0.8.0", False, "0.8.0"), ("0.9.0", False, "0.9.0")],
)
def test_release_selection_never_automatically_downgrades(applied, required, selected, monkeypatch):
    monkeypatch.setattr(envd_policy, "VERSION", "0.8.0")
    assert envd_policy.update_required(applied) is required
    assert envd_policy.selected_version(applied) == selected


@pytest.fixture
def guest(tmp_path, monkeypatch):
    binary = tmp_path / "a13n-envd"
    binary.write_text(
        '#!/bin/sh\nif [ "$1" = --version ]; then echo "a13n-envd $(cat version)"; else printf "%s\\n" "$*" >> restarts; fi\n'
    )
    binary.chmod(0o755)
    (tmp_path / "version").write_text("0.7.0")
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    monkeypatch.setattr(
        envd_upgrade,
        "installation_command",
        lambda *, version: f"printf %s {shlex.quote(version)} > version; printf installed >> installs",
    )
    return tmp_path


def run_script(guest, restart=None):
    return subprocess.run(
        ["sh", "-c", envd_upgrade.script("0.8.0", restart=restart)],
        cwd=guest,
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_native_upgrade_installs_once_and_rejoins_version_conditional_restart(guest):
    for _ in range(2):
        result = run_script(guest)
        assert result.returncode == 0, result.stderr
    assert (guest / "installs").read_text() == "installed"
    assert (guest / "restarts").read_text().splitlines() == [
        "restart --runtime-dir /run/a13n-envd-state --version 0.8.0"
    ] * 2


def test_failed_install_never_restarts_and_retains_retry_marker(guest, monkeypatch):
    monkeypatch.setattr(envd_upgrade, "installation_command", lambda *, version: "false")
    assert run_script(guest).returncode != 0
    assert (guest / "version").read_text() == "0.7.0"
    assert not (guest / "restarts").exists()
    assert (guest / "restart-needed").exists()


def test_external_manager_restart_is_retried_after_install_without_reinstalling(guest):
    (guest / "fail-restart").touch()
    restart = ["sh", "-c", "test ! -e fail-restart && printf restarted >> manager"]
    assert run_script(guest, restart).returncode != 0
    assert (guest / "restart-needed").exists()
    (guest / "fail-restart").unlink()
    assert run_script(guest, restart).returncode == 0
    assert run_script(guest, restart).returncode == 0
    assert (guest / "installs").read_text() == "installed"
    assert (guest / "manager").read_text() == "restarted"
    assert not (guest / "restart-needed").exists()
    assert not (guest / "restarts").exists()


@pytest.mark.anyio
@pytest.mark.parametrize("status,expected", [("ready", True), ("running", False), ("failed", None), ("conflict", None)])
async def test_upgrade_rejoins_the_existing_job_and_classifies_completion(status, expected):
    execute = AsyncMock(return_value=status)
    if expected is None:
        with pytest.raises(EnvironmentProviderError) as error:
            await envd_upgrade.upgrade(execute, provider_key="test", version="0.8.0", operation_id="envoper-fixed")
        assert error.value.code == "envd_upgrade_failed"
    else:
        assert (
            await envd_upgrade.upgrade(execute, provider_key="test", version="0.8.0", operation_id="envoper-fixed")
            is expected
        )
    argv = execute.call_args.args[0]
    assert argv[:2] == ["sh", "-c"]
    assert "/var/lib/a13n-envd/update-0.8.0" in argv[2]
    assert "envoper-fixed" in argv[2]
    assert "flock -n 9" in argv[2]


@pytest.mark.anyio
@pytest.mark.parametrize("method", ["create", "start"])
@pytest.mark.parametrize("pending", [None, "target", "upgrade"])
async def test_backend_upgrade_hook_precedes_final_readiness_and_preserves_pending(monkeypatch, method, pending):
    from a13n_harness.environment.models import EnvironmentState
    from a13n_service.providers.environment._backend import ManagementBackend
    from a13n_service.providers.environment._backend_factory import BackendProvider, ManagementBackendFactory
    from a13n_service.providers.environment.management import Pending
    from pydantic import BaseModel

    events = []
    observed = EnvironmentState(provider_key="test", state_version="1", state={"id": "owned-target"})

    class Target(ManagementBackend):
        provider_key = "test"
        environment_id = "env-test"

        def __init__(self, **kwargs):
            super().__init__(observed)

        async def create(self):
            assert self.defer_envd_readiness
            events.append("target")
            return Pending(self.state, settled=True) if pending == "target" else None

        async def start(self):
            return await self.create()

        async def update_envd(self, version, operation_id):
            assert not self.defer_envd_readiness
            events.append((version, operation_id))
            return Pending(self.state, settled=True) if pending == "upgrade" else None

        async def close(self):
            events.append("close")

    async def ready(target, operation_id):
        events.append("ready")

    class Recipe(BaseModel):
        pass

    factory = ManagementBackendFactory(key="test", environment_model=Recipe, management=Target)
    provider = BackendProvider(factory, None, None)
    provider.configure_envd_version("0.8.0")
    monkeypatch.setattr(provider, "_wait_ready", ready)
    result = await getattr(provider, method)({}, environment_id="env-test", operation_id="envoper-test", state=None)
    assert isinstance(result, Pending) is (pending is not None)
    expected = ["target"]
    if pending != "target":
        expected.append(("0.8.0", "envoper-test"))
    if pending is None:
        expected.append("ready")
        assert result == observed
    assert events == [*expected, "close"]


@pytest.mark.parametrize(
    "fields",
    [
        {"execution": "envd_http", "template": "auto", "desktop": True},
        {"execution": "envd_http", "template": "custom", "preparation": "headless"},
    ],
)
def test_preinstalled_e2b_cannot_infer_or_select_installation(fields):
    with pytest.raises(ValueError):
        E2BRecipe(**fields, envd_management="preinstalled")
    custom = E2BRecipe(execution="envd_http", template="custom", envd_management="preinstalled")
    assert custom.preparation == "preinstalled"


@pytest.mark.anyio
async def test_failed_guest_upgrade_requires_a_new_explicit_attempt(tmp_path, monkeypatch):
    import anyio
    from a13n_service.providers.environment.envd_install import dispatch

    root = tmp_path / "upgrade"
    content = "set -eu; test -f repaired; printf done > applied"
    monkeypatch.setattr(envd_upgrade, "script", lambda *args, **kwargs: content)
    monkeypatch.setattr(
        envd_upgrade,
        "dispatch",
        lambda script, revision, operation, *, root: dispatch(
            script,
            revision,
            operation,
            root=str(tmp_path / "upgrade"),
            timeout_seconds=5,
        ),
    )

    async def execute(argv):
        result = subprocess.run(argv, capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stderr
        return result.stdout

    async def finish(operation):
        with anyio.fail_after(10):
            while not await envd_upgrade.upgrade(execute, provider_key="test", version="0.8.0", operation_id=operation):
                await anyio.sleep(0.01)

    with pytest.raises(EnvironmentProviderError) as error:
        await finish("envoper-failed")
    assert error.value.code == "envd_upgrade_failed"
    (root / "repaired").touch()
    with pytest.raises(EnvironmentProviderError):
        await finish("envoper-failed")
    await finish("envoper-explicit-retry")
    assert (root / "applied").read_text() == "done"
    assert (root / "attempt").read_text() == "envoper-explicit-retry"
