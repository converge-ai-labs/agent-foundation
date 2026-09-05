"""Repository-wide pytest scheduling policy."""

from pathlib import Path

import pytest

_REPOSITORY_ROOT = Path(__file__).parent
_FOUNDATION_TEST_ROOT = _REPOSITORY_ROOT / "packages" / "foundation-service" / "tests"


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if item.get_closest_marker("xdist_group") is not None:
            continue
        path = Path(item.path)
        if path.is_relative_to(_FOUNDATION_TEST_ROOT):
            # Each worker owns its session-scoped containers and SQLite template.
            # Keep file-local fixtures together without serializing the whole suite.
            item.add_marker(pytest.mark.xdist_group(path.relative_to(_REPOSITORY_ROOT).as_posix()))
        else:
            item.add_marker(pytest.mark.xdist_group("remaining"))
