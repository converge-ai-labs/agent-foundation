from __future__ import annotations

from pathlib import Path

import pytest
from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings
from a13n_harness_ui.settings_loader import ensure_default_directories, load_harness_ui_settings
from pydantic import ValidationError

pytestmark = pytest.mark.anyio


def test_settings_normalize_process_values(tmp_path: Path) -> None:
    settings = HarnessUiSettings(
        storage=StorageSettings(data_root=tmp_path / "nested" / ".." / "store"),
        log_level="debug",
    )

    assert settings.storage.data_root == (tmp_path / "store").resolve()
    assert settings.log_level == "DEBUG"
    assert settings.log_format == "pretty"
    assert settings.pricing_auto_update is True


def test_settings_reject_unknown_or_invalid_values(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        StorageSettings.model_validate({"data_root": tmp_path, "unknown": True})

    with pytest.raises(ValidationError):
        HarnessUiSettings(storage=StorageSettings(data_root=tmp_path), log_level="TRACE")

    with pytest.raises(ValidationError):
        StorageSettings(data_root=tmp_path, max_object_bytes=512)


def test_settings_remain_strict(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        StorageSettings.model_validate({"data_root": str(tmp_path)}, strict=True)


async def test_loads_one_strict_full_settings_yaml(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.yaml"
    data_root = tmp_path / "data"
    settings_path.write_text(
        """
schema_version: "1"
process:
  log_level: debug
  pricing_auto_update: false
""".strip()
        + "\n"
    )

    source = await load_harness_ui_settings(settings_path)

    assert source.explicit is True
    assert source.exists is True
    assert source.configuration is not None
    assert source.candidate_error is None
    assert source.settings.storage.data_root == data_root
    assert source.settings.log_level == "DEBUG"
    assert source.settings.pricing_auto_update is False


async def test_default_settings_use_one_fixed_user_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    source = await load_harness_ui_settings()

    assert source.exists is False
    assert source.path == tmp_path / ".a13n-harness-ui/a13n-harness-ui.yaml"
    assert source.settings.storage.data_root == tmp_path / ".a13n-harness-ui/data"

    ensure_default_directories(source)
    assert (tmp_path / ".a13n-harness-ui/data").is_dir()


async def test_settings_yaml_reports_excessive_depth_as_candidate_error(tmp_path: Path) -> None:
    deep = tmp_path / "deep.yaml"
    lines = ["root:"]
    for depth in range(70):
        lines.append(f"{'  ' * (depth + 1)}level-{depth}:")
    lines.append(f"{'  ' * 72}value")
    deep.write_text("\n".join(lines) + "\n")

    source = await load_harness_ui_settings(deep)

    assert source.explicit is True
    assert source.exists is True
    assert source.configuration is None
    assert source.candidate_error is not None
    assert source.candidate_error.code == "configuration_source_limit"
    ensure_default_directories(source)
    assert source.settings.storage.data_root.is_dir()


async def test_settings_yaml_reports_duplicate_keys_and_explicit_missing_file(
    tmp_path: Path,
) -> None:
    duplicate = tmp_path / "duplicate.yaml"
    duplicate.write_text('schema_version: "1"\nprocess:\n  log_level: INFO\nprocess:\n  log_level: DEBUG\n')

    source = await load_harness_ui_settings(duplicate)

    assert source.explicit is True
    assert source.exists is True
    assert source.configuration is None
    assert source.candidate_error is not None
    assert source.candidate_error.code == "settings_invalid"
    assert source.settings.log_level == "INFO"

    missing = await load_harness_ui_settings(tmp_path / "missing.yaml")
    assert missing.explicit is True
    assert missing.exists is False
    assert missing.configuration is None
    assert missing.candidate_error is not None
    assert missing.candidate_error.code == "settings_unavailable"
    ensure_default_directories(missing)
    assert missing.settings.storage.data_root.is_dir()
