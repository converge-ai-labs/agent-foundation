"""Generate one reviewed Agent UI migration against a disposable SQLite database."""

from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

from a13n_ui.storage.migration import DatabaseMigrator


def main() -> None:
    parser = argparse.ArgumentParser(prog="agent-ui-db-migrate")
    parser.add_argument("message")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="agent-ui-migration-") as temporary:
        DatabaseMigrator(Path(temporary) / "metadata.sqlite3").revision(args.message)


if __name__ == "__main__":
    main()
