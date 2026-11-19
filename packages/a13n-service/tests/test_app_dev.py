import click
import pytest
from a13n_service.app_dev import _snapshot, require_local
from a13n_service.settings import Database, Settings
from pydantic import SecretStr


@pytest.mark.parametrize("address", ["localhost", "db.example.com", "127.0.0.1/app?hostaddr=192.0.2.3"])
def test_app_development_refuses_postgres_target_overrides(address):
    settings = Settings(database=Database(url=SecretStr(f"postgresql+psycopg://user@{address}")))
    with pytest.raises(click.ClickException, match="loopback IP"):
        require_local(settings)


def test_app_development_requires_explicit_local_database(monkeypatch):
    settings = Settings(database=Database(url=SecretStr("postgresql+psycopg://app@127.0.0.1:5432/app")))
    monkeypatch.delenv("PGHOSTADDR", raising=False)
    monkeypatch.delenv("PGSERVICE", raising=False)
    require_local(settings)
    monkeypatch.setenv("PGHOSTADDR", "192.0.2.3")
    with pytest.raises(click.ClickException, match="connection overrides"):
        require_local(settings)


def test_snapshot_tracks_python_additions_and_removals_not_build_output(tmp_path):
    initial = _snapshot(tmp_path)
    (tmp_path / "index.js").touch()
    assert _snapshot(tmp_path) == initial
    source = tmp_path / "app.py"
    source.touch()
    assert list(_snapshot(tmp_path)) == [source]
    source.unlink()
    assert _snapshot(tmp_path) == initial
