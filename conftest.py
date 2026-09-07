"""Repository-wide pytest scheduling policy."""

from pathlib import Path

import pytest

_REPOSITORY_ROOT = Path(__file__).parent


# xdist reads group marks in this hook, so assign them before its hook runs.
@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if item.get_closest_marker("xdist_group") is not None:
            continue
        path = Path(item.path)
        # Keep file-local fixtures together while allowing independent files to
        # use different workers. Explicit groups retain cross-file constraints.
        item.add_marker(pytest.mark.xdist_group(path.relative_to(_REPOSITORY_ROOT).as_posix()))
