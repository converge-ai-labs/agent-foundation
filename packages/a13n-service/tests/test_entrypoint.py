"""One executable owns role-aware migrations for native and container startup."""

import os
import subprocess
from pathlib import Path

import pytest
from a13n_service.cli import _prepare_database
from a13n_service.configuration.sources import load_settings

ENTRYPOINT = Path(__file__).parents[3] / "scripts" / "docker-entrypoint.sh"


@pytest.mark.parametrize(
    ("role", "enabled", "expected"),
    [
        ("all", True, "upgrade"),
        ("control", True, "upgrade"),
        ("control", False, "check"),
        ("worker", True, "check"),
        ("connectivity", True, "check"),
    ],
)
def test_role_aware_schema_preparation(tmp_path, monkeypatch, role, enabled, expected):
    config = tmp_path / "service.toml"
    config.write_text(f'[service]\nrole="{role}"\n[migration]\nauto_migrate={str(enabled).lower()}\n')
    calls = []

    class Migrator:
        def upgrade(self):
            calls.append("upgrade")

        def current(self, *, check_heads):
            assert check_heads
            calls.append("check")

    monkeypatch.setattr("a13n_service.cli._migrator", lambda settings: Migrator())
    _prepare_database(load_settings(config, environ={}))
    assert calls == [expected]


def test_cli_worker_override_cannot_migrate(tmp_path, monkeypatch):
    config = tmp_path / "service.toml"
    config.write_text('[service]\nrole="all"\n[migration]\nauto_migrate=true\n')
    calls = []

    class Migrator:
        def upgrade(self):
            pytest.fail("worker must never migrate")

        def current(self, *, check_heads):
            calls.append(check_heads)

    monkeypatch.setattr("a13n_service.cli._migrator", lambda settings: Migrator())
    _prepare_database(load_settings(config, environ={}, overrides={"service": {"role": "worker"}}))
    assert calls == [True]


def test_entrypoint_preserves_exact_command_and_config(tmp_path):
    calls = tmp_path / "calls"
    executable = tmp_path / "a13n-service"
    executable.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > "{calls}"\n')
    executable.chmod(0o755)
    args = ["--config", "/mounted config/service.toml", "serve", "--role", "worker"]
    subprocess.run(
        [str(ENTRYPOINT), "a13n-service", *args],
        check=True,
        env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"},
    )
    assert calls.read_text().splitlines() == args
