"""Local reset ownership checks must run before any destructive operation."""

import dataclasses
from pathlib import Path
from types import SimpleNamespace

import pytest
from a13n_service.configuration.sources import load_settings

from dev.service.environment import LOCAL_CONFIG
from dev.service.lifecycle import lifecycle_lock
from dev.service.tests.support import environment_for


@pytest.fixture
def environment(tmp_path):
    root = tmp_path.resolve()
    settings = load_settings(
        LOCAL_CONFIG,
        environ={},
        overrides={
            "objects": {"local_root": root / "var/service/objects"},
            "filesystem": {"root": root / "var/service/files"},
        },
    )
    return environment_for(settings, root)


def test_owned_local_target_and_lock(environment):
    environment.validate()
    with lifecycle_lock(environment.root):
        with pytest.raises(ValueError, match="running"):
            with lifecycle_lock(environment.root):
                pytest.fail("exclusive reset lock must not be acquired")
    with lifecycle_lock(environment.root):
        pass


def test_replacement_namespace_never_selects_retained_legacy_resources(environment, monkeypatch, capsys):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if tuple(command[:3]) == ("docker", "ps", "-a"):
            return SimpleNamespace(stdout="container legacy-postgres\n")
        if tuple(command[:3]) == ("docker", "volume", "ls"):
            return SimpleNamespace(stdout="volume legacy-postgres\n")
        return SimpleNamespace(stdout="")

    monkeypatch.setattr("dev.service.environment.subprocess.run", run)
    environment.report_legacy_resources()
    environment.compose("down", "--volumes", "--remove-orphans")
    assert environment.project == f"a13n-dev-v2-{environment.local_id}"
    assert environment.legacy_project == f"a13n-local-{environment.local_id}"
    compose = calls[-1]
    assert compose[compose.index("--project-name") + 1] == environment.project
    assert environment.legacy_project not in compose
    output = capsys.readouterr().out
    assert "will not be used, stopped, reset, or migrated" in output


@pytest.mark.parametrize(
    "overrides",
    [
        {
            "database": {
                "url": "postgresql+psycopg://a13n_service_dev:local-only-password@db.example.com:15432/a13n_service_dev"
            }
        },
        {
            "database": {
                "url": "postgresql+psycopg://a13n_service_dev:local-only-password@127.0.0.1:15432/other_database"
            }
        },
        {"redis": {"url": "redis://127.0.0.1:16379/1"}},
        {"objects": {"backend": "s3", "bucket": "not-owned"}},
        {"objects": {"local_root": Path("/tmp/not-owned")}},
    ],
)
def test_effective_overrides_cannot_expand_reset_ownership(environment, overrides):
    values = environment.settings.model_dump()
    for group, fields in overrides.items():
        values[group].update(fields)
    changed = dataclasses.replace(environment, settings=type(environment.settings).model_validate(values))
    with pytest.raises(ValueError):
        changed.validate()


def test_symlink_cannot_redirect_state_deletion(environment, tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (environment.root / "var").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        environment.validate()
    assert outside.exists()


def test_reset_marker_cannot_redirect_writes(environment, tmp_path):
    environment.incomplete.parent.mkdir(parents=True)
    outside = tmp_path / "unrelated.txt"
    outside.write_text("preserve")
    environment.incomplete.symlink_to(outside)
    with pytest.raises(ValueError, match="symlink"):
        environment.validate()
    assert outside.read_text() == "preserve"
