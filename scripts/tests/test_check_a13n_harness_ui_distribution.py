from __future__ import annotations

import hashlib
import json
import sys
import zipfile
from pathlib import Path

import pytest

SCRIPTS_DIRECTORY = Path(__file__).parents[1]
sys.path.insert(0, str(SCRIPTS_DIRECTORY))

from check_a13n_harness_ui_distribution import TERMINAL_PACKAGE_PATHS, DistributionError, validate_wheel  # noqa: E402


def _write_wheel(
    path: Path,
    *,
    index: bytes = b'<script src="/assets/main.js"></script>',
    provider_version: str | None = "1.2.3",
    harness_version: str | None = "1.2.3",
    protocol_version: str | None = "1.2.3",
    include_runtime_manifest: bool = True,
    include_terminal_shell: bool = True,
    include_license: bool = True,
    omitted_package_file: str | None = None,
    include_entrypoint: bool = True,
    cli_content: bytes = b"def main(): pass\n",
    extra_packaged_files: dict[str, bytes] | None = None,
) -> None:
    files = {
        "index.html": index,
        "assets/main.css": b"body { color: black; }\n",
        "assets/main.js": b"console.log('a13n-harness-ui')\n",
    }
    manifest = {
        "schema_version": "1",
        "source": "apps/a13n-harness-ui",
        "files": {name: hashlib.sha256(content).hexdigest() for name, content in files.items()},
    }
    with zipfile.ZipFile(path, mode="w") as archive:
        for name, content in files.items():
            archive.writestr(f"a13n_harness_ui/static/{name}", content)
        archive.writestr(
            "a13n_harness_ui/static/asset-manifest.json",
            json.dumps(manifest),
        )
        if include_license:
            archive.writestr(
                "a13n_harness_ui-9.8.7.dist-info/licenses/LICENSE",
                (SCRIPTS_DIRECTORY.parent / "packages/a13n-harness-ui/LICENSE").read_bytes(),
            )
        for module in TERMINAL_PACKAGE_PATHS:
            if module.as_posix() == "a13n_harness_ui/interactive/shell.py" and not include_terminal_shell:
                continue
            if module.name == omitted_package_file:
                continue
            content = b"\n"
            if module.as_posix().startswith("a13n_harness_ui/subagents/") or module.as_posix() in {
                "a13n_harness_ui/prompts.py",
                "a13n_harness_ui/assets/system_prompt.md",
            }:
                content = (SCRIPTS_DIRECTORY.parent / "packages/a13n-harness-ui" / module).read_bytes()
            elif module.as_posix() == "a13n_harness_ui/interactive/shell.py":
                content = b"class CliShell: pass\n"
            elif module.as_posix() == "a13n_harness_ui/webui.py":
                content = b"def create_webui(): pass\n"
            elif module.as_posix() == "a13n_harness_ui/cli.py":
                content = cli_content
            archive.writestr(module.as_posix(), content)
        archive.writestr("a13n_harness_ui/interactive/__init__.py", b"\n")
        if include_runtime_manifest:
            archive.writestr(
                "a13n_harness_ui/assets/a13n-envd-release.json",
                json.dumps(
                    {
                        "schema_version": "1",
                        "release": "0.0.3",
                        "base_url": "https://example.test/releases/0.0.3",
                        "targets": {"test-target": {}},
                    }
                ),
            )
        if include_entrypoint:
            archive.writestr(
                "a13n_harness_ui-9.8.7.dist-info/entry_points.txt",
                "[console_scripts]\na13n-harness-ui = a13n_harness_ui.cli:main\n",
            )
        archive.writestr(
            "a13n_harness_ui-9.8.7.dist-info/METADATA",
            "\n".join(
                (
                    "Metadata-Version: 2.4",
                    "Name: a13n-harness-ui",
                    "Version: 9.8.7",
                    "Requires-Dist: a13n-environment"
                    + (f"=={provider_version}" if provider_version is not None else ""),
                    "Requires-Dist: a13n-harness" + (f"=={harness_version}" if harness_version is not None else ""),
                    "Requires-Dist: a13n-stream-protocol"
                    + (f"=={protocol_version}" if protocol_version is not None else ""),
                    "",
                )
            ),
        )
        for name, content in (extra_packaged_files or {}).items():
            archive.writestr(f"a13n_harness_ui/static/{name}", content)


