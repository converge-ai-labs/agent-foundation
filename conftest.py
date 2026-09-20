"""Repository-wide pytest scheduling policy."""

import os
from pathlib import Path

import pytest

_REPOSITORY_ROOT = Path(__file__).parent
# Developer machines often export an HTTP proxy, and macOS falls back to the system
# proxy when no variable is set. Tests talk only to local fakes, so bypass every proxy.
for _name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
    os.environ.pop(_name, None)
    os.environ.pop(_name.lower(), None)
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "*"


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
