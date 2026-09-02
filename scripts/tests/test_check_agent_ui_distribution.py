from __future__ import annotations

import hashlib
import json
import sys
import zipfile
from pathlib import Path

import pytest

SCRIPTS_DIRECTORY = Path(__file__).parents[1]
sys.path.insert(0, str(SCRIPTS_DIRECTORY))

from check_agent_ui_distribution import DistributionError, validate_wheel  # noqa: E402


def _write_wheel(
    path: Path,
    *,
    index: bytes,
    provider_version: str | None = "1.2.3",
    harness_version: str | None = "1.2.3",
    protocol_version: str | None = "1.2.3",
    include_runtime_manifest: bool = True,
    extra_packaged_files: dict[str, bytes] | None = None,
) -> None:
    files = {
        "index.html": index,
        "assets/main.css": b"body { color: black; }\n",
        "assets/main.js": b"console.log('agent-ui')\n",
    }
    manifest = {
        "schema_version": "1",
        "source": "apps/harness-ui",
        "files": {name: hashlib.sha256(content).hexdigest() for name, content in files.items()},
    }
    with zipfile.ZipFile(path, mode="w") as archive:
        for name, content in files.items():
            archive.writestr(f"a13n_ui/static/{name}", content)
        archive.writestr(
            "a13n_ui/static/asset-manifest.json",
            json.dumps(manifest),
        )
        if include_runtime_manifest:
            archive.writestr(
                "a13n_ui/assets/agent-envd-release.json",
                json.dumps(
                    {
                        "schema_version": "1",
                        "release": "0.0.3",
                        "base_url": "https://example.test/releases/0.0.3",
                        "targets": {"test-target": {}},
                    }
                ),
            )
        archive.writestr(
            "a13n_ui-9.8.7.dist-info/METADATA",
            "\n".join(
                (
                    "Metadata-Version: 2.4",
                    "Name: a13n-ui",
                    "Version: 9.8.7",
                    "Requires-Dist: a13n-environment-provider"
                    + (f"=={provider_version}" if provider_version is not None else ""),
                    "Requires-Dist: a13n-harness" + (f"=={harness_version}" if harness_version is not None else ""),
                    "Requires-Dist: a13n-stream-protocol"
                    + (f"=={protocol_version}" if protocol_version is not None else ""),
                    "",
                )
            ),
        )
        for name, content in (extra_packaged_files or {}).items():
            archive.writestr(f"a13n_ui/static/{name}", content)


def test_validates_declared_shell_assets(tmp_path: Path) -> None:
    wheel = tmp_path / "agent-ui.whl"
    _write_wheel(
        wheel,
        index=(b'<link rel="stylesheet" href="/assets/main.css"><script type="module" src="/assets/main.js"></script>'),
    )

    validate_wheel(wheel, require_exact_internal_version=True)


def test_development_validation_allows_unpinned_workspace_dependencies(tmp_path: Path) -> None:
    wheel = tmp_path / "agent-ui.whl"
    _write_wheel(
        wheel,
        index=b'<script src="/assets/main.js"></script>',
        provider_version=None,
        harness_version=None,
        protocol_version=None,
    )

    validate_wheel(wheel)


def test_rejects_missing_agent_envd_manifest(tmp_path: Path) -> None:
    wheel = tmp_path / "agent-ui.whl"
    _write_wheel(
        wheel,
        index=b'<script src="/assets/main.js"></script>',
        include_runtime_manifest=False,
    )

    with pytest.raises(DistributionError, match=r"missing a13n_ui/assets/agent-envd-release\.json"):
        validate_wheel(wheel)


def test_rejects_undeclared_shell_reference(tmp_path: Path) -> None:
    wheel = tmp_path / "agent-ui.whl"
    _write_wheel(wheel, index=b'<script src="/assets/missing.js"></script>')

    with pytest.raises(DistributionError, match="shell references an undeclared or missing asset"):
        validate_wheel(wheel, require_exact_internal_version=True)


def test_rejects_mismatched_internal_dependency_pins(tmp_path: Path) -> None:
    wheel = tmp_path / "agent-ui.whl"
    _write_wheel(
        wheel,
        index=b'<script src="/assets/main.js"></script>',
        harness_version="1.2.3",
        protocol_version="1.2.4",
    )

    with pytest.raises(DistributionError, match="internal dependency versions do not match"):
        validate_wheel(wheel, require_exact_internal_version=True)


def test_rejects_packaged_file_missing_from_manifest(tmp_path: Path) -> None:
    wheel = tmp_path / "agent-ui.whl"
    _write_wheel(
        wheel,
        index=b'<script src="/assets/main.js"></script>',
        extra_packaged_files={"assets/undeclared.js": b"unexpected\n"},
    )

    with pytest.raises(DistributionError, match="manifest does not match packaged files"):
        validate_wheel(wheel, require_exact_internal_version=True)
