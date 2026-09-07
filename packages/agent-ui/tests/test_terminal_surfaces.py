from __future__ import annotations

from pathlib import Path

import pytest
from a13n_ui.app import open_agent_ui_app
from a13n_ui.errors import ThreadError
from a13n_ui.settings import AgentUiSettings, StorageSettings
from a13n_ui.surfaces import (
    NewThreadDefaults,
    SkillReference,
    ThreadConfigurationMutationInput,
    ThreadConfigurationPatch,
)

pytestmark = pytest.mark.anyio


def _settings(root: Path) -> AgentUiSettings:
    return AgentUiSettings(storage=StorageSettings(data_root=root))


def _write_configuration(
    root: Path,
    *,
    projects: tuple[tuple[str, str, Path], ...],
    skills: bool = False,
) -> Path:
    configuration = root / "a13n-ui.yaml"
    configuration.write_text(f'schema_version: "2"\ndefaults:\n  project: {projects[0][0]}\n  agent: agent-main\n')
    resources = {
        "models/main.yaml": """
schema_version: "1"
kind: model
id: model-main
name: Main
route: openai:gpt-5
authentication: {kind: api_key, env: OPENAI_API_KEY}
""",
        "agents/main.yaml": f"""
schema_version: "1"
kind: agent
id: agent-main
name: Main Agent
model: model-main
{
            '''capabilities:
  - capability: skills
'''
            if skills
            else ""
        }""",
    }
    for project_id, name, project_root in projects:
        resources[f"projects/{project_id}.yaml"] = f"""
schema_version: "1"
kind: project
id: {project_id}
name: {name}
roots:
  - path: {project_root.as_posix()}
"""
    for relative_path, content in resources.items():
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content.lstrip())
    return configuration


async def test_launch_project_resolution_uses_first_root_specificity(tmp_path: Path) -> None:
    outer = tmp_path / "outer"
    inner = outer / "inner"
    inner.mkdir(parents=True)
    configuration = _write_configuration(
        tmp_path,
        projects=(
            ("project-outer", "Outer", outer),
            ("project-inner", "Inner", inner),
        ),
    )

    async with open_agent_ui_app(
        _settings(tmp_path / "state"),
        configuration_path=configuration,
    ) as app:
        selected = await app.resolve_launch_project(inner / ".")
        unmatched = await app.resolve_launch_project(tmp_path)
        explicit = await app.resolve_launch_project(tmp_path, project_id="project-outer")

    assert selected.kind == "selected"
    assert selected.project.project_id == "project-inner"
    assert unmatched.kind == "unmatched"
    assert explicit.kind == "selected"
    assert explicit.project.project_id == "project-outer"


async def test_launch_project_resolution_reports_equal_specificity_as_ambiguous(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    configuration = _write_configuration(
        tmp_path,
        projects=(
            ("project-one", "One", workspace),
            ("project-two", "Two", workspace),
        ),
    )

    async with open_agent_ui_app(
        _settings(tmp_path / "state"),
        configuration_path=configuration,
    ) as app:
        resolution = await app.resolve_launch_project(workspace)

    assert resolution.kind == "ambiguous"
    assert tuple(item.project_id for item in resolution.projects) == ("project-one", "project-two")


async def test_thread_activity_filters_projects_and_exposes_bounded_catalogs(tmp_path: Path) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    (first_root / "src").mkdir()
    (first_root / "src" / "main.py").write_text("print('ok')\n")
    configuration = _write_configuration(
        tmp_path,
        projects=(
            ("project-first", "First", first_root),
            ("project-second", "Second", second_root),
        ),
    )

    async with open_agent_ui_app(
        _settings(tmp_path / "state"),
        configuration_path=configuration,
    ) as app:
        first = await app.create_thread(
            defaults=NewThreadDefaults(project_id="project-first"),
            title="First task",
        )
        await app.create_thread(
            defaults=NewThreadDefaults(project_id="project-second"),
            title="Second task",
        )
        page = await app.thread_activity(project_id="project-first")
        all_projects = await app.thread_activity(project_id=None)
        selectors = await app.thread_selectors()
        paths = await app.complete_project_paths(project_id="project-first", query="main")
        patched = await app.patch_thread_configuration(
            thread_id=first.thread_id,
            mutation=ThreadConfigurationMutationInput(
                expected_version=first.configuration.version,
                patch=ThreadConfigurationPatch(environment_profile_id="environment-sandbox"),
            ),
        )

    assert tuple(row.thread.thread_id for row in page.rows) == (first.thread_id,)
    assert all_projects.total == 2
    assert selectors.agents[0].agent_id == "agent-main"
    assert selectors.environments[0].profile_id == "environment-native"
    assert paths.items[0].display == "workspace:src/main.py"
    assert patched.configuration.environment_profile_id == "environment-sandbox"


async def test_skill_reference_validation_rejects_stale_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    workspace = tmp_path / "workspace"
    skill = workspace / ".agents" / "skills" / "review"
    skill.mkdir(parents=True)
    document = skill / "SKILL.md"
    document.write_text("---\nname: review\ndescription: Review code carefully.\n---\n")
    configuration = _write_configuration(
        tmp_path,
        projects=(("project-main", "Main", workspace),),
        skills=True,
    )

    async with open_agent_ui_app(
        _settings(tmp_path / "state"),
        configuration_path=configuration,
    ) as app:
        catalog = await app.skill_catalog()
        item = catalog.items[0]
        reference = SkillReference(
            catalog_id=catalog.catalog_id,
            item_id=item.item_id,
            name=item.name,
        )
        assert await app.validate_skill_references((reference,)) == ("review",)
        document.write_text("---\nname: review\ndescription: Review the new code carefully.\n---\n")
        with pytest.raises(ThreadError) as stale:
            await app._terminal_projections.validate_skill_references((reference,))

    assert stale.value.code == "skill_reference_stale"
