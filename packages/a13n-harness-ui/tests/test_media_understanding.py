import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import yaml
from a13n_harness.errors import ModelResolutionError
from a13n_harness.toolsets.file_media import (
    AgentMediaUnderstandingProvider,
    MediaUnderstandingError,
    MediaUnderstandingRequest,
    MediaUnderstandingResult,
)
from a13n_harness_ui.composition import AgentCompositionResolver, AgentReconstructor, ThreadCompositionSelection
from a13n_harness_ui.composition.models import ResolvedRunComposition
from a13n_harness_ui.configuration import load_harness_ui_configuration
from a13n_harness_ui.configuration.models import HarnessUiDocument
from a13n_harness_ui.errors import ConfigurationError
from a13n_harness_ui.media_understanding import FileMediaUnderstanding
from a13n_harness_ui.model_runtime import HarnessUiModelResolver, model_recipe_id
from pydantic_ai.models.test import TestModel

pytestmark = pytest.mark.anyio


def write_configuration(path: Path, media=None) -> Path:
    root = path / "custom.yaml"
    root.write_text(yaml.safe_dump({"schema_version": "1", "media_understanding": media or {}}))
    (path / "models").mkdir()
    (path / "agents").mkdir()
    for name, capabilities in (
        ("text", []),
        ("vision", ["image_understanding", "video_understanding", "audio_understanding"]),
    ):
        (path / "models" / f"{name}.yaml").write_text(
            yaml.safe_dump(
                {
                    "schema_version": "1",
                    "kind": "model",
                    "id": f"model-{name}",
                    "name": name,
                    "route": "openai-chat:test",
                    "authentication": {"kind": "api_key", "env": "TEST_MEDIA_KEY"},
                    "settings": {"temperature": 0.7, "max_tokens": 111},
                    "model_characteristics": {"capabilities": capabilities},
                }
            )
        )
    (path / "agents" / "main.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "1",
                "kind": "agent",
                "id": "agent-main",
                "name": "Main",
                "model": "model-text",
            }
        )
    )
    return root


def selection():
    return ThreadCompositionSelection(
        thread_id="thread-test",
        version=1,
        project_id=None,
        agent_source_kind="agent",
        agent_source_id="agent-main",
        environment_profile_id="environment-native",
        harness_plugin_ids=(),
        environment_run_extension_ids=(),
        mcp_server_ids=(),
    )


def request(kind="image"):
    return MediaUnderstandingRequest(
        kind=kind, media_type=f"{kind}/test", source_name="test", source_bytes=b"test", instructions="Read the label"
    )


async def test_media_defaults_capture_complete_recipes_and_preserve_old_serialization(tmp_path):
    path = write_configuration(tmp_path, {"image": "model-vision", "video": "model-vision"})
    source = await load_harness_ui_configuration(path)
    captured = AgentCompositionResolver().resolve_run(source, selection())
    recipe = captured.media_understanding["image"]
    assert recipe.settings == {"temperature": 0.7, "max_tokens": 111}
    assert recipe.authentication.env == "TEST_MEDIA_KEY"
    reconstructed = AgentReconstructor(instrumentation=None).reconstruct(captured, subagent_operator=None)
    assert isinstance(reconstructed.file_media_understanding("harness-thread"), FileMediaUnderstanding)
    source.models["model-vision"].settings["temperature"] = 0
    assert recipe.settings["temperature"] == 0.7
    assert ResolvedRunComposition.model_validate_json(captured.model_dump_json()) == captured
    legacy = captured.model_dump(mode="json")
    legacy.pop("media_understanding")
    assert ResolvedRunComposition.model_validate_json(json.dumps(legacy)).model_dump(mode="json") == legacy
    assert "media_understanding" not in HarnessUiDocument().model_dump(mode="json")


@pytest.mark.parametrize("media", [{"image": "model-missing"}, {"video": "model-text"}])
async def test_media_defaults_reject_missing_or_incompatible_models(tmp_path, media):
    with pytest.raises(ConfigurationError):
        await load_harness_ui_configuration(write_configuration(tmp_path, media))


async def test_configured_media_is_lazy_and_takes_priority_over_environment(tmp_path, monkeypatch):
    source = await load_harness_ui_configuration(write_configuration(tmp_path, {"image": "model-vision"}))
    captured = AgentCompositionResolver().resolve_run(source, selection())
    recipe = captured.media_understanding["image"]
    resolver = HarnessUiModelResolver({model_recipe_id(recipe): recipe})
    resolve = AsyncMock(return_value=TestModel(custom_output_text="label"))
    monkeypatch.setattr(resolver, "resolve", resolve)
    ambient = AsyncMock()
    monkeypatch.setattr(AgentMediaUnderstandingProvider, "from_environment", ambient)
    media = FileMediaUnderstanding(captured.media_understanding, resolver, thread_id="actual-harness-thread")
    resolve.assert_not_called()
    result = await media.understand(request())
    assert result.text == "label"
    resolve.assert_awaited_once_with(model_recipe_id(recipe), thread_id="actual-harness-thread")
    ambient.assert_not_called()


