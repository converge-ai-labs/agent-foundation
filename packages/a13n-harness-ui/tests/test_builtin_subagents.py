from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from a13n_harness_ui.cli import cli
from a13n_harness_ui.composition import AgentCompositionResolver, ThreadCompositionSelection
from a13n_harness_ui.composition.models import ResolvedRunComposition
from a13n_harness_ui.configuration import load_harness_ui_configuration
from a13n_harness_ui.configuration.loader import parse_canonical_markdown
from a13n_harness_ui.configuration.models import HarnessUiDocument, LoadedHarnessUiConfiguration
from a13n_harness_ui.configuration.setup import SetupSelection, preview_setup, publish_setup
from a13n_harness_ui.errors import CompositionError, ConfigurationError
from a13n_harness_ui.interactive.setup import SetupWizard
from a13n_harness_ui.subagents import BUILTIN_SUBAGENT_NAMES
from click.testing import CliRunner
from pydantic import ValidationError


@pytest.fixture(autouse=True)
def no_real_model_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    import pydantic_ai.models

    monkeypatch.setattr(pydantic_ai.models, "ALLOW_MODEL_REQUESTS", False)


def _source(tmp_path: Path, include: tuple[str, ...]) -> Path:
    path = tmp_path / "a13n-harness-ui.yaml"
    path.write_text(yaml.safe_dump({"schema_version": "2", "subagents": {"include": list(include)}}))
    resources = {
        "models/main.yaml": {
            "kind": "model",
            "id": "model-main",
            "name": "Main",
            "route": "openai:gpt-5",
            "authentication": {"kind": "api_key", "env": "TEST_KEY"},
        },
        "agents/main.yaml": {"kind": "agent", "id": "agent-main", "name": "Main", "model": "model-main"},
        "projects/main.yaml": {
            "kind": "project",
            "id": "project-main",
            "name": "Main",
            "roots": [{"path": str(tmp_path)}],
        },
    }
    for relative, resource in resources.items():
        target = tmp_path / relative
        target.parent.mkdir(exist_ok=True)
        target.write_text(yaml.safe_dump({"schema_version": "1", **resource}))
    return path


def _resolve(source):
    return AgentCompositionResolver().resolve_run(
        source,
        ThreadCompositionSelection(
            thread_id="thread-main",
            version=1,
            project_id="project-main",
            agent_source_kind="agent",
            agent_source_id="agent-main",
            environment_profile_id="environment-native",
            harness_plugin_ids=(),
            environment_run_extension_ids=(),
            mcp_server_ids=(),
        ),
    )


@pytest.mark.parametrize("include", [(), ("explorer",), BUILTIN_SUBAGENT_NAMES])
@pytest.mark.anyio
async def test_named_inclusion_captures_inheriting_leaf_children_without_local_files(tmp_path: Path, include) -> None:
    path = _source(tmp_path, include)
    source = await load_harness_ui_configuration(path)
    assert {f"subagent-builtin-{name}" for name in BUILTIN_SUBAGENT_NAMES} <= source.subagents.keys()
    composition = _resolve(source)
    assert tuple(child.name for child in composition.root.children) == include
    for child in composition.root.children:
        assert child.definition.model == composition.root.model
        assert child.definition.capabilities == composition.root.capabilities
        assert child.definition.tools == composition.root.tools
        assert child.definition.children == ()
        assert child.instruction
        assert child.definition.instructions
    assert not (tmp_path / "subagents").exists()
    assert not (tmp_path / "built-in-subagents").exists()
    captured = composition.model_dump_json()
    path.write_text('schema_version: "2"\nsubagents: {include: []}\n')
    assert not _resolve(await load_harness_ui_configuration(path)).root.children
    assert ResolvedRunComposition.model_validate_json(captured) == composition


@pytest.mark.anyio
async def test_explicit_builtin_selection_deduplicates_but_same_roster_name_fails(tmp_path: Path) -> None:
    path = _source(tmp_path, ("explorer",))
    agent_path = tmp_path / "agents/main.yaml"
    agent = yaml.safe_load(agent_path.read_text())
    agent["subagents"] = [{"markdown": "subagent-builtin-explorer"}]
    agent_path.write_text(yaml.safe_dump(agent))
    assert len(_resolve(await load_harness_ui_configuration(path)).root.children) == 1
    local = tmp_path / "subagents"
    local.mkdir()
    (local / "explorer.md").write_text("---\nname: explorer\ndescription: Custom explorer.\n---\nLocal body.\n")
    agent["subagents"] = [{"markdown": "subagent-explorer"}]
    agent_path.write_text(yaml.safe_dump(agent))
    with pytest.raises(CompositionError, match="duplicate roster names"):
        _resolve(await load_harness_ui_configuration(path))


