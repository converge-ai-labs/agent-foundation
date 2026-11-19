"""Exercise the installed distribution, never a sys.path-injected source tree."""

{% if cookiecutter.surface != 'frontend-only' -%}
import asyncio
{% endif -%}import tomllib
from importlib import metadata{% if cookiecutter.surface != 'backend-only' %}, resources{% endif %}
from pathlib import Path

from a13n_service.app_definitions import discover_apps
from packaging.version import Version


def test_entry_point_discovery():
    installed = metadata.distribution("{{ cookiecutter.project_slug }}")
    entry = next(item for item in installed.entry_points if item.group == "a13n_service.app_plugins")
    app = entry.load()()
    assert entry.name == app.app_id == "{{ cookiecutter.app_id }}"
    project = tomllib.loads(Path("pyproject.toml").read_text())["project"]
    assert Version(installed.version) == Version(project["version"])
    assert any(item.distribution == installed.name and item.app_id == app.app_id for item in discover_apps())
{% if cookiecutter.surface != 'backend-only' %}    assert app.frontend is not None and app.frontend.bridge == 1
    root = resources.files(app.frontend.package).joinpath(app.frontend.directory)
    script = root.joinpath(app.frontend.entry).read_text()
    assert "@a13n/console" in script
    assert "createRoot" not in script and "react/jsx-runtime" not in script
    assert root.joinpath("index.css").read_text()
    assert not root.joinpath("index.js.map").is_file()
{% else %}    assert app.frontend is None
{% endif -%}
{% if cookiecutter.surface == 'frontend-only' %}    assert not app.routers
{% else %}

def test_summary_uses_the_supplied_workspace():
    installed = metadata.distribution("{{ cookiecutter.project_slug }}")
    entry = next(item for item in installed.entry_points if item.group == "a13n_service.app_plugins")
    app = entry.load()()
    route = next(route for router in app.routers for route in router.routes if route.path.endswith("/summary"))
    result = asyncio.run(route.endpoint(workspace_id="wsp_test"))
    assert result.workspace_id == "wsp_test"
    assert result.message == "Your App backend is connected."
    assert route.openapi_extra["x-a13n-mcp"] is True
{% endif -%}
