"""The Session cutover preserves history and converts only mutable selections."""

import json

import pytest
from a13n_harness_ui.environment_bindings import EnvironmentBindingSelection
from a13n_harness_ui.storage.migration import DatabaseMigrator
from alembic import command
from sqlalchemy import create_engine, text


def seed(path, bindings):
    migrator = DatabaseMigrator(path)
    migrator._run(lambda config: command.upgrade(config, "dd0077431d5b"), write=True)
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO thread (thread_id, metadata_version, title, archived, created_at, updated_at, "
                "initial_state_schema_version, initial_state_digest, continuation_schema_version, continuation_digest) "
                "VALUES ('thread-old', 1, 'Preserved', 0, '2026-01-01', '2026-01-01', '1', :initial, '1', :continuation)"
            ),
            {"initial": "a" * 64, "continuation": "b" * 64},
        )
        connection.execute(
            text(
                "INSERT INTO thread_configuration (thread_id, version, agent_source_kind, agent_source_id, "
                "environment_profile_id, environment_bindings_json, default_environment, harness_plugin_ids_json, "
                "environment_run_extension_ids_json, mcp_server_ids_json) "
                "VALUES ('thread-old', 4, 'agent', 'agent-test', 'environment-native', :bindings, 'build', '[]', '[]', '[]')"
            ),
            {"bindings": json.dumps(bindings)},
        )
    return migrator, engine


def test_session_cutover_preserves_thread_and_converts_saved_policies(tmp_path):
    policy = {"destinations": {"mode": "allowlist"}}
    base = {"device_id": "device-build", "working_directory": "/work", "alias": "build"}
    bindings = [
        {**base, "egress": None},
        {**base, "alias": "source", "working_directory": "/source", "egress": policy},
        {**base, "alias": "current", "session": {"egress": policy}},
    ]
    migrator, engine = seed(tmp_path / "metadata.sqlite3", bindings)
    try:
        with engine.connect() as connection:
            before = dict(connection.execute(text("SELECT * FROM thread")).mappings().one())
            config = dict(connection.execute(text("SELECT * FROM thread_configuration")).mappings().one())
        migrator.upgrade()
        migrator.upgrade()
        migrator.verify_current()
        with engine.connect() as connection:
            assert dict(connection.execute(text("SELECT * FROM thread")).mappings().one()) == before
            after = dict(connection.execute(text("SELECT * FROM thread_configuration")).mappings().one())
            converted = json.loads(after.pop("environment_bindings_json"))
            config.pop("environment_bindings_json")
            assert after == config
            assert connection.execute(text("SELECT COUNT(*) FROM envd_session_binding")).scalar_one() == 0
        assert converted == [
            {**base, "session": {}},
            {**base, "alias": "source", "working_directory": "/source", "session": {"egress": policy}},
            bindings[2],
        ]
        for binding in converted:
            EnvironmentBindingSelection.model_validate(binding)
    finally:
        engine.dispose()


def test_conflicting_cutover_policy_rolls_back_and_can_be_corrected(tmp_path):
    binding = {
        "device_id": "device-build",
        "working_directory": "/work",
        "alias": "build",
        "egress": None,
        "session": {"egress": {"destinations": {"mode": "allowlist"}}},
    }
    migrator, engine = seed(tmp_path / "metadata.sqlite3", [binding])
    try:
        with pytest.raises(ValueError, match="Conflicting Session egress"):
            migrator.upgrade()
        with engine.begin() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "dd0077431d5b"
            saved = connection.execute(text("SELECT environment_bindings_json FROM thread_configuration")).scalar_one()
            assert json.loads(saved) == [binding]
            connection.execute(text("UPDATE thread_configuration SET environment_bindings_json = '[]'"))
        migrator.upgrade()
        migrator.verify_current()
    finally:
        engine.dispose()


