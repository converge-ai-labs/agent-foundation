"""Provider recipes freeze the shared installer and profile assets into their identity."""

import hashlib
import re
import subprocess
from pathlib import Path

import pytest
from a13n_service.providers.environment import envd_assets, envd_release
from a13n_service.providers.environments.daytona import desktop_build as daytona
from a13n_service.providers.environments.e2b import desktop_build as e2b
from a13n_service.providers.environments.modal import desktop_build as modal
from a13n_service.providers.environments.sprites import desktop_install as sprites
from a13n_service.providers.environments.vercel import desktop_build as vercel

BUILDERS = [
    pytest.param(daytona, daytona.specification, id="daytona"),
    pytest.param(e2b, e2b.specification, id="e2b"),
    pytest.param(modal, modal.specification, id="modal"),
    pytest.param(sprites, sprites.installation_script, id="sprites"),
    pytest.param(vercel, vercel.specification, id="vercel"),
]


def test_shared_assets_match_committed_sources():
    root = Path(__file__).resolve().parents[4]
    assert re.fullmatch(r"[0-9a-f]{40}", envd_assets.SOURCE_REVISION)
    for source, _, checksum in envd_assets.files("desktop"):
        assert hashlib.sha256((root / source).read_bytes()).hexdigest() == checksum
    headless = envd_assets.commands("headless")
    assert len(headless) == 1
    assert "entrypoint.sh" in headless[0]
    assert all("python" not in command for command in headless)
    assert len(envd_assets.files("desktop")) == 4


@pytest.mark.parametrize(("builder", "render"), BUILDERS)
def test_recipe_installs_shared_assets(builder, render):
    manifest = str(render())
    for source, destination, checksum in envd_assets.files("desktop"):
        assert (
            f"https://raw.githubusercontent.com/converge-ai-labs/agent-foundation/{envd_assets.SOURCE_REVISION}/{source}"
            in manifest
        )
        assert f"{checksum}  {destination}" in manifest


@pytest.mark.parametrize(("builder", "render"), BUILDERS)
@pytest.mark.parametrize("changed", ["source", "checksum"])
def test_asset_update_changes_recipe_revision(builder, render, monkeypatch, changed):
    original = builder.revision()
    if changed == "source":
        monkeypatch.setattr(envd_assets, "SOURCE_REVISION", "0" * 40)
    else:
        monkeypatch.setattr(
            envd_assets,
            "HEADLESS_FILES",
            tuple((source, destination, "0" * 64) for source, destination, _ in envd_assets.HEADLESS_FILES),
        )
    assert builder.revision() != original


@pytest.mark.parametrize(("builder", "render"), BUILDERS)
def test_recipe_freezes_shared_release_installer(builder, render):
    manifest = render()
    command = envd_release.installation_command()
    if builder is daytona:
        assert f"RUN {command}\n" in manifest["dockerfile"]
    elif builder is e2b:
        assert {"type": "RUN", "args": [command, "root"]} in manifest["steps"]
    elif builder is modal:
        assert command in manifest["commands"]
    elif builder is vercel:
        assert command in manifest["script"].splitlines()
    else:
        assert command in manifest.splitlines()
    assert "scripts/install-a13n-envd.sh" in command
    assert envd_release.INSTALLER_SHA256 in command
    assert f"--version {envd_release.VERSION}" in command
    assert "python" not in command
    assert "latest" not in command
    assert render() == manifest


@pytest.mark.parametrize(("builder", "render"), BUILDERS)
@pytest.mark.parametrize("field", ["VERSION", "TARGET", "SOURCE_REVISION", "INSTALLER_SHA256"])
def test_release_update_changes_every_recipe_revision(builder, render, monkeypatch, field):
    original = builder.revision()
    manifest = render()
    monkeypatch.setattr(envd_release, field, "changed")
    assert builder.revision() != original
    assert render() != manifest


@pytest.mark.parametrize("builder", [modal, daytona])
def test_selected_base_is_frozen_in_manifest_and_revision(builder):
    first, second = ("registry.example/base@sha256:" + digit * 64 for digit in ("a", "b"))
    assert builder.specification("headless", first) != builder.specification("headless", second)
    assert builder.revision("headless", first) != builder.revision("headless", second)
    assert builder.revision("headless", first) != builder.revision("desktop", first)
    assert builder.specification("headless") == builder.specification("headless", None)


@pytest.mark.parametrize("provider", ["modal", "daytona"])
def test_selected_base_is_explicit_and_preinstalled_fingerprints_stay_compatible(provider):
    from a13n_harness.providers.environment.daytona.shared import DaytonaEnvironmentConfiguration
    from a13n_harness.providers.environment.modal.shared import ModalEnvironmentConfiguration
    from a13n_service.providers.environment.recipes.daytona import DaytonaRecipe
    from a13n_service.providers.environment.recipes.modal import ModalRecipe
    from pydantic import ValidationError

    recipe, shared, field, identity = (
        (ModalRecipe, ModalEnvironmentConfiguration, "image", "im-Custom")
        if provider == "modal"
        else (DaytonaRecipe, DaytonaEnvironmentConfiguration, "snapshot", "custom-snapshot")
    )
    custom = {field: identity}
    assert recipe.model_validate(custom).fingerprint == shared.model_validate(custom).fingerprint
    base = "registry.example/base@sha256:" + "a" * 64
    assert recipe.model_validate({"base_image": base}).base_image == base
    with pytest.raises(ValidationError, match="base_image requires"):
        recipe.model_validate({**custom, "base_image": base})
    for invalid in ("ubuntu:24.04", "base@sha256:" + "a" * 63, base + "\nRUN echo bad"):
        with pytest.raises(ValidationError):
            recipe.model_validate({"base_image": invalid})


@pytest.mark.parametrize(
    ("uid", "system", "arch", "libc", "occupied", "diagnostic"),
    [
        ("0", "Linux", "x86_64", "glibc 2.41", False, None),
        ("1000", "Linux", "x86_64", "glibc 2.41", False, "USER root"),
        ("0", "Linux", "aarch64", "glibc 2.41", False, "x86_64"),
        ("0", "Linux", "x86_64", "glibc 2.36", False, "glibc >= 2.39"),
        ("0", "Linux", "x86_64", "musl", False, "glibc >= 2.39"),
        ("0", "Linux", "x86_64", "glibc 2.41", True, "reserve the sandbox"),
    ],
)
def test_selected_base_preflight_diagnostics_without_package_mutation(uid, system, arch, libc, occupied, diagnostic):
    # Shell functions model the build environment; no package or user mutations are run.
    import shlex

    prelude = (
        f"id() {{ printf '%s\\n' {shlex.quote(uid)}; }}; "
        f'uname() {{ if [ "$1" = -s ]; then echo {system}; else echo {arch}; fi; }}; '
        f"getconf() {{ printf '%s\\n' {shlex.quote(libc)}; }}; "
        f"getent() {{ return {0 if occupied else 2}; }}; "
    )
    result = subprocess.run(["sh", "-c", prelude + envd_assets.base_preflight()], capture_output=True, text=True)
    if diagnostic is None:
        assert result.returncode == 0, result.stderr
    else:
        assert result.returncode == 1
        assert "envd_base_unsupported" in result.stderr and diagnostic in result.stderr
