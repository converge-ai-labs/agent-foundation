"""Repository-wide pytest scheduling policy."""

from pathlib import Path

import pytest

_REPOSITORY_ROOT = Path(__file__).parent
_INFRASTRUCTURE_TEST_ROOT = _REPOSITORY_ROOT / "packages" / "foundation-service" / "tests"


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        path = Path(item.path)
        if path.is_relative_to(_INFRASTRUCTURE_TEST_ROOT):
            item.add_marker(pytest.mark.xdist_group("infrastructure"))
        elif item.get_closest_marker("xdist_group") is None:
            item.add_marker(pytest.mark.xdist_group("remaining"))
