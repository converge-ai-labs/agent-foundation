"""Provider startup selects one native restart owner, independent of Session recovery."""

import shlex

import pytest
from a13n_service.providers.environment import envd_assets


@pytest.mark.parametrize("program", ["a13n-envd", "a13n-sandbox-desktop"])
@pytest.mark.parametrize("mode", ["daemon", "supervise"])
def test_startup_selects_explicit_native_mode_without_a_shell_supervisor(program, mode):
    expected = ["exec", "a13n-sandbox-entrypoint"]
    if program == "a13n-sandbox-desktop":
        expected.append(program)
    expected.extend(["a13n-envd", mode])
    command = envd_assets.startup(program, mode=mode)
    assert shlex.split(command) == expected
    assert "command -v" not in command
    assert "a13n-envd-supervise" not in command


@pytest.mark.parametrize("program", ["a13n-envd", "a13n-sandbox-desktop"])
def test_unmanaged_guest_defaults_to_native_supervision(program):
    assert envd_assets.startup(program) == envd_assets.startup(program, mode="supervise")


@pytest.mark.parametrize("profile", ["headless", "desktop"])
def test_guest_manifest_contains_public_assets_without_a_packaged_supervisor(profile):
    commands = envd_assets.commands(profile)
    assert len(commands) == len(envd_assets.files(profile))
    assert all("sha256sum -c" in command for command in commands)
    assert all("envd-supervise" not in command for command in commands)
    assert all("base64 -d" not in command for command in commands)
