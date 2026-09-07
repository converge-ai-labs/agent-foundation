"""Run selected workspace tests once per package, keeping package processes separate."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, help="Override each suite's worker count; 0 runs without xdist")
    parser.add_argument("paths", nargs="*", help="Workspace test directories, files, or pytest node IDs")
    args = parser.parse_args(argv)
    if args.workers is not None and args.workers < 0:
        parser.error("--workers must be non-negative")

    roots = [*sorted(Path("packages").glob("*/tests")), Path("scripts/tests")]
    batches: dict[Path, list[str]] = {}
    for selection in args.paths or [str(root) for root in roots]:
        path = Path(selection.split("::", 1)[0]).resolve()
        owner = next((root for root in roots if path.is_relative_to(root.resolve())), None)
        if owner is None or not path.exists():
            parser.error(f"not a workspace test path: {selection}")
        batches.setdefault(owner, []).append(selection)

    for owner, selections in batches.items():
        workers = args.workers
        if workers is None:
            workers = 7 if owner == Path("packages/a13n-service/tests") else 2
        print(f"\n==> {owner} ({workers} workers)", flush=True)
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-n", str(workers), "--dist", "loadgroup", *selections],
            check=False,
        )
        if result.returncode:
            return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