@pytest.mark.anyio
async def test_explicit_agent_child_keeps_own_model_and_does_not_expand_defaults_recursively(tmp_path: Path) -> None:
    path = _source(tmp_path, ("explorer",))
    model_path = tmp_path / "models/reviewer.yaml"
    model = yaml.safe_load((tmp_path / "models/main.yaml").read_text())
    model.update(id="model-reviewer", route="openai:gpt-4.1")
    model_path.write_text(yaml.safe_dump(model))
    child = {
        "schema_version": "1",
        "kind": "agent",
        "id": "agent-reviewer",
        "name": "Reviewer",
        "model": "model-reviewer",
    }
    (tmp_path / "agents/reviewer.yaml").write_text(yaml.safe_dump(child))
    parent_path = tmp_path / "agents/main.yaml"
    parent = yaml.safe_load(parent_path.read_text())
    parent["subagents"] = [{"agent": "agent-reviewer"}]
    parent_path.write_text(yaml.safe_dump(parent))
    composition = _resolve(await load_harness_ui_configuration(path))
    reviewer, explorer = composition.root.children
    assert reviewer.name == "agent-reviewer"
    assert reviewer.definition.model.model_id == "model-reviewer"
    assert not reviewer.definition.children
    assert explorer.definition.model == composition.root.model


@pytest.mark.parametrize("include", [["missing"], ["explorer", "explorer"], "all"])
def test_invalid_inclusion_is_rejected(include) -> None:
    with pytest.raises(ValidationError):
        HarnessUiDocument.model_validate({"subagents": {"include": include}})


@pytest.mark.parametrize("model", ["inherit", "model-main"])
def test_markdown_model_overrides_are_not_a_second_agent_configuration(model: str) -> None:
    with pytest.raises(ConfigurationError, match="inherit the parent model"):
        parse_canonical_markdown(
            Path("demo.md"), f"---\nname: demo\ndescription: Demo\nmodel: {model}\n---\nBody.\n".encode()
        )


@pytest.mark.parametrize("choice", ["all", "none"])
@pytest.mark.anyio
async def test_advanced_setup_offers_subagents_and_only_publishes_inclusion(tmp_path: Path, choice: str) -> None:
    wizard = SetupWizard(advanced=True)
    wizard.accept("api")
    wizard.accept("openai:gpt-5")
    wizard.accept("env:TEST_KEY")
    wizard.accept("full-control")
    assert wizard.question is not None
    assert wizard.question.key == "subagents"
    assert wizard.question.choices == ("all", "none")
    wizard.accept(choice)
    wizard.accept("")
    assert wizard.question is None
    selection = SetupSelection.model_validate(wizard.selection(str(tmp_path)))
    path = tmp_path / "config/a13n-harness-ui.yaml"
    validator = AgentCompositionResolver().validate_generation
    preview = await preview_setup(path, selection, validate_candidate=validator)
    assert not any(name.startswith("subagents/") for name in preview.files)
    result = await publish_setup(path, selection, expected_generation=preview.generation, validate_candidate=validator)
    assert result.completed
    source = await load_harness_ui_configuration(path)
    assert source.document.subagents.include == (BUILTIN_SUBAGENT_NAMES if choice == "all" else ())
    assert not list(path.parent.glob("subagents/*.md"))
    preserved = selection.model_copy(update={"include_default_subagents": None})
    assert (
        yaml.safe_load((await preview_setup(path, preserved, validate_candidate=validator)).files[path.name])[
            "subagents"
        ]
        == yaml.safe_load(path.read_text())["subagents"]
    )