def test_validates_cli_distribution(tmp_path: Path) -> None:
    wheel = tmp_path / "a13n-harness-ui.whl"
    _write_wheel(
        wheel,
    )

    validate_wheel(wheel, require_exact_internal_version=True)


def test_rejects_missing_project_license(tmp_path: Path) -> None:
    wheel = tmp_path / "a13n-harness-ui.whl"
    _write_wheel(wheel, include_license=False)

    with pytest.raises(DistributionError, match="missing the project license"):
        validate_wheel(wheel)


def test_development_validation_allows_unpinned_workspace_dependencies(tmp_path: Path) -> None:
    wheel = tmp_path / "a13n-harness-ui.whl"
    _write_wheel(
        wheel,
        provider_version=None,
        harness_version=None,
        protocol_version=None,
    )

    validate_wheel(wheel)


def test_rejects_missing_a13n_envd_manifest(tmp_path: Path) -> None:
    wheel = tmp_path / "a13n-harness-ui.whl"
    _write_wheel(
        wheel,
        include_runtime_manifest=False,
    )

    with pytest.raises(DistributionError, match=r"missing a13n_harness_ui/assets/a13n-envd-release\.json"):
        validate_wheel(wheel)


def test_rejects_missing_console_entrypoint(tmp_path: Path) -> None:
    wheel = tmp_path / "a13n-harness-ui.whl"
    _write_wheel(
        wheel,
        include_entrypoint=False,
    )

    with pytest.raises(DistributionError, match=r"Expected one entry_points\.txt"):
        validate_wheel(wheel)


def test_rejects_unimportable_entrypoint(tmp_path: Path) -> None:
    wheel = tmp_path / "a13n-harness-ui.whl"
    _write_wheel(
        wheel,
        cli_content=b"raise RuntimeError('broken wheel')\n",
    )

    with pytest.raises(DistributionError, match="cannot import its entrypoint and CLI"):
        validate_wheel(wheel)


def test_rejects_missing_terminal_shell(tmp_path: Path) -> None:
    wheel = tmp_path / "a13n-harness-ui.whl"
    _write_wheel(
        wheel,
        include_terminal_shell=False,
    )

    with pytest.raises(DistributionError, match=r"missing a13n_harness_ui/interactive/shell\.py"):
        validate_wheel(wheel)


@pytest.mark.parametrize("name", ["code-reviewer.md", "executor.md", "explorer.md", "system_prompt.md"])
def test_rejects_missing_packaged_prompt(tmp_path: Path, name: str) -> None:
    wheel = tmp_path / "a13n-harness-ui.whl"
    _write_wheel(wheel, omitted_package_file=name)
    with pytest.raises(DistributionError, match="missing a13n_harness_ui/"):
        validate_wheel(wheel)


def test_rejects_mismatched_internal_dependency_pins(tmp_path: Path) -> None:
    wheel = tmp_path / "a13n-harness-ui.whl"
    _write_wheel(
        wheel,
        harness_version="1.2.3",
        protocol_version="1.2.4",
    )

    with pytest.raises(DistributionError, match="internal dependency versions do not match"):
        validate_wheel(wheel, require_exact_internal_version=True)


def test_rejects_undeclared_shell_reference(tmp_path: Path) -> None:
    wheel = tmp_path / "a13n-harness-ui.whl"
    _write_wheel(wheel, index=b'<script src="/assets/missing.js"></script>')

    with pytest.raises(DistributionError, match="shell references an undeclared or missing asset"):
        validate_wheel(wheel, require_exact_internal_version=True)


def test_rejects_packaged_file_missing_from_manifest(tmp_path: Path) -> None:
    wheel = tmp_path / "a13n-harness-ui.whl"
    _write_wheel(
        wheel,
        index=b'<script src="/assets/main.js"></script>',
        extra_packaged_files={"assets/undeclared.js": b"unexpected\n"},
    )

    with pytest.raises(DistributionError, match="manifest does not match packaged files"):
        validate_wheel(wheel, require_exact_internal_version=True)
