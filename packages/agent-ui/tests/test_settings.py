from __future__ import annotations

from pathlib import Path

import pytest
from a13n_ui.settings import AgentUiSettings, DurabilityProfile, StorageSettings
from pydantic import ValidationError


def test_settings_normalize_restart_bound_values(tmp_path: Path) -> None:
    settings = AgentUiSettings(
        storage=StorageSettings(data_root=tmp_path / "nested" / ".." / "store"),
        log_level="debug",
    )

    assert settings.storage.data_root == (tmp_path / "store").resolve()
    assert settings.storage.durability_profile is DurabilityProfile.full
    assert settings.log_level == "DEBUG"
    assert settings.log_format == "pretty"


def test_settings_reject_unknown_or_invalid_values(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        StorageSettings.model_validate({"data_root": tmp_path, "unknown": True})

    with pytest.raises(ValidationError):
        AgentUiSettings(storage=StorageSettings(data_root=tmp_path), log_level="TRACE")

    with pytest.raises(ValidationError):
        StorageSettings(data_root=tmp_path, max_object_bytes=512)


def test_settings_remain_strict(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        StorageSettings.model_validate({"data_root": str(tmp_path)}, strict=True)
