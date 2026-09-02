from __future__ import annotations

from pathlib import Path

import pytest
from a13n_ui.errors import ConfigurationError
from a13n_ui.settings import AgentUiSettings, StorageSettings
from a13n_ui.settings_loader import ensure_default_directories, load_agent_ui_settings
from pydantic import ValidationError

pytestmark = pytest.mark.anyio


def test_settings_normalize_process_values(tmp_path: Path) -> None:
    settings = AgentUiSettings(
        storage=StorageSettings(data_root=tmp_path / "nested" / ".." / "store"),
        log_level="debug",
    )

    assert settings.storage.data_root == (tmp_path / "store").resolve()
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


async def test_loads_one_strict_full_settings_yaml(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.yaml"
    data_root = tmp_path / "data"
    settings_path.write_text(
        f"""
schema_version: "1"
process:
  storage:
    data_root: {data_root.as_posix()}
  log_level: debug
""".strip()
        + "\n"
    )

    source = await load_agent_ui_settings(settings_path)

    assert source.explicit is True
    assert source.exists is True
    assert source.settings.storage.data_root == data_root
    assert source.settings.log_level == "DEBUG"


async def test_default_settings_use_one_fixed_user_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    source = await load_agent_ui_settings()

    assert source.exists is False
    assert source.path == tmp_path / ".a13n-ui/agent-ui.yaml"
    assert source.settings.storage.data_root == tmp_path / ".a13n-ui/data"

    ensure_default_directories(source)
    assert (tmp_path / ".a13n-ui/data").is_dir()


async def test_settings_yaml_rejects_excessive_depth_with_bounded_error(tmp_path: Path) -> None:
    deep = tmp_path / "deep.yaml"
    lines = ["root:"]
    for depth in range(70):
        lines.append(f"{'  ' * (depth + 1)}level-{depth}:")
    lines.append(f"{'  ' * 72}value")
    deep.write_text("\n".join(lines) + "\n")

    with pytest.raises(ConfigurationError) as invalid:
        await load_agent_ui_settings(deep)
    assert invalid.value.code == "settings_source_limit"


async def test_settings_yaml_rejects_duplicate_keys_and_explicit_missing_files(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.yaml"
    duplicate.write_text(
        f"storage:\n  data_root: {tmp_path.as_posix()}\nstorage:\n  data_root: {tmp_path.as_posix()}\n"
    )

    with pytest.raises(ConfigurationError) as invalid:
        await load_agent_ui_settings(duplicate)
    assert invalid.value.code == "settings_invalid"

    with pytest.raises(ConfigurationError) as missing:
        await load_agent_ui_settings(tmp_path / "missing.yaml")
    assert missing.value.code == "settings_unavailable"