async def test_partial_configuration_preserves_environment_for_other_media(tmp_path, monkeypatch):
    source = await load_harness_ui_configuration(write_configuration(tmp_path, {"image": "model-vision"}))
    captured = AgentCompositionResolver().resolve_run(source, selection())
    resolver = HarnessUiModelResolver({})
    provider = AsyncMock()
    provider.understand.return_value = MediaUnderstandingResult(text="video result")
    kinds = []

    def from_environment(*, kind):
        kinds.append(kind)
        return provider

    monkeypatch.setattr(AgentMediaUnderstandingProvider, "from_environment", from_environment)
    media = FileMediaUnderstanding(captured.media_understanding, resolver, thread_id="thread-child")
    assert (await media.understand(request("video"))).text == "video result"
    assert kinds == ["video"]


async def test_configured_failure_does_not_fall_back(tmp_path, monkeypatch):
    source = await load_harness_ui_configuration(write_configuration(tmp_path, {"image": "model-vision"}))
    captured = AgentCompositionResolver().resolve_run(source, selection())
    resolver = HarnessUiModelResolver({})
    resolve = AsyncMock(side_effect=ModelResolutionError("missing", code="model_credential_missing"))
    monkeypatch.setattr(resolver, "resolve", resolve)
    ambient = AsyncMock()
    monkeypatch.setattr(AgentMediaUnderstandingProvider, "from_environment", ambient)
    media = FileMediaUnderstanding(captured.media_understanding, resolver, thread_id="thread-test")
    with pytest.raises(MediaUnderstandingError, match="media_understanding_configuration_invalid"):
        await media.understand(request())
    ambient.assert_not_called()


async def test_tui_media_publication_validates_references_and_preserves_primary_selection(tmp_path, monkeypatch):
    from a13n_harness_ui.app import open_harness_ui_app
    from a13n_harness_ui.cli import CliRequest
    from a13n_harness_ui.configuration import ResourceMutationRequest
    from a13n_harness_ui.interactive.backend import SessionBackend
    from a13n_harness_ui.interactive.rendering import Status
    from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings

    path = write_configuration(tmp_path)
    monkeypatch.setenv("HARNESS_VIDEO_UNDERSTANDING_MODEL", "private-ambient-model")
    settings = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"))
    async with open_harness_ui_app(settings, configuration_path=path) as app:
        backend = SessionBackend(app, CliRequest(), tmp_path, Status())
        before = backend.overrides
        choices = await backend.media_default_choices("image")
        assert [choice.value for choice in choices] == ["default", "model-vision"]
        await backend.set_media_default("image", "model-vision")
        assert backend.overrides == before
        assert yaml.safe_load(path.read_text())["media_understanding"] == {"image": "model-vision"}
        assert (await backend.media_default_choices())[0].description == "vision"
        with pytest.raises(ConfigurationError):
            await app.delete_configuration(relative_path="models/vision.yaml")
        model_path = tmp_path / "models/vision.yaml"
        model = yaml.safe_load(model_path.read_text())
        model["model_characteristics"]["capabilities"] = []
        with pytest.raises(ConfigurationError):
            await app.mutate_configuration(
                relative_path="models/vision.yaml", request=ResourceMutationRequest(content=yaml.safe_dump(model))
            )
        with pytest.raises(ConfigurationError):
            await backend.set_media_default("audio", "model-text")
        assert (await app.current_configuration()).document.media_understanding.audio is None
        await backend.set_media_default("image", "default")
        assert (await app.current_configuration()).document.media_understanding.selections() == {}
        await app.delete_configuration(relative_path="models/vision.yaml")
        assert not model_path.exists()


