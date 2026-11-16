"""Check the shipped migration graphs without opening a database."""

from __future__ import annotations

import sys
from collections.abc import Iterable

from alembic.script import ScriptDirectory


def check_graphs(graphs: Iterable[tuple[str, ScriptDirectory]]) -> int:
    failed = False
    for name, scripts in graphs:
        heads = sorted(scripts.get_heads())
        if len(heads) == 1:
            print(f"{name}: one migration head ({heads[0]})")
            continue
        failed = True
        print(f"{name}: expected one migration head; found {len(heads)}", file=sys.stderr)
        for head in heads:
            revision = scripts.get_revision(head)
            assert revision is not None
            print(f"  {head}: {revision.path}", file=sys.stderr)
        print(
            "Reconcile concurrent branches with a reviewed Alembic merge revision, "
            "then rerun this check. Preserve revisions that may already be applied.",
            file=sys.stderr,
        )
    return int(failed)


def repository_graphs() -> tuple[tuple[str, ScriptDirectory], ...]:
    from a13n_harness_ui.storage.migration import MIGRATIONS_PATH
    from a13n_service.distribution import OSS
    from a13n_service.migrations.runner import configuration

    return (
        ("Harness UI", ScriptDirectory(str(MIGRATIONS_PATH))),
        ("Service OSS", ScriptDirectory.from_config(configuration(OSS))),
    )


def main() -> int:
    return check_graphs(repository_graphs())


if __name__ == "__main__":
    raise SystemExit(main())
