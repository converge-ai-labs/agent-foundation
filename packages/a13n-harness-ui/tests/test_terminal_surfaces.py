from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.errors import ThreadError
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings
from a13n_harness_ui.surfaces import (
    NewThreadDefaults,
    RootOperationStatus,
    SkillReference,
    ThreadConfigurationMutationInput,
    ThreadConfigurationPatch,
    ThreadMetadataMutation,
    ThreadMetadataPatch,
)
from a13n_harness_ui.thread_files import AttachmentUpload, ComposerAttachment, ComposerInput
from anyio import Event, fail_after
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, FunctionModel

pytestmark = pytest.mark.anyio


def _settings(root: Path) -> HarnessUiSettings:
    return HarnessUiSettings(storage=StorageSettings(data_root=root))


def _write_configuration(
    root: Path,
    *,
    projects: tuple[tuple[str, str, Path], ...],
    skills: bool = False,
) -> Path:
    configuration = root / "a13n-harness-ui.yaml"
    configuration.write_text(f'schema_version: "1"\ndefaults:\n  project: {projects[0][0]}\n  agent: agent-main\n')
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

    async with open_harness_ui_app(
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

    async with open_harness_ui_app(
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

    async with open_harness_ui_app(
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


@pytest.mark.parametrize("change", ["selected", "unrelated", "source"])
async def test_skill_references_refresh_on_submission_and_keep_active_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
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

    async with open_harness_ui_app(
        _settings(tmp_path / "state"),
        configuration_path=configuration,
    ) as app:
        catalog = await app.skill_catalog()
        item = next(item for item in catalog.items if item.name == "review")
        reference = SkillReference(
            catalog_id=catalog.catalog_id,
            item_id=item.item_id,
            name=item.name,
        )
        assert await app.validate_skill_references((reference,)) == ("review",)
        if change == "selected":
            document.write_text("---\nname: review\ndescription: Review the new code carefully.\n---\n")
        elif change == "unrelated":
            other = skill.parent / "other"
            other.mkdir()
            (other / "SKILL.md").write_text("---\nname: other\ndescription: Another skill.\n---\n")
        else:
            # The same name now resolves to the lower-precedence user source.
            user_skill = tmp_path / "home" / ".agents" / "skills" / "review"
            user_skill.mkdir(parents=True)
            document = document.rename(user_skill / "SKILL.md")
        latest = await app.skill_catalog()
        assert latest.catalog_id != catalog.catalog_id
        latest_item = next(item for item in latest.items if item.name == "review")
        assert (latest_item.item_id == item.item_id) is (change == "unrelated")
        assert await app.validate_skill_references((reference,)) == ("review",)

        started, finish = Event(), Event()
        seen: list[ModelMessage] = []

        async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
            seen[:] = messages
            started.set()
            await finish.wait()
            yield "review complete"

        async def resolve(self, context, model_id):
            return FunctionModel(stream_function=model)

        monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
        thread = await app.create_thread()
        receipt = await app.submit_thread(
            thread_id=thread.thread_id, prompt="$review check this", skill_references=(reference,)
        )
        with fail_after(10):
            await started.wait()
        active = await app.skill_catalog(thread_id=thread.thread_id)
        assert active.context_kind == "active"
        assert active.catalog_id == latest.catalog_id
        assert active.items == latest.items

        # Editing live files must not replace the catalog pinned at admission.
        document.write_text("---\nname: review\ndescription: Changed while running.\n---\n")
        assert (await app.skill_catalog(thread_id=thread.thread_id)) == active
        steering = await app.steer_root_operation(
            receipt_id=receipt.receipt_id, message="$review focus", skill_references=(reference,)
        )
        assert steering.accepted
        finish.set()
        with fail_after(10):
            operation = await app.wait_root_operation(receipt.receipt_id)
        assert operation.status is RootOperationStatus.completed
        assert (await app.skill_catalog(thread_id=thread.thread_id)).catalog_id != active.catalog_id

        document.unlink()
        with pytest.raises(ThreadError) as unavailable:
            await app.submit_thread(
                thread_id=thread.thread_id, prompt="$review check again", skill_references=(reference,)
            )
        assert unavailable.value.code == "skill_reference_unavailable"
        assert await app.active_root_operation(thread.thread_id) is None
        assert '<skill-selection source="harness-ui">' in str(seen)
        saved = await app.get_thread_transcript(thread_id=thread.thread_id)
        parts = [part for entry in saved.entries for part in entry.parts]
        hints = [part for part in parts if part.metadata.source_id == "a13n-harness-ui.skills"]
        assert len(hints) == 2  # Submission and steering, once each.
        assert all(not part.metadata.display for part in hints)
        authored = [part for part in parts if part.text in ("$review check this", "$review focus")]
        assert len(authored) == 2
        assert all(
            part.metadata.model_extra["harness_ui"]["skills"]
            == [{"name": "review", "source_id": latest_item.source_id, "start": 0, "end": 7}]
            for part in authored
        )

    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=configuration) as restored:
        history = await restored.get_thread_transcript(thread_id=thread.thread_id)
        assert history.entries == saved.entries


@pytest.mark.parametrize("with_skill", [False, True])
@pytest.mark.parametrize("input_kind", ["plain", "composer", "attachment"])
async def test_steering_after_project_rename_does_not_read_current_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, with_skill: bool, input_kind: str
) -> None:
    configuration = _write_configuration(tmp_path, projects=(("project-main", "Main", tmp_path),), skills=True)
    started, finish = Event(), Event()
    seen: list[ModelMessage] = []

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        seen[:] = messages
        started.set()
        await finish.wait()
        yield "complete"

    async def resolve(self, context, model_id):
        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=configuration) as app:
        thread = await app.create_thread()
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="wait")
        with fail_after(10):
            await started.wait()
        catalog = await app.skill_catalog(thread_id=thread.thread_id)
        item = next(item for item in catalog.items if item.name == "harness-ui-configuration")
        references = (
            (SkillReference(catalog_id=catalog.catalog_id, item_id=item.item_id, name=item.name),) if with_skill else ()
        )
        project = configuration.parent / "projects" / "project-main.yaml"
        project.write_text(project.read_text().replace("name: Main", "name: Renamed"))
        await app.reload_configuration()
        assert (await app.skill_catalog(thread_id=thread.thread_id)) == catalog

        async def unreadable(*args, **kwargs):
            raise AssertionError("Active steering must not read stored configuration or checkpoints")

        # Simulate a shared generation that this running App cannot decode. The
        # live stream and its pinned catalog still own steering, including attachments.
        with monkeypatch.context() as patch:
            patch.setattr(app._store.objects, "read_model", unreadable)
            if with_skill:
                assert (await app.skill_catalog(thread_id=thread.thread_id)) == catalog
            else:
                # Plain guidance needs no catalog, even if its cached entry is absent.
                app._terminal_projections._active_skill_catalogs.clear()
                patch.setattr(app._terminal_projections, "skill_catalog", unreadable)
            text = "focus on the renamed project"
            message: str | ComposerInput = text
            if input_kind == "composer":
                message = ComposerInput(parts=(text,))
            elif input_kind == "attachment":
                message = ComposerInput(
                    parts=(
                        text,
                        ComposerAttachment(
                            upload=AttachmentUpload(
                                name="guidance.txt", media_type="text/plain", data=b"Review the change"
                            ),
                            label="file#1",
                        ),
                    )
                )
            steering = await app.steer_root_operation(
                receipt_id=receipt.receipt_id, message=message, skill_references=references
            )
            assert steering.accepted
            assert steering.enqueue_id is not None
        finish.set()
        with fail_after(10):
            operation = await app.wait_root_operation(receipt.receipt_id)
        assert operation.status is RootOperationStatus.completed
        assert "focus on the renamed project" in str(seen)