async def test_auxiliary_settings_are_independent_and_cancellation_propagates(tmp_path, monkeypatch):
    import asyncio

    from pydantic_ai.messages import ModelResponse, TextPart
    from pydantic_ai.models.function import FunctionModel

    source = await load_harness_ui_configuration(write_configuration(tmp_path, {"image": "model-vision"}))
    captured = AgentCompositionResolver().resolve_run(source, selection())
    seen = []

    async def infer(messages, info):
        seen.append(info.model_settings)
        return ModelResponse(parts=[TextPart("description")])

    resolver = HarnessUiModelResolver({})
    monkeypatch.setattr(resolver, "resolve", AsyncMock(return_value=FunctionModel(infer)))
    media = FileMediaUnderstanding(captured.media_understanding, resolver, thread_id="child-thread")
    assert (await media.understand(request())).text == "description"
    assert seen == [{"temperature": 0.7, "max_tokens": 111}]
    monkeypatch.setattr(resolver, "resolve", AsyncMock(side_effect=asyncio.CancelledError))
    with pytest.raises(asyncio.CancelledError):
        await media.understand(request())


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("delegated", [False, True])
async def test_view_native_first_with_configured_auxiliary_in_root_and_child(tmp_path, monkeypatch, native, delegated):
    from a13n_harness_ui.app import open_harness_ui_app
    from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings
    from a13n_harness_ui.surfaces import RootOperationStatus
    from pydantic_ai import BinaryContent
    from pydantic_ai.messages import ModelRequest, ToolReturnPart, UserPromptPart
    from pydantic_ai.models.function import DeltaToolCall, FunctionModel

    path = write_configuration(tmp_path, {"image": "model-vision"})
    root = yaml.safe_load(path.read_text())
    root["defaults"] = {"agent": "agent-main", "project": "project-test"}
    root["tools"] = {"enable_codeact": False}
    path.write_text(yaml.safe_dump(root))
    (tmp_path / "projects").mkdir()
    (tmp_path / "projects/test.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "1",
                "kind": "project",
                "id": "project-test",
                "name": "Test",
                "roots": [{"path": str(tmp_path)}],
            }
        )
    )
    (tmp_path / "sample.png").write_bytes(b"\x89PNG")
    agent_path = tmp_path / "agents/main.yaml"
    agent = yaml.safe_load(agent_path.read_text())
    agent["capabilities"] = [{"capability": "dynamic_environment", "configuration": {"files_enabled": True}}]
    if delegated:
        child = {**agent, "id": "agent-worker", "name": "Worker"}
        (tmp_path / "agents/worker.yaml").write_text(yaml.safe_dump(child))
        agent["subagents"] = [{"agent": "agent-worker"}]
    agent_path.write_text(yaml.safe_dump(agent))
    if native:
        model_path = tmp_path / "models/text.yaml"
        model = yaml.safe_load(model_path.read_text())
        model["model_characteristics"]["capabilities"] = ["image_understanding"]
        model_path.write_text(yaml.safe_dump(model))
    returns = []
    binaries = []
    auxiliary_threads = []
    primary_threads = []

    async def stream(messages, info):
        if delegated and "delegate" in {tool.name for tool in info.function_tools}:
            returned = {
                part.tool_name
                for message in messages
                if isinstance(message, ModelRequest)
                for part in message.parts
                if isinstance(part, ToolReturnPart)
            }
            if "delegate" not in returned:
                name, args = "delegate", {"subagent_name": "agent-worker", "prompt": "Read image"}
            elif "wait_subagent" not in returned:
                name, args = "wait_subagent", {"timeout_seconds": 10}
            else:
                yield "child finished"
                return
            yield {0: DeltaToolCall(name=name, json_args=json.dumps(args), tool_call_id=f"call-{name}")}
            return
        for message in messages:
            if isinstance(message, ModelRequest):
                returns.extend(part for part in message.parts if isinstance(part, ToolReturnPart))
                for part in message.parts:
                    if isinstance(part, UserPromptPart) and isinstance(part.content, list):
                        binaries.extend(item for item in part.content if isinstance(item, BinaryContent))
        if not returns:
            yield {
                0: DeltaToolCall(
                    name="view",
                    json_args=json.dumps({"file_path": str(tmp_path / "sample.png"), "instructions": "Read label"}),
                    tool_call_id="call-view",
                )
            }
        else:
            yield "done"

    async def resolve(self, model_id, *, thread_id):
        recipe = self._recipes[model_id]
        if recipe.model_id == "model-vision":
            auxiliary_threads.append(thread_id)
            return TestModel(custom_output_text="label from auxiliary")
        primary_threads.append(thread_id)
        return FunctionModel(stream_function=stream)

    monkeypatch.setattr(HarnessUiModelResolver, "resolve", resolve)
    settings = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"))
    async with open_harness_ui_app(settings, configuration_path=path) as app:
        thread = await app.create_thread()
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Read the image")
        operation = await app.wait_root_operation(receipt.receipt_id)
        assert operation.status is RootOperationStatus.completed, operation
    assert returns
    if native:
        assert auxiliary_threads == []
        assert binaries and binaries[0].data == b"\x89PNG"
    else:
        assert len(auxiliary_threads) == 1
        assert auxiliary_threads[0] in primary_threads
        assert (auxiliary_threads[0] != primary_threads[0]) is delegated
        assert any(part.content == "label from auxiliary" for part in returns)
