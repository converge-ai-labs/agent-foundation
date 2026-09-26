"""Repository-wide pytest scheduling and memory policy."""

import gc
import os
from pathlib import Path

import pytest
from anyio import lowlevel as anyio_lowlevel

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


# AnyIO's run variables by event loop, checked against anyio 4.14.2; if it is renamed, importing this module fails.
_ANYIO_RUN_VARS = anyio_lowlevel._run_vars


@pytest.hookimpl(trylast=True)
def pytest_runtest_teardown() -> None:
    """Let the test's event loop, and the fixture values its tasks hold, be collected once the test ends.

    AnyIO keys its run variables weakly by event loop, but its `_root_task` variable holds the task that ran a
    fixture's setup; that task's result is the fixture value and it references the loop, so the entry keeps its
    own key alive. Otherwise a worker ends a full run holding every test's application, about two million
    objects, and each full garbage collection, including the two pytest runs at exit, takes seconds.
    """
    for loop in list(_ANYIO_RUN_VARS):
        if loop.is_closed():
            del _ANYIO_RUN_VARS[loop]


def pytest_collection_finish() -> None:
    """Leave what collection loaded, the imported modules and the collected tests, out of later garbage collections.

    Those objects live until the process exits, yet every full collection would scan them again. Frozen, a full
    collection scans only what tests allocate, and the collections pytest and the interpreter run at exit are
    quick. Objects tests allocate are never frozen, so their leaks stay visible to the collector.
    """
    gc.collect()
    gc.freeze()
