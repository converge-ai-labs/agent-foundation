"""Run after building and installing the wheel into the host development environment."""

import re
from importlib import metadata, resources
from pathlib import Path

from a13n_service.app_definitions import discover_apps


def test_installed_wheel_discovers_the_app_and_contains_the_complete_frontend() -> None:
    installed = metadata.distribution("acme-reports-app-example")
    entry = next(item for item in installed.entry_points if item.group == "a13n_service.app_plugins")
    app = entry.load()()
    assert entry.name == app.app_id == "acme.reports"
    assert installed.version == "0.1.0"
    assert any(item.distribution == installed.name and item.app_id == app.app_id for item in discover_apps())
    assert app.frontend is not None and app.frontend.bridge == 1
    root = resources.files(app.frontend.package).joinpath(app.frontend.directory)
    entry_script = root.joinpath(app.frontend.entry).read_text()
    stylesheet = root.joinpath("index.css").read_text()
    assert "@a13n/console" in entry_script
    assert "createRoot" not in entry_script and "react/jsx-runtime" not in entry_script
    assert re.search(r"url\([^)]*\.woff2", stylesheet)
    assert "base64" not in stylesheet
    assert any(item.name.endswith(".woff2") for item in root.joinpath("assets").iterdir())
    assert any(item.name.endswith(".js") for item in root.joinpath("chunks").iterdir())
    assert root.joinpath("SpaceGrotesk-OFL.txt").is_file()
    files = {str(item) for item in installed.files or ()}
    assert "acme_reports/static/index.js" in files
    assert "acme_reports/static/index.css" in files
    assert Path(str(root)).is_dir()
