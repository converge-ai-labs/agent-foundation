"""Mixed-version document tests, not a claim of whole historical-wheel support."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

import pytest
import yaml
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.composition import ThreadCompositionSelection
from a13n_harness_ui.configuration import LoadedHarnessUiConfiguration, load_harness_ui_configuration
from a13n_harness_ui.configuration.models import (
    AgentResource,
    HarnessUiDocument,
    McpServerResource,
    ModelResource,
    ProjectResource,
    ProjectRoot,
    ResourceId,
    StrictModel,
)
from a13n_harness_ui.configuration.setup import preview_setup, publish_setup
from a13n_harness_ui.errors import EnvironmentLifecycleError, HarnessUiError
from a13n_harness_ui.file_context import GitContextSource
from a13n_harness_ui.model_accounts.api_keys import ApiKeyInput, ApiKeyStore
from a13n_harness_ui.storage.objects import ObjectKind
from a13n_harness_ui.thread_files import AttachmentUpload, ThreadFiles
from pydantic import Field, SecretStr, ValidationError, create_model

from .test_app import _settings, _write_configuration
from .test_object_store import _object_store
from .test_setup import _selection, _validate

pytestmark = pytest.mark.anyio


async def test_legacy_context_window_survives_upgrade_and_webui_status(tmp_path):
    import httpx
    from a13n_harness_ui.storage import open_local_store
    from a13n_harness_ui.webui import create_webui

    root = _write_configuration(tmp_path)
    model_path = tmp_path / "models/primary.yaml"
    model_path.write_text(model_path.read_text() + "model_characteristics: {context_window: 128000}\n")
    original_yaml = model_path.read_bytes()
    # Reproduce the legacy wire shape under the unchanged normalization version.
    legacy = await load_harness_ui_configuration(root)
    payload = legacy.model_dump(mode="json")
    characteristics = payload["models"]["model-primary"]["model_characteristics"]
    characteristics["context_window"] = characteristics.pop("context_window_tokens")
    settings = _settings(tmp_path / "state").model_copy(update={"pricing_auto_update": False})
    async with open_local_store(settings.storage) as store:
        envelope = await store.objects.publish(
            object_kind=ObjectKind.configuration_generation, object_schema_version="1", payload=payload
        )
        await store.configurations.accept(
            generation_digest=legacy.source_digest,
            generation=envelope.ref,
            sources=(),
            resources=(),
            expected_current_digest=None,
        )
    # No source reload: the retained generation itself must remain readable.
    async with open_harness_ui_app(settings) as app:
        assert (await app.status()).candidate_error_code is None
        thread = await app.create_thread()
        thread_id = thread.thread_id
    server = create_webui(lambda: open_harness_ui_app(settings, configuration_path=root), api_key="test-key")
    async with server.router.lifespan_context(server):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server),
            base_url="http://127.0.0.1",
            headers={"Authorization": "Bearer test-key"},
        ) as client:
            response = await client.get("/api/status")
            assert response.status_code == 200
            status = response.json()["app"]
            assert status["candidate_error_code"] is None
            assert status["accepted_generation_digest"] == legacy.source_digest
            assert (await client.get(f"/api/threads/{thread_id}")).status_code == 200
    async with open_local_store(settings.storage) as store:
        assert await store.objects.read(envelope.ref) == envelope
        restored = await store.objects.read_model(envelope.ref, LoadedHarnessUiConfiguration)
        assert restored.models["model-primary"].model_characteristics.context_window_tokens == 128000
        assert await store.configurations.reference(legacy.source_digest) == envelope.ref
    assert model_path.read_bytes() == original_yaml
    # Repeated startup must not collide on the same source-generation digest.
    async with open_harness_ui_app(settings, configuration_path=root) as app:
        assert (await app.status()).candidate_error_code is None
        assert (await app.get_thread(thread_id)).thread.thread_id == thread_id


async def test_legacy_run_composition_reads_context_window_without_rewriting_object(tmp_path):
    from a13n_harness_ui.composition import AgentCompositionResolver, ResolvedRunComposition

    from .test_composition import _catalog, _write_source
    from .test_composition import _selection as composition_selection

    root = _write_source(tmp_path)
    model_path = tmp_path / "models/primary.yaml"
    model_path.write_text(model_path.read_text() + "model_characteristics: {context_window: 128000}\n")
    source = await load_harness_ui_configuration(root)
    composition = AgentCompositionResolver(_catalog()).resolve_run(source, composition_selection())
    payload = composition.model_dump(mode="json")
    pending = [payload]
    replacements = 0
    while pending:
        node = pending.pop()
        if isinstance(node, dict):
            if "context_window_tokens" in node:
                node["context_window"] = node.pop("context_window_tokens")
                replacements += 1
            pending.extend(node.values())
        elif isinstance(node, list):
            pending.extend(node)
    assert replacements > 0
    store, _ = _object_store(tmp_path / "state")
    envelope = await store.publish(object_kind=ObjectKind.run_composition, object_schema_version="1", payload=payload)
    assert await store.read_model(envelope.ref, ResolvedRunComposition) == composition
    assert await store.read(envelope.ref) == envelope


async def test_additive_configuration_fields_survive_nested_snapshot_round_trip(tmp_path, caplog):
    root = _write_configuration(tmp_path)
    extra = {"items": [1, True, None, "  private-future-value  "]}
    root.write_text(json.dumps({"schema_version": "1", "future_root": extra, "display": {"future_display": extra}}))
    for relative in ("models/primary.yaml", "agents/assistant.yaml", "projects/main.yaml"):
        path = tmp_path / relative
        raw = yaml.safe_load(path.read_text())
        raw["future_resource"] = extra
        if relative.startswith("projects/"):
            raw["defaults"] = {"future_default": extra}
            raw["roots"][0]["future_root"] = extra
        path.write_text(json.dumps(raw))
    loaded = await load_harness_ui_configuration(root)
    restored = LoadedHarnessUiConfiguration.model_validate_json(loaded.model_dump_json())
    assert restored == loaded
    payload = restored.model_dump(mode="json")
    assert payload["document"]["future_root"] == extra
    assert payload["document"]["display"]["future_display"] == extra
    for kind, resource in (("models", "model-primary"), ("agents", "agent-assistant"), ("projects", "project-main")):
        assert payload[kind][resource]["future_resource"] == extra
    assert payload["projects"]["project-main"]["defaults"]["future_default"] == extra
    assert payload["projects"]["project-main"]["roots"][0]["future_root"] == extra
    assert "future_display" in caplog.text
    assert "private-future-value" not in caplog.text


@pytest.mark.parametrize(
    "values", [{"schema_version": "2"}, {"tools": {"enable_codeact": "false"}}, {"process": {"log_level": "invalid"}}]
)
async def test_additive_reader_keeps_known_field_validation(values):
    with pytest.raises(ValidationError):
        HarnessUiDocument.model_validate_json(json.dumps(values))


async def test_authentication_transport_and_selection_contracts_remain_strict():
    model = {"schema_version": "1", "kind": "model", "id": "model-test", "name": "Test", "route": "openai:gpt-5"}
    for authentication in (
        {"kind": "future_auth"},
        {"kind": "api_key", "env": "TEST_KEY", "future_source": "other"},
        {"kind": "api_key", "env": "TEST_KEY", "credential_ref": "key-test"},
    ):
        with pytest.raises(ValidationError):
            ModelResource.model_validate_json(json.dumps({**model, "authentication": authentication}))
    with pytest.raises(ValidationError):
        McpServerResource.model_validate_json(
            json.dumps(
                {
                    "schema_version": "1",
                    "kind": "mcp_server",
                    "id": "mcp-test",
                    "name": "Test",
                    "transport": {"command": "test", "url": "https://example.test"},
                }
            )
        )
    with pytest.raises(ValidationError):
        AgentResource.model_validate_json(
            json.dumps(
                {
                    "schema_version": "1",
                    "kind": "agent",
                    "id": "agent-test",
                    "name": "Test",
                    "subagents": [{"agent": "agent-one", "markdown": "subagent-two"}],
                }
            )
        )
    with pytest.raises(ValidationError):
        ApiKeyInput.model_validate_json('{"credential_ref":"key-test","key":"value","future_input":true}')


async def test_empty_project_defaults_keep_pre_defaults_wire_shape(tmp_path):
    # Project fields before d31ad4e1; this checks the historical document reader,
    # not the SQLite or dependency compatibility of an entire previous release.
    old_project = create_model(
        "PreDefaultsProject",
        __base__=StrictModel,
        schema_version=(Literal["1"], ...),
        kind=(Literal["project"], ...),
        id=(ResourceId, ...),
        name=(str, Field(min_length=1, max_length=256)),
        position=(int, 0),
        roots=(tuple[ProjectRoot, ...], Field(min_length=1, max_length=64)),
    )
    current = ProjectResource(
        schema_version="1", kind="project", id="project-test", name="Test", roots=(ProjectRoot(path=str(tmp_path)),)
    )
    assert "defaults" not in current.model_dump()
    assert old_project.model_validate_json(current.model_dump_json()).id == current.id
    assert (
        ProjectResource.model_validate_json(
            old_project.model_validate_json(current.model_dump_json()).model_dump_json()
        )
        == current
    )
    explicit = current.model_copy(update={"defaults": type(current.defaults)(mcp_servers=())})
    assert explicit.model_dump(mode="json")["defaults"] == {"mcp_servers": []}


async def test_project_snapshot_decode_does_not_revisit_filesystem(tmp_path, monkeypatch):
    loaded = await load_harness_ui_configuration(_write_configuration(tmp_path))
    store, _ = _object_store(tmp_path / "objects")
    envelope = await store.publish(
        object_kind=ObjectKind.configuration_generation,
        object_schema_version="1",
        payload=loaded.model_dump(mode="json"),
    )
    (tmp_path / "workspace").rmdir()

    def unexpected_filesystem_access(*args, **kwargs):
        raise AssertionError("Retained Project validation must not resolve or stat paths")

    with monkeypatch.context() as patch:
        patch.setattr(Path, "resolve", unexpected_filesystem_access)
        patch.setattr(Path, "exists", unexpected_filesystem_access)
        patch.setattr(Path, "is_dir", unexpected_filesystem_access)
        assert await store.read_model(envelope.ref, LoadedHarnessUiConfiguration) == loaded
    reloaded = await load_harness_ui_configuration(tmp_path / "a13n-harness-ui.yaml")
    assert reloaded == loaded


async def test_source_path_normalization_affects_generation_not_original_byte_digest(tmp_path):
    root = _write_configuration(tmp_path)
    link = tmp_path / "alias"
    link.symlink_to(tmp_path / "workspace", target_is_directory=True)
    project = tmp_path / "projects/main.yaml"
    raw = yaml.safe_load(project.read_text())
    raw["roots"][0]["path"] = str(link)
    project.write_text(json.dumps(raw))
    before = await load_harness_ui_configuration(root)
    other = tmp_path / "other"
    other.mkdir()
    link.unlink()
    link.symlink_to(other, target_is_directory=True)
    after = await load_harness_ui_configuration(root)
    assert before.source("projects/main.yaml").source_digest == after.source("projects/main.yaml").source_digest
    assert before.source_digest != after.source_digest
    assert before.projects["project-main"].roots[0].path != after.projects["project-main"].roots[0].path


async def test_setup_preserves_unknown_fields_when_changing_known_selections(tmp_path):
    root = tmp_path / "config.yaml"
    future = {"nested": [None, "  exact  "]}
    root.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "future_root": future,
                "defaults": {"future_default": future},
                "subagents": {"include": [], "future_subagent": future},
            }
        )
    )
    selection = _selection(tmp_path, include_default_subagents=True)
    preview = await preview_setup(root, selection, validate_candidate=_validate())
    payload = yaml.safe_load(preview.files[root.name])
    assert payload["future_root"] == future
    assert payload["defaults"]["future_default"] == future
    assert payload["subagents"]["future_subagent"] == future
    assert payload["subagents"]["include"]
    assert (await publish_setup(root, selection, validate_candidate=_validate())).completed
    assert yaml.safe_load(root.read_text()) == payload


async def test_api_key_operations_preserve_unknown_document_fields(tmp_path):
    path = tmp_path / "auth.json"
    future = {"nested": [None, True, "  exact  "]}
    path.write_text(json.dumps({"version": 1, "keys": {"key-old": "old-secret"}, "future_metadata": future}))
    store = ApiKeyStore(path)
    assert await store.load("key-old") == "old-secret"
    await store.put(ApiKeyInput(credential_ref="key-new", key=SecretStr("new-secret")))
    assert json.loads(path.read_text()) == {
        "version": 1,
        "keys": {"key-old": "old-secret", "key-new": "new-secret"},
        "future_metadata": future,
    }
    await store.delete("key-old")
    assert json.loads(path.read_text()) == {"version": 1, "keys": {"key-new": "new-secret"}, "future_metadata": future}
    assert "secret" not in repr(await store.list())


@pytest.mark.parametrize("keys", [[], {"key-test": 123}])
async def test_api_key_known_shape_is_not_relaxed_or_overwritten(tmp_path, keys):
    path = tmp_path / "auth.json"
    original = json.dumps({"version": 1, "keys": keys, "future_metadata": {}})
    path.write_text(original)
    with pytest.raises(HarnessUiError):
        await ApiKeyStore(path).put(ApiKeyInput(credential_ref="key-test", key=SecretStr("replacement")))
    assert path.read_text() == original


async def test_attachment_metadata_accepts_additions_and_preserves_integrity(tmp_path):
    files = ThreadFiles(tmp_path)
    try:
        attachment = await files.stage("thread-test", AttachmentUpload(name="plain.txt", data=b"hello"))
        metadata = files.directory("thread-test") / "tmp/uploads" / attachment.attachment_id / "metadata.json"
        original = json.loads(metadata.read_text())
        # Fields before 8f202793; no gratuitous source:null for ordinary uploads.
        assert set(original) == {"attachment_id", "name", "media_type", "size"}
        original["future_caption"] = {"text": "display only"}
        metadata.write_text(json.dumps(original))
        assert await files.read("thread-test", attachment.attachment_id) == (attachment, b"hello")
        await files.retain("thread-test", attachment.attachment_id)
        retained = files.directory("thread-test") / "attachments" / attachment.attachment_id / "metadata.json"
        assert json.loads(retained.read_text()) == original
        for field, value in (("attachment_id", "attachment-other"), ("size", 1)):
            retained.write_text(json.dumps({**original, field: value}))
            with pytest.raises(ValueError, match="content has changed"):
                await files.read("thread-test", attachment.attachment_id)
    finally:
        await files.close()


async def test_unavailable_project_is_local_to_selected_run_and_history_survives_restart(tmp_path):
    root = _write_configuration(tmp_path)
    dormant = yaml.safe_load((tmp_path / "projects/main.yaml").read_text())
    dormant.update(id="project-dormant", roots=[{"path": str(tmp_path / "missing")}], future_caption="unused")
    (tmp_path / "projects/dormant.yaml").write_text(json.dumps(dormant))
    settings = _settings(tmp_path / "state")
    async with open_harness_ui_app(settings, configuration_path=root) as app:
        created = await app.create_thread()
        stored = await app._threads.get(created.thread_id)
        source = await app.current_configuration()
        assert source is not None
        assert "project-dormant" in source.projects
        selection = ThreadCompositionSelection(
            thread_id=stored.thread_id,
            version=stored.configuration.version,
            project_id=stored.configuration.project_id,
            local_roots=stored.configuration.local_roots,
            agent_source_kind=stored.configuration.agent_source.kind,
            agent_source_id=stored.configuration.agent_source.id,
            environment_profile_id=stored.configuration.environment_profile_id,
            harness_plugin_ids=stored.configuration.harness_plugin_ids,
            environment_run_extension_ids=stored.configuration.environment_run_extension_ids,
            mcp_server_ids=stored.configuration.mcp_server_ids,
        )
        executor = app._root_runs._executor
        published = await executor._compositions.publish(source, selection)
        plan = await executor._environments.prepare(published.value)
        assert "workspace" in plan.environments
        assert not (await plan.finalize()).cleanup_errors
        (tmp_path / "workspace").rmdir()
        assert await app.current_configuration() == source
        with pytest.raises(EnvironmentLifecycleError) as failure:
            await executor._environments.prepare(published.value)
        assert failure.value.code == "project_root_invalid"
        assert (await app._threads.get(created.thread_id)).configuration.project_id == "project-main"
    # An invalid new candidate must still fall back to the readable accepted snapshot.
    root.write_text('schema_version: "unsupported"\n')
    async with open_harness_ui_app(settings, configuration_path=root) as restarted:
        assert await restarted.current_configuration() == source
        status = await restarted.status()
        assert status.candidate_error_code == "settings_invalid"
        assert (await restarted._threads.get(created.thread_id)).configuration.project_id == "project-main"


async def test_attachment_provenance_keeps_required_nullable_fields(tmp_path):
    source = GitContextSource(
        repository_path=str(tmp_path),
        git_dir=str(tmp_path / ".git"),
        path="new-file",
        comparison="staged",
        head_oid=None,
        index_revision="index-revision",
        revision="diff-revision",
    )
    files = ThreadFiles(tmp_path / "state")
    try:
        attachment = await files.stage(
            "thread-test", AttachmentUpload(name="staged.patch", data=b"patch", source=source)
        )
        assert (await files.read("thread-test", attachment.attachment_id))[0].source == source
        await files.retain("thread-test", attachment.attachment_id)
        metadata = files.directory("thread-test") / "attachments" / attachment.attachment_id / "metadata.json"
        assert "head_oid" in json.loads(metadata.read_text())["source"]
        assert (await files.read("thread-test", attachment.attachment_id))[0].source == source
    finally:
        await files.close()
