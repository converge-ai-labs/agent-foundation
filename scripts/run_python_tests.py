"""Run selected workspace tests once per package, keeping package processes separate."""

from __future__ import annotations

import argparse
import contextlib
import os
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

LOCK_VARIABLE = "A13N_TEST_LOCK"


def default_workers(owner: Path) -> int:
    if owner == Path("packages/a13n-service/tests"):
        return 7
    # Leave headroom for the editor, the dev stack and a second checkout on the same machine.
    return max(2, min(8, (os.cpu_count() or 4) - 2))


@contextlib.contextmanager
def full_run_lock(enabled: bool) -> Iterator[None]:
    """Serialize whole-workspace runs on one machine so concurrent checkouts do not starve each other."""
    if not enabled or os.name == "nt":
        yield
        return
    import fcntl

    path = Path(os.environ.get(LOCK_VARIABLE) or Path(tempfile.gettempdir()) / "a13n-python-tests.lock")
    with path.open("w") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(f"waiting for another full test run holding {path}", flush=True)
            fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, help="Override each suite's worker count; 0 runs without xdist")
    parser.add_argument("--no-lock", action="store_true", help="Do not serialize whole-workspace runs per machine")
    parser.add_argument(
        "paths", nargs="*", help="Workspace test directories, files, or pytest node IDs (@FILE reads one per line)"
    )
    args = parser.parse_args(argv)
    if args.workers is not None and args.workers < 0:
        parser.error("--workers must be non-negative")
    requested: list[str] = []
    for entry in args.paths:
        if entry.startswith("@"):
            requested.extend(line.strip() for line in Path(entry[1:]).read_text().splitlines() if line.strip())
        else:
            requested.append(entry)

    roots = [*sorted(Path("packages").glob("*/tests")), Path("scripts/tests")]
    batches: dict[Path, list[str]] = {}
    for selection in requested or [str(root) for root in roots]:
        path = Path(selection.split("::", 1)[0]).resolve()
        owner = next((root for root in roots if path.is_relative_to(root.resolve())), None)
        if owner is None or not path.exists():
            parser.error(f"not a workspace test path: {selection}")
        batches.setdefault(owner, []).append(selection)

    with full_run_lock(enabled=not requested and not args.no_lock):
        for owner, selections in batches.items():
            workers = args.workers if args.workers is not None else default_workers(owner)
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
