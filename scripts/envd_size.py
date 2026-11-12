"""Measure already-built release artifacts and enforce Envd distribution budgets."""

from __future__ import annotations

import argparse
import json
import os
import tarfile
import tempfile
import zipfile
from pathlib import Path

MIB = 1024 * 1024
# Keep both below the native updater's 256 MiB download/extraction limit.
BUDGETS = {"default": (32 * MIB, 16 * MIB), "slim": (28 * MIB, 14 * MIB), "vm": (224 * MIB, 128 * MIB)}


def package(binary: Path, license_path: Path, archive: Path) -> None:
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
            output.write(binary, "a13n-envd.exe")
            output.write(license_path, "LICENSE")
    else:
        with tarfile.open(archive, "w:gz", compresslevel=6) as output:
            output.add(binary, arcname="a13n-envd", recursive=False)
            output.add(license_path, arcname="LICENSE", recursive=False)


def measure(binary: Path, archive: Path, target: str, variant: str, build_dir: Path) -> dict:
    binary_budget, archive_budget = BUDGETS[variant]
    binary_bytes, archive_bytes = binary.stat().st_size, archive.stat().st_size
    copies = None
    errors = []
    if binary_bytes > binary_budget:
        errors.append("Binary exceeds size budget")
    if archive_bytes > archive_budget:
        errors.append("Archive exceeds size budget")
    if variant == "vm":
        # Check the actual upstream build input against final linked bytes. This
        # catches repeated include_bytes! constants independently of size budgets.
        data = binary.read_bytes()
        bundles = list(build_dir.glob("build/a13n-envd-*/out/microsandbox-runtime.tar.gz"))
        copies = max((data.count(bundle.read_bytes()) for bundle in bundles), default=0)
        if copies != 1:
            errors.append("Expected exactly one embedded Microsandbox runtime archive")
    return {
        "target": target,
        "variant": variant,
        "binary_bytes": binary_bytes,
        "archive_bytes": archive_bytes,
        "binary_budget": binary_budget,
        "archive_budget": archive_budget,
        "microsandbox_copies": copies,
        "passed": not errors,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--variant", choices=BUDGETS, default="default")
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--build-dir", type=Path, default=Path("target/release"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as temporary:
        archive = args.archive
        if archive is None:
            extension = "zip" if "windows" in args.target else "tar.gz"
            archive = Path(temporary) / f"envd.{extension}"
            package(args.binary, Path("LICENSE"), archive)
        report = measure(args.binary, archive, args.target, args.variant, args.build_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    summary = (
        f"### Envd size: {args.target} / {args.variant}\n\n"
        f"Binary: {report['binary_bytes'] / MIB:.2f} MiB / {report['binary_budget'] / MIB:.0f} MiB; "
        f"archive: {report['archive_bytes'] / MIB:.2f} MiB / {report['archive_budget'] / MIB:.0f} MiB.\n"
        f"Microsandbox archive copies: {report['microsandbox_copies']}; "
        f"budget check: {'PASS' if report['passed'] else 'FAIL'}.\n"
    )
    print(summary)
    if path := os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(path).open("a", encoding="utf-8") as output:
            output.write(summary)
    for error in report["errors"]:
        print(f"::error::{error}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
