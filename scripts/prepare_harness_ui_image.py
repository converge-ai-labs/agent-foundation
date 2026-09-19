"""Stage wheels and locked third-party constraints for the Harness UI image.

Development uses source-workspace wheels. Releases reuse the exact UI wheel
published by the release job and resolve its declared internal dependency bounds.
Browser assets must already be prepared before a development build.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = (
    "a13n-envd-client",
    "a13n-logging",
    "a13n-harness",
    "a13n-stream-protocol",
    "a13n-harness-ui",
)


def prepare(target: Path, release_dist: Path | None = None) -> None:
    target = target.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="harness-ui-image-", dir=target.parent) as temporary:
        staging = Path(temporary)
        if release_dist is not None:
            wheels = list(release_dist.glob("a13n_harness_ui-*.whl"))
            if len(wheels) != 1:
                raise ValueError("Release distribution must contain exactly one Harness UI wheel")
            shutil.copy2(wheels[0], staging)
        else:
            for package in PACKAGES:
                subprocess.run(
                    ["uv", "build", "--wheel", "--package", package, "--out-dir", str(staging)],
                    cwd=ROOT,
                    check=True,
                )
        constraints = subprocess.run(
            [
                "uv",
                "export",
                "--locked",
                "--package",
                "a13n-harness-ui",
                "--no-dev",
                "--no-emit-workspace",
                "--no-hashes",
                "--no-header",
                "--no-annotate",
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        (staging / "constraints.txt").write_text(constraints, encoding="utf-8")
        if target.exists():
            # This command owns only its dedicated build-context directory.
            shutil.rmtree(target)
        shutil.copytree(staging, target)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-dist", type=Path)
    args = parser.parse_args()
    prepare(ROOT / "dist/a13n-harness-ui-image", args.release_dist)


if __name__ == "__main__":
    main()
