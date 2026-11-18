"""Template contracts, including Jinja/GitHub-expression separation."""

import json
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
import yaml
from cookiecutter.exceptions import FailedHookException
from cookiecutter.main import cookiecutter

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "context",
    [
        {},
        {
            "project_name": 'A "quoted" App & tools',
            "project_slug": "orbit-tools",
            "module_name": "orbit_plugin",
            "app_id": "orbit.tools",
            "version": "2.3.4",
        },
    ],
)
def test_generate_independent_fullstack_project(tmp_path, context):
    project = Path(cookiecutter(str(ROOT), no_input=True, extra_context=context, output_dir=str(tmp_path)))
    manifest = tomllib.loads((project / "pyproject.toml").read_text())
    slug = context.get("project_slug", "my-service-app")
    module = context.get("module_name", "my_service_app")
    app_id = context.get("app_id", "example.my-app")
    assert manifest["project"]["name"] == slug
    assert manifest["project"]["description"] == context.get("project_name", "My Service App")
    assert manifest["project"]["entry-points"]["a13n_service.app_plugins"][app_id] == f"{module}.app:definition"
    assert "license" not in manifest["project"]
    assert not any("license" in path.name.lower() for path in project.rglob("*"))
    app_source = (project / f"src/{module}/app.py").read_text()
    compile(app_source, "app.py", "exec")
    assert "database=" not in app_source
    assert "AppDatabase" not in "".join(path.read_text() for path in (project / "src").rglob("*.py"))
    assert not any(path.is_dir() for path in (project / "src").rglob("migrations"))
    assert (project / "AGENTS.md").is_file()
    assert app_id in (project / "AGENTS.md").read_text()
    readme_text = (project / "README.md").read_text()
    assert "[local development guide](https://github.com/converge-ai-labs/agent-foundation/blob/" in readme_text
    compile((project / "tests/test_installed_app.py").read_text(), "test_installed_app.py", "exec")
    assert (project / "frontend/pnpm-lock.yaml").is_file()
    assert json.loads((project / "frontend/package.json").read_text())["private"] is True
    assert manifest["tool"]["uv"]["workspace"]["members"] == []
    workflow_text = (project / ".github/workflows/build.yml").read_text()
    workflow = yaml.safe_load(workflow_text)
    assert "${{ github.sha }}" in workflow_text
    assert workflow["permissions"] == {"contents": "read"}
    assert "Build and verify installed wheel" in [step.get("name") for step in workflow["jobs"]["build"]["steps"]]
    release_text = (project / ".github/workflows/release.yml").read_text()
    release = yaml.safe_load(release_text)
    assert release["jobs"]["publish"]["needs"] == "build"
    assert release["jobs"]["publish"]["environment"] == "pypi"
    assert "${{ secrets.PYPI_TOKEN }}" in release_text
    for path in project.rglob("*"):
        if path.is_file():
            assert "cookiecutter." not in path.read_text(), path


@pytest.mark.parametrize(
    "context",
    [
        {"project_slug": "../escape"},
        {"project_slug": "Bad Name"},
        {"module_name": "class"},
        {"module_name": "bad-module"},
        {"app_id": "Bad/Scope"},
        {"app_id": "acme.tools-"},
        {"version": "1.0"},
        {"service_ref": "main"},
        {"project_name": "bad\nname"},
    ],
)
def test_invalid_inputs_fail_before_generation(tmp_path, context):
    with pytest.raises(FailedHookException):
        cookiecutter(str(ROOT), no_input=True, extra_context=context, output_dir=str(tmp_path))


def test_wheel_path_follows_current_metadata_after_version_bump(tmp_path):
    project = Path(cookiecutter(str(ROOT), no_input=True, output_dir=str(tmp_path)))
    manifest = project / "pyproject.toml"
    manifest.write_text(manifest.read_text().replace('version = "0.1.0"', 'version = "0.2.0"'))
    result = subprocess.run(
        [sys.executable, "scripts/wheel_path.py"], cwd=project, check=True, capture_output=True, text=True
    )
    assert result.stdout.strip() == "dist/my_service_app-0.2.0-py3-none-any.whl"
