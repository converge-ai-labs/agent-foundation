from pathlib import Path

import pytest
from converge_foundation_service.storage.config import StorageSettings
from pydantic import ValidationError


def _settings(tmp_path: Path) -> dict[str, object]:
    return {
        "database": {"backend": "sqlite", "path": tmp_path / "database.sqlite3"},
        "redis": {"backend": "memory"},
        "objects": {"backend": "local", "root": tmp_path / "objects"},
        "filesystem": {"root": tmp_path / "files"},
    }


def test_settings_select_explicit_backends(tmp_path: Path) -> None:
    settings = StorageSettings.model_validate(_settings(tmp_path))

    assert settings.database.backend == "sqlite"
    assert settings.redis.backend == "memory"
    assert settings.objects.backend == "local"


def test_settings_reject_unknown_fields(tmp_path: Path) -> None:
    value = _settings(tmp_path)
    value["unexpected"] = True

    with pytest.raises(ValidationError, match="unexpected"):
        StorageSettings.model_validate(value)


@pytest.mark.parametrize("object_root", ["files", "files/objects"])
def test_settings_reject_overlapping_local_roots(tmp_path: Path, object_root: str) -> None:
    value = _settings(tmp_path)
    value["objects"] = {"backend": "local", "root": tmp_path / object_root}

    with pytest.raises(ValidationError, match="must not overlap"):
        StorageSettings.model_validate(value)


def test_settings_reject_sqlite_beneath_shared_root(tmp_path: Path) -> None:
    value = _settings(tmp_path)
    value["database"] = {"backend": "sqlite", "path": tmp_path / "files" / "database.sqlite3"}

    with pytest.raises(ValidationError, match="SQLite database"):
        StorageSettings.model_validate(value)


def test_settings_normalizes_parent_components_when_comparing_roots(tmp_path: Path) -> None:
    value = _settings(tmp_path)
    value["objects"] = {"backend": "local", "root": tmp_path / "nested" / ".." / "files"}

    with pytest.raises(ValidationError, match="must not overlap"):
        StorageSettings.model_validate(value)


def test_settings_bound_resource_limits(tmp_path: Path) -> None:
    value = _settings(tmp_path)
    value["filesystem"] = {"root": tmp_path / "files", "worker_limit": 1000}

    with pytest.raises(ValidationError, match="worker_limit"):
        StorageSettings.model_validate(value)


def test_memory_sqlite_is_not_treated_as_a_filesystem_path(tmp_path: Path) -> None:
    value = _settings(tmp_path)
    value["database"] = {"backend": "sqlite", "path": ":memory:"}
    value["objects"] = {"backend": "s3", "bucket": "objects"}
    value["filesystem"] = {"root": Path.cwd()}

    settings = StorageSettings.model_validate(value)

    assert settings.database.backend == "sqlite"


def test_secret_url_is_not_rendered() -> None:
    settings = StorageSettings.model_validate(
        {
            "database": {"backend": "postgresql", "url": "postgresql://user:password@example/db"},
            "redis": {"backend": "redis", "url": "redis://:secret@example/0"},
            "objects": {"backend": "s3", "bucket": "objects"},
            "filesystem": {"root": "/data/files"},
        }
    )

    rendered = repr(settings)
    assert "password" not in rendered
    assert "secret" not in rendered
