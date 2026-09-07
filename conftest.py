"""Repository-wide pytest scheduling policy."""

from pathlib import Path

import pytest

_REPOSITORY_ROOT = Path(__file__).parent
_A13N_SERVICE_TEST_ROOT = _REPOSITORY_ROOT / "packages" / "a13n-service" / "tests"


# xdist reads group marks in this hook, so assign them before its hook runs.
@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if item.get_closest_marker("xdist_group") is not None:
            continue
        path = Path(item.path)
        if path.is_relative_to(_A13N_SERVICE_TEST_ROOT):
            # Each worker owns its session-scoped containers and SQLite template.
            # Keep file-local fixtures together without serializing the whole suite.
            item.add_marker(pytest.mark.xdist_group(path.relative_to(_REPOSITORY_ROOT).as_posix()))
        else:
            item.add_marker(pytest.mark.xdist_group("remaining"))
