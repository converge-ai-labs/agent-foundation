"""Bound native provider tests so stalled process cleanup produces diagnostics."""

from pathlib import Path

import pytest

_TEST_ROOT = Path(__file__).parent


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if item.path.is_relative_to(_TEST_ROOT) and item.get_closest_marker("timeout") is None:
            item.add_marker(pytest.mark.timeout(120))
