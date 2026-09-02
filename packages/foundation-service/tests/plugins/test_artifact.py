from __future__ import annotations

from pathlib import Path

import pytest
from a13n_service.plugins.artifact import inspect_distribution_wheel, inspect_plugin_wheel
from a13n_service.plugins.errors import PluginError

from .conftest import build_wheel


@pytest.mark.anyio
async def test_inspects_standard_plugin_wheel_without_importing(tmp_path: Path) -> None:
    path = tmp_path / "plugin.whl"
    path.write_bytes(build_wheel(requires_dist=("pydantic>=2",)))

    inspected = await inspect_plugin_wheel(path, max_expanded_bytes=1024 * 1024, max_members=100)

    assert inspected.plugin_key == "acme.audit"
    assert inspected.distribution_name == "acme-audit"
    assert inspected.version == "1.0.0"
    assert inspected.top_level_package == "acme_audit"
    assert inspected.requires_dist == ("pydantic>=2",)
    assert inspected.wheel_tags == ("py3-none-any",)
    assert inspected.root_is_purelib is True


@pytest.mark.anyio
async def test_inspects_dependency_wheel_without_requiring_plugin_entry_point(tmp_path: Path) -> None:
    path = tmp_path / "dependency.whl"
    path.write_bytes(build_wheel(requires_dist=("pydantic>=2",), include_entry_point=False))

    inspected = await inspect_distribution_wheel(path, max_expanded_bytes=1024 * 1024, max_members=100)

    assert inspected.distribution_name == "acme-audit"
    assert inspected.version == "1.0.0"
    assert inspected.requires_dist == ("pydantic>=2",)
    assert inspected.wheel_tags == ("py3-none-any",)
    assert inspected.root_is_purelib is True


@pytest.mark.anyio
async def test_dependency_wheel_cannot_register_foundation_plugin_entry_point(tmp_path: Path) -> None:
    path = tmp_path / "dependency.whl"
    path.write_bytes(build_wheel(include_entry_point=True))

    with pytest.raises(PluginError) as captured:
        await inspect_distribution_wheel(path, max_expanded_bytes=1024 * 1024, max_members=100)

    assert captured.value.details == {"reason": "dependency_extension_entry_point"}


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("wheel", "reason"),
    (
        (build_wheel(second_entry_point=True), "plugin_entry_point_count"),
        (build_wheel(corrupt_record=True), "record_hash_mismatch"),
        (build_wheel(unsafe_member=True), "unsafe_archive_member"),
        (build_wheel(requires_dist=("example @ https://example.test/pkg.whl",)), "direct_requirement_unsupported"),
    ),
)
async def test_rejects_invalid_or_unsafe_wheels(tmp_path: Path, wheel: bytes, reason: str) -> None:
    path = tmp_path / "plugin.whl"
    path.write_bytes(wheel)

    with pytest.raises(PluginError) as rejected:
        await inspect_plugin_wheel(path, max_expanded_bytes=1024 * 1024, max_members=100)

    assert rejected.value.code == "plugin_artifact_invalid"
    assert rejected.value.details == {"reason": reason}
