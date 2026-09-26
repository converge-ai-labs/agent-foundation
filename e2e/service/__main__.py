"""Run the Service E2E scenarios: `python -m e2e.service [pytest arguments]`.

The report lists every journey with its outcome and names each skipped journey with its reason. Pass
`--require-all` to fail, instead of skip, a journey whose external dependency is unavailable. Journeys on hosted
sandbox vendors run only with `--hosted`, `-m hosted`, or a `-k` expression naming their vendor type.
"""

import sys
from pathlib import Path

import pytest

SUITE = Path(__file__).resolve().parent
# Testcontainers 4.13 deprecates its own readiness decorator on import; the notice says nothing about the suite.
TESTCONTAINERS_NOTICE = "The @wait_container_is_ready decorator is deprecated"


def main() -> None:
    arguments = [str(SUITE), "-v", "-rfEs", "--durations=0", "-W", f"ignore:{TESTCONTAINERS_NOTICE}:DeprecationWarning"]
    raise SystemExit(pytest.main([*arguments, *sys.argv[1:]]))


if __name__ == "__main__":
    main()