async def test_empty_skill_references_do_not_resolve_a_catalog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    configuration = _write_configuration(tmp_path, projects=(("project-main", "Main", tmp_path),))
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=configuration) as app:

        async def unexpected(**kwargs):
            raise AssertionError("There are no Skill references to resolve")

        monkeypatch.setattr(app._terminal_projections, "skill_catalog", unexpected)
        assert await app.validate_skill_references(()) == ()


@pytest.mark.parametrize("invalid", ["identity", "name", "duplicate", "mixed_duplicate", "ambiguous"])
async def test_skill_reference_validation_rejects_invalid_references(tmp_path: Path, invalid: str) -> None:
    configuration = _write_configuration(tmp_path, projects=(("project-main", "Main", tmp_path),), skills=True)
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=configuration) as app:
        catalog = await app.skill_catalog()
        item = next(item for item in catalog.items if item.name == "harness-ui-configuration")
        reference = SkillReference(catalog_id=catalog.catalog_id, item_id=item.item_id, name=item.name)
        references = (reference,)
        if invalid == "identity":
            references = (reference.model_copy(update={"item_id": "0" * 64}),)
        elif invalid == "name":
            references = (reference.model_copy(update={"name": "missing", "catalog_id": "0" * 64}),)
        elif invalid == "duplicate":
            references = (reference, reference)
        elif invalid == "mixed_duplicate":
            references = (reference, reference.model_copy(update={"catalog_id": "0" * 64, "item_id": "0" * 64}))
        else:
            catalog = catalog.model_copy(update={"items": (*catalog.items, item)})
            references = (reference.model_copy(update={"catalog_id": "0" * 64}),)
        with pytest.raises(ThreadError) as error:
            app._terminal_projections.validate_references_against(catalog, references)
        assert error.value.code == (
            "skill_reference_invalid" if "duplicate" in invalid else "skill_reference_unavailable"
        )


