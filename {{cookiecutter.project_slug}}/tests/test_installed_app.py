"""Exercise the installed distribution, never a sys.path-injected source tree."""

import asyncio
import tomllib
from importlib import metadata, resources
from pathlib import Path

from a13n_service.app_definitions import discover_apps
from packaging.version import Version


def test_entry_point_discovery_and_packaged_frontend():
    installed = metadata.distribution("{{ cookiecutter.project_slug }}")
    entry = next(item for item in installed.entry_points if item.group == "a13n_service.app_plugins")
    app = entry.load()()
    assert entry.name == app.app_id == "{{ cookiecutter.app_id }}"
    project = tomllib.loads(Path("pyproject.toml").read_text())["project"]
    assert Version(installed.version) == Version(project["version"])
    assert any(item.distribution == installed.name and item.app_id == app.app_id for item in discover_apps())
    assert app.frontend is not None and app.frontend.bridge == 1
    root = resources.files(app.frontend.package).joinpath(app.frontend.directory)
    script = root.joinpath(app.frontend.entry).read_text()
    assert "@a13n/console" in script
    assert "createRoot" not in script and "react/jsx-runtime" not in script
    assert root.joinpath("index.css").read_text()


def test_summary_uses_the_supplied_workspace():
    installed = metadata.distribution("{{ cookiecutter.project_slug }}")
    entry = next(item for item in installed.entry_points if item.group == "a13n_service.app_plugins")
    app = entry.load()()
    route = next(route for router in app.routers for route in router.routes if route.path.endswith("/summary"))
    result = asyncio.run(route.endpoint(workspace_id="wsp_test"))
    assert result.workspace_id == "wsp_test"
    assert result.message == "Your App backend is connected."
    assert route.openapi_extra["x-a13n-mcp"] is True
