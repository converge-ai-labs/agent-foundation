"""Run cross-version import and lifecycle coverage without code-generation tools.

Each file runs in a fresh pytest process so CLI logging setup cannot leak into
App output tests. Eager mode sets Python 3.15's filter before importing pytest.
Tests launching further interpreters own their child mode; native import tests
cover both modes explicitly. Existing component CI retains the complete suites.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

IMPORT_TESTS = [
    "packages/a13n-logging/tests/test_imports.py",
    "packages/a13n-envd-client/tests/test_imports.py",
    "packages/a13n-stream-protocol/tests/test_imports.py",
    "packages/a13n-harness/tests/test_imports.py",
    "packages/a13n-harness-ui/tests/test_lazy_imports.py",
    "packages/a13n-service/tests/test_lazy_imports.py",
]
LIFECYCLE_TESTS = [
    "packages/a13n-harness/tests/test_execution.py",
    "packages/a13n-harness/tests/test_codeact.py",
    "packages/a13n-harness/tests/test_tool_permissions.py",
    "packages/a13n-harness-ui/tests/test_cli.py",
    "packages/a13n-harness-ui/tests/test_interactive.py",
    "packages/a13n-harness-ui/tests/test_app.py",
    "packages/a13n-harness-ui/tests/test_mcp_runtime.py",
    "packages/a13n-service/tests/test_cli.py",
    "packages/a13n-service/tests/test_schema.py",
    "packages/a13n-service/tests/test_mcp.py",
    "packages/a13n-service/tests/test_s3_addressing.py",
    "packages/a13n-service/tests/test_s3_writes.py",
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--eager", action="store_true")
    parser.add_argument("--junit-directory", type=Path, default=Path("test-results"))
    args, pytest_args = parser.parse_known_args()
    if args.eager and sys.version_info < (3, 15):
        parser.error("--eager requires Python 3.15")
    bootstrap = "import sys; "
    if args.eager:
        bootstrap += "sys.set_lazy_imports_filter(lambda *args: False); "
    bootstrap += "import pytest; raise SystemExit(pytest.main(sys.argv[1:]))"
    tests = LIFECYCLE_TESTS if args.eager else [*IMPORT_TESTS, *LIFECYCLE_TESTS]
    failed = False
    for target in tests:
        path = Path(target)
        report = args.junit_directory / f"{path.parts[1]}-{path.stem}.xml"
        completed = subprocess.run(
            [sys.executable, "-c", bootstrap, target, *pytest_args, f"--junitxml={report}"],
            cwd=Path(__file__).resolve().parents[1],
        )
        failed |= completed.returncode != 0
    raise SystemExit(int(failed))


if __name__ == "__main__":
    main()
