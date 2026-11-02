"""Prepared Skill schemas survive isolated sdist-to-wheel builds without Service imports."""

import json
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "packages/a13n-service"
SCHEMA = "a13n_service/builtin_skills/platform-operations/schemas/platform-tools.json"


def test_prepared_schema_survives_sdist_rebuild_and_missing_asset_fails(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    for name in ("hatch_build.py", "build_docs.py"):
        shutil.copy2(PACKAGE / name, source / name)
    manifest = (PACKAGE / "pyproject.toml").read_text()
    # Keep the production build system/artifact rules, without runtime dependencies.
    (source / "pyproject.toml").write_text(
        '[project]\nname = "a13n-service"\nversion = "0.0.0"\n' + manifest[manifest.index("[build-system]") :]
    )
    (source / ".gitignore").write_text(SCHEMA + "\n")
    target = source / SCHEMA
    target.parent.mkdir(parents=True)
    expected = json.dumps({"tools": [{"name": "prepared_contract"}]}).encode()
    target.write_bytes(expected)
    (source / "a13n_service/__init__.py").write_text('raise RuntimeError("Build must not import Service")\n')
    (source / "a13n_service/documentation.json").write_text(
        json.dumps(
            {
                "format": 1,
                "sections": [
                    {
                        "source": "docs/a13n-service/example.md",
                        "language": "en",
                        "title": "Example",
                        "text": "Prepared documentation",
                        "headings": [],
                        "line_start": 1,
                        "line_end": 1,
                    }
                ],
            }
        )
    )
    (source / "a13n_service/static").mkdir()
    (source / "a13n_service/static/index.html").write_text("prepared Console")

    def build(path, *args):
        return subprocess.run(
            ["uv", "build", "--python", sys.executable, str(path), *args], capture_output=True, text=True, check=False
        )

    output = tmp_path / "dist"
    result = build(source, "--sdist", "--out-dir", str(output))
    assert result.returncode == 0, result.stderr
    with tarfile.open(next(output.glob("*.tar.gz"))) as archive:
        entry = next(member for member in archive.getmembers() if member.name.endswith(SCHEMA))
        assert archive.extractfile(entry).read() == expected
        archive.extractall(tmp_path / "unpacked", filter="data")
    unpacked = next((tmp_path / "unpacked").iterdir())
    result = build(unpacked, "--wheel", "--out-dir", str(output))
    assert result.returncode == 0, result.stderr
    with zipfile.ZipFile(next(output.glob("*.whl"))) as wheel:
        assert wheel.read(SCHEMA) == expected
    (unpacked / SCHEMA).unlink()
    result = build(unpacked, "--wheel", "--out-dir", str(output))
    assert result.returncode != 0
    assert "make a13n-service-assets" in result.stderr
