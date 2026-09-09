"""Local reset ownership checks must run before any destructive operation."""

import dataclasses
from pathlib import Path

import pytest
from a13n_service.configuration.sources import load_settings

from dev.service.environment import LOCAL_CONFIG, Environment


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
    return Environment(settings, root)


def test_owned_local_target_and_lock(environment):
    environment.validate()
    with environment.lock(shared=True):
        with pytest.raises(ValueError, match="running"):
            with environment.lock():
                pytest.fail("exclusive reset lock must not be acquired")
    with environment.lock():
        pass


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
    (environment.root / "var").mkdir()
    outside = tmp_path / "unrelated.txt"
    outside.write_text("preserve")
    environment.incomplete.symlink_to(outside)
    with pytest.raises(ValueError, match="symlink"):
        environment.validate()
    assert outside.read_text() == "preserve"
