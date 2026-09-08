from __future__ import annotations

import argparse
from pathlib import Path

from prepare_a13n_harness_ui_assets import prepare_assets

REPOSITORY_ROOT = Path(__file__).parents[1]
DEFAULT_SOURCE = REPOSITORY_ROOT / "apps" / "a13n-harness-ui" / "dist"
DEFAULT_TARGET = REPOSITORY_ROOT / "packages" / "a13n-harness-ui" / "a13n_harness_ui" / "static"


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare Harness UI WebUI files for the Harness UI Python package.")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    args = parser.parse_args()

    try:
        prepare_assets(args.source, args.target)
    except (OSError, ValueError) as error:
        raise SystemExit(str(error)) from error

    print(f"Prepared Harness UI WebUI assets in {args.target}")


if __name__ == "__main__":
    main()