@pytest.mark.anyio
@pytest.mark.usefixtures("migrated_databases")
@pytest.mark.parametrize(
    "legacy_session", [None, {"target": "old-device", "descriptor": {"session_id": "old-session"}}]
)
async def test_old_session_hints_are_inert_and_provider_state_is_preserved(tmp_path, legacy_session):
    from datetime import UTC, datetime

    from a13n_harness.environment.models import EnvironmentState
    from a13n_harness_ui.app import open_harness_ui_app
    from a13n_harness_ui.storage import ObjectKind
    from a13n_harness_ui.storage.contracts import EnvironmentBindingKey, StoredEnvironmentState

    from .test_app import _settings, _write_configuration

    configuration = _write_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=configuration) as app:
        thread = await app.create_thread()
        key = EnvironmentBindingKey(
            thread_id=thread.thread_id,
            environment_profile_id="environment-managed",
            profile_digest="a" * 64,
            adapter_key="test.managed",
            normalized_root="/work",
        )
        state = EnvironmentState(provider_key="test_managed", state_version="1", state={"instance_id": "original"})
        payload = StoredEnvironmentState(binding=key, state=state, created_at=datetime.now(UTC)).model_dump(mode="json")
        payload["envd_session"] = legacy_session
        envelope = await app._store.objects.publish(
            object_kind=ObjectKind.environment_state, object_schema_version="1", payload=payload
        )
        await app._store.environment_states.select(key=key, expected=None, replacement=envelope.ref)
        head = await app._store.environment_states.get(key)
        assert head.state == envelope.ref
        restored = await app._root_runs._executor._environments._load_state(
            key=key, reference=head.state, provider_key="test_managed"
        )
        assert restored == state
        assert await app._store.objects.read(envelope.ref) == envelope
        assert await app._store.envd_sessions.list(thread.thread_id) == {}


@pytest.mark.anyio
@pytest.mark.usefixtures("migrated_databases")
async def test_session_cutover_accepts_unchanged_configuration_sources(tmp_path, monkeypatch):
    from a13n_harness_ui.composition import AgentCompositionResolver, CompositionAcceptanceService
    from a13n_harness_ui.configuration import load_harness_ui_configuration, loader
    from a13n_harness_ui.settings import StorageSettings
    from a13n_harness_ui.storage import ObjectKind, open_local_store

    from .test_composition import _catalog, _write_source
    from .test_device_configuration import device

    path = _write_source(tmp_path)
    devices = tmp_path / "devices"
    devices.mkdir()
    (devices / "build.yaml").write_text(device().model_dump_json())
    project = tmp_path / "projects" / "main.yaml"
    with project.open("a") as stream:
        stream.write(
            "defaults:\n  default_environment: build\n  environment_bindings:\n"
            "    - {device_id: device-build, working_directory: /work, alias: build}\n"
        )
    with monkeypatch.context() as patch:
        patch.setattr(loader, "_NORMALIZATION_VERSION", "1")
        old = await load_harness_ui_configuration(path)
    current = await load_harness_ui_configuration(path)
    assert current.sources == old.sources
    # The old normalized binding carried top-level egress. Historical objects
    # remain immutable; the new normalizer must publish under a different key.
    payload = old.model_dump(mode="json")
    saved = payload["projects"]["project-main"]["defaults"]["environment_bindings"][0]
    saved["egress"] = saved.pop("session")["egress"]
    async with open_local_store(StorageSettings(data_root=tmp_path / "data")) as store:
        previous = await store.objects.publish(
            object_kind=ObjectKind.configuration_generation, object_schema_version="1", payload=payload
        )
        await store.configurations.accept(
            generation_digest=old.source_digest,
            generation=previous.ref,
            sources=(),
            resources=(),
            expected_current_digest=None,
        )
        service = CompositionAcceptanceService(store, AgentCompositionResolver(_catalog()))
        accepted = await service.accept(current, expected_current_digest=old.source_digest)
        assert accepted.source_digest != old.source_digest
        assert await service.current() == current
        assert await store.objects.read(previous.ref) == previous
        again = await service.accept(current, expected_current_digest=current.source_digest)
        assert again == accepted
