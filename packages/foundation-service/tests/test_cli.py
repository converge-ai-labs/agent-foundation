from pathlib import Path

import pytest
from click.testing import CliRunner
from converge_foundation_service.cli import main
from converge_foundation_service.settings import ServiceRole, get_settings
from fastapi import FastAPI


def test_serve_role_override_does_not_construct_environment_app(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    missing_web_dist = tmp_path / "missing-web"
    monkeypatch.setenv("FOUNDATION_ROLE", "all")
    monkeypatch.setenv("FOUNDATION_WEB_DIST_DIR", str(missing_web_dist))
    get_settings.cache_clear()
    served_apps: list[FastAPI] = []

    def capture_app(app: FastAPI, **kwargs: object) -> None:
        del kwargs
        served_apps.append(app)

    monkeypatch.setattr("converge_foundation_service.cli.uvicorn.run", capture_app)
    try:
        result = CliRunner().invoke(main, ["serve", "--role", "execution"])
    finally:
        get_settings.cache_clear()

    assert result.exit_code == 0, result.output
    assert len(served_apps) == 1
    assert served_apps[0].state.settings.role is ServiceRole.execution


def test_database_cli_delegates_to_service_migrator(monkeypatch: pytest.MonkeyPatch) -> None:
    revisions: list[str] = []

    class Migrator:
        def upgrade(self, revision: str) -> None:
            revisions.append(revision)

    monkeypatch.setattr("converge_foundation_service.cli._migrator", lambda: Migrator())

    result = CliRunner().invoke(main, ["db", "upgrade", "--revision", "head"])

    assert result.exit_code == 0, result.output
    assert revisions == ["head"]
