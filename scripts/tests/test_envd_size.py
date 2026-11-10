from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
SPEC = importlib.util.spec_from_file_location("envd_size", ROOT / "scripts/envd_size.py")
assert SPEC and SPEC.loader
size = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(size)


@pytest.mark.parametrize("variant", ["default", "vm"])
@pytest.mark.parametrize("field", ["binary", "archive"])
@pytest.mark.parametrize("excess", [0, 1])
def test_budgets_are_inclusive_and_check_both_artifacts(tmp_path, monkeypatch, variant, field, excess):
    monkeypatch.setitem(size.BUDGETS, variant, (64, 32))
    binary, archive = tmp_path / "binary", tmp_path / "archive"
    binary.write_bytes(b"x" * (64 + excess if field == "binary" else 64))
    archive.write_bytes(b"x" * (32 + excess if field == "archive" else 32))
    bundle = tmp_path / "build/a13n-envd-test/out/microsandbox-runtime.tar.gz"
    bundle.parent.mkdir(parents=True)
    bundle.write_bytes(binary.read_bytes())
    report = size.measure(binary, archive, "x86_64-unknown-linux-gnu", variant, tmp_path)
    assert report["passed"] is (excess == 0)
    assert report["errors"] == ([] if excess == 0 else [f"{field.title()} exceeds size budget"])


@pytest.mark.parametrize("copies", [0, 1, 2, 7])
def test_vm_archive_must_have_one_complete_copy(tmp_path, copies):
    bundle = tmp_path / "build/a13n-envd-test/out/microsandbox-runtime.tar.gz"
    bundle.parent.mkdir(parents=True)
    bundle.write_bytes(b"unique runtime archive bytes")
    binary, archive = tmp_path / "binary", tmp_path / "archive"
    binary.write_bytes(b"ELF" + bundle.read_bytes() * copies + b"end")
    archive.write_bytes(b"compressed")
    report = size.measure(binary, archive, "x86_64-unknown-linux-gnu", "vm", tmp_path)
    assert report["microsandbox_copies"] == copies
    assert report["passed"] is (copies == 1)


@pytest.mark.parametrize("extension", ["tar.gz", "zip"])
def test_measurement_packages_only_binary_and_license(tmp_path, extension):
    binary, license_path, archive = tmp_path / "binary", tmp_path / "license", tmp_path / f"out.{extension}"
    binary.write_bytes(b"binary")
    license_path.write_text("license")
    size.package(binary, license_path, archive)
    if extension == "zip":
        with zipfile.ZipFile(archive) as output:
            assert output.namelist() == ["a13n-envd.exe", "LICENSE"]
            assert output.read("a13n-envd.exe") == b"binary"
    else:
        with tarfile.open(archive) as output:
            assert output.getnames() == ["a13n-envd", "LICENSE"]
            assert output.extractfile("a13n-envd").read() == b"binary"


def test_failure_writes_report_and_checks_uncompressed_size(tmp_path):
    binary = tmp_path / "a13n-envd"
    with binary.open("wb") as output:
        output.truncate(size.BUDGETS["default"][0] + 1)
    report = tmp_path / "report.json"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/envd_size.py"),
            "--binary",
            str(binary),
            "--target",
            "x86_64-unknown-linux-gnu",
            "--output",
            str(report),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    data = json.loads(report.read_text())
    assert data["errors"] == ["Binary exceeds size budget"]
    assert data["archive_bytes"] < data["archive_budget"]