def test_cli_discovers_all_builtins_and_reports_selected_names(tmp_path: Path) -> None:
    path = _source(tmp_path, ("explorer",))
    result = CliRunner().invoke(cli, ["--config", str(path), "config", "subagents", "--format", "json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["configuration_key"] == "subagents.include"
    assert {item["name"]: item["included"] for item in data["subagents"]} == {
        "code-reviewer": False,
        "executor": False,
        "explorer": True,
    }


@pytest.mark.parametrize("historical_model", [None, "model-main"])
@pytest.mark.anyio
async def test_retained_generation_decodes_historical_markdown_models_without_changing_child_recipe(
    tmp_path: Path, historical_model: str | None
) -> None:
    from a13n_harness_ui.settings import StorageSettings
    from a13n_harness_ui.storage import ObjectKind, open_local_store

    path = _source(tmp_path, ())
    source = await load_harness_ui_configuration(path)
    historical = source.model_dump(mode="json")
    historical["subagents"]["subagent-legacy"] = {
        "id": "subagent-legacy",
        "name": "legacy",
        "description": "Retained child",
        "instruction": None,
        "model": historical_model,
        "tools": None,
        "body": "Retained instructions.",
    }
    historical["agents"]["agent-main"]["subagents"] = [{"markdown": "subagent-legacy"}]
    async with open_local_store(StorageSettings(data_root=tmp_path / "state")) as store:
        envelope = await store.objects.publish(
            object_kind=ObjectKind.configuration_generation,
            object_schema_version="1",
            payload=historical,
        )
        loaded = await store.objects.read_model(envelope.ref, LoadedHarnessUiConfiguration)
        parent = _resolve(loaded)
        selection = ThreadCompositionSelection(
            thread_id="thread-child",
            version=1,
            project_id="project-main",
            agent_source_kind="markdown",
            agent_source_id="subagent-legacy",
            environment_profile_id="environment-native",
            harness_plugin_ids=(),
            environment_run_extension_ids=(),
            mcp_server_ids=(),
        )
        # An admitting parent override is inherited only by the historical null case.
        parent_node = parent.root.model_copy(
            update={"model": parent.root.model.model_copy(update={"model_id": "model-override"})}
        )
        child = AgentCompositionResolver().resolve_run(loaded, selection, parent_node=parent_node).root
        assert child.model.model_id == (historical_model or "model-override")
        assert child.instructions == ("Retained instructions.",)
        assert "model" not in source.subagents["subagent-builtin-explorer"].model_dump()


@pytest.mark.parametrize("child_name", ["explorer", "agent-worker"])
@pytest.mark.anyio
async def test_native_delegate_wait_and_linked_resume_use_captured_inherited_or_agent_recipe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, child_name: str
) -> None:
    from a13n_harness_ui.app import open_harness_ui_app
    from a13n_harness_ui.cli import CliRequest
    from a13n_harness_ui.interactive.backend import SessionBackend
    from a13n_harness_ui.interactive.rendering import Status, StreamRenderer
    from a13n_harness_ui.model_runtime import HarnessUiModelResolver
    from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings
    from pydantic_ai.messages import ModelRequest, ToolReturnPart
    from pydantic_ai.models.function import DeltaToolCall, FunctionModel

    path = _source(tmp_path, ("explorer",))
    parent_path = tmp_path / "agents/main.yaml"
    parent = yaml.safe_load(parent_path.read_text())
    parent["subagents"] = [{"agent": "agent-worker"}]
    parent_path.write_text(yaml.safe_dump(parent))
    (tmp_path / "agents/worker.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "1",
                "kind": "agent",
                "id": "agent-worker",
                "name": "Worker",
                "model": "model-worker",
            }
        )
    )
    model = yaml.safe_load((tmp_path / "models/main.yaml").read_text())
    model.update(id="model-worker", route="openai:gpt-4.1")
    (tmp_path / "models/worker.yaml").write_text(yaml.safe_dump(model))
    calls = 0
    child_models = []

    async def build(self, recipe, authentication):
        async def stream(messages, info):
            nonlocal calls
            if "delegate" not in {tool.name for tool in info.function_tools}:
                child_models.append(recipe.model_id)
                yield "Child completed."
                return
            calls += 1
            if calls == 1:
                yield {
                    0: DeltaToolCall(
                        name="delegate",
                        json_args=json.dumps({"subagent_name": child_name, "prompt": "Bounded task"}),
                        tool_call_id="delegate-1",
                    )
                }
            elif calls in {2, 4}:
                yield {
                    0: DeltaToolCall(
                        name="wait_subagent", json_args='{"timeout_seconds": 10}', tool_call_id=f"wait-{calls}"
                    )
                }
            elif calls == 3:
                returned = next(
                    part
                    for message in messages
                    if isinstance(message, ModelRequest)
                    for part in message.parts
                    if isinstance(part, ToolReturnPart) and part.tool_name == "delegate"
                )
                value = returned.content
                if isinstance(value, str):
                    value = json.loads(value)
                assert isinstance(value, dict), value
                yield {
                    0: DeltaToolCall(
                        name="resume_subagent",
                        json_args=json.dumps({"execution_id": value["execution_id"], "prompt": "Follow-up task"}),
                        tool_call_id="resume-1",
                    )
                }
            else:
                yield "Parent completed."

        return FunctionModel(stream_function=stream)

    monkeypatch.setattr(HarnessUiModelResolver, "_api_key_model", build)
    async with open_harness_ui_app(
        HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"), pricing_auto_update=False),
        configuration_path=path,
    ) as app:
        backend = SessionBackend(app, CliRequest(agent_id="agent-main"), tmp_path, Status())
        renderer = StreamRenderer(backend.status)
        assert await backend.execute(renderer, prompt="Delegate and resume") == ""
        assert "Parent completed." in renderer.drain()
        page = await app.query_child_executions(parent_thread_id=backend.thread_id)
        assert len(page.executions) == 2
        assert {item.persisted_status for item in page.executions} == {"succeeded"}
        usage = await app.thread_usage(thread_id=backend.thread_id)
        assert usage.root.model_requests == 5
        assert usage.descendants.model_requests == 2
        assert usage.combined.model_requests == 7
        assert len(usage.recent_runs) == 3
        assert sum(run.totals.model_requests for run in usage.recent_runs if run.descendant) == 2

    assert child_models == (["model-main"] if child_name == "explorer" else ["model-worker"]) * 2