async def test_archived_activity_filters_before_pagination_and_binds_cursors(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    configuration = _write_configuration(tmp_path, projects=(("project-main", "Main", workspace),))
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=configuration) as app:
        archived = []
        for index in range(3):
            thread = await app.create_thread(
                defaults=NewThreadDefaults(project_id="project-main"), title=f"Stored {index}"
            )
            archived.append(
                await app.update_thread_metadata(
                    thread_id=thread.thread_id,
                    mutation=ThreadMetadataMutation(
                        expected_version=thread.metadata_version, patch=ThreadMetadataPatch(archived=True)
                    ),
                )
            )
        active = await app.create_thread(defaults=NewThreadDefaults(project_id="project-main"), title="Active")
        page = await app.thread_activity(project_id=None, archived_only=True, limit=2)
        assert page.total == 3 and len(page.rows) == 2 and page.next_cursor
        assert all(row.thread.archived for row in page.rows)
        remaining = await app.thread_activity(project_id=None, archived_only=True, limit=2, cursor=page.next_cursor)
        assert len(remaining.rows) == 1 and remaining.next_cursor is None
        assert {row.thread.thread_id for row in (*page.rows, *remaining.rows)} == {
            thread.thread_id for thread in archived
        }
        with pytest.raises(ThreadError, match="another query"):
            await app.thread_activity(project_id=None, include_archived=True, cursor=page.next_cursor)
        ordinary = await app.thread_activity(project_id=None)
        assert [row.thread.thread_id for row in ordinary.rows] == [active.thread_id]
        search = await app.thread_activity(project_id=None, archived_only=True, query="Stored 1")
        assert search.total == 1 and search.rows[0].thread.title == "Stored 1"
        await app.update_thread_metadata(
            thread_id=archived[0].thread_id,
            mutation=ThreadMetadataMutation(
                expected_version=archived[0].metadata_version,
                patch=ThreadMetadataPatch(archived=False),
            ),
        )
        assert (await app.thread_activity(project_id=None, archived_only=True)).total == 2
        assert (await app.thread_activity(project_id=None)).total == 2
