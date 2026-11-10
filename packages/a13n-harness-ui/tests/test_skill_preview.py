"""Skill previews use the captured Environment pipeline without Run admission."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from a13n_envd_client import EIPDeviceConnection
from a13n_harness.capabilities.skills import SkillManager
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.environment_bindings import EnvironmentSelectionPatch
from a13n_harness_ui.storage import ThreadConfigurationMutation, ThreadConfigurationPatch
from a13n_harness_ui.storage.contracts import AgentResourceSource
from a13n_harness_ui.surfaces import NewThreadDefaults
from anyio import Event, create_task_group, sleep_forever

from .test_app import _settings, _write_configuration
from .test_device_runtime import requires_envd

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("migrated_databases")]


def _skill(root: Path, name: str, description: str = "Preview instructions.") -> Path:
    document = root / ".agents" / "skills" / name / "SKILL.md"
    document.parent.mkdir(parents=True, exist_ok=True)
    document.write_text(f"---\nname: {name}\ndescription: {description}\n---\n")
    return document


def _configuration(tmp_path: Path) -> Path:
    path = _write_configuration(tmp_path)
    agent = tmp_path / "agents" / "assistant.yaml"
    agent.write_text(agent.read_text() + "capabilities:\n  - capability: skills\n")
    return path


@pytest.mark.parametrize("cancel", [False, True])
async def test_preview_joins_scope_and_never_publishes_or_creates_user_sources(tmp_path, monkeypatch, cancel):
    path = _configuration(tmp_path)
    _skill(tmp_path / "workspace", "review")
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=path) as app:
        service = app._root_runs._executor._environments
        missing_user = tmp_path / "absent-user-skills"
        service._user_skills_root = missing_user
        roots = []
        original = service._assemble

        async def assemble(composition, *, thread_root, preview):
            roots.append(thread_root)
            return await original(composition, thread_root=thread_root, preview=preview)

        async def forbidden(*args, **kwargs):
            pytest.fail("Preview must not admit, publish state, or touch durable Thread files")

        monkeypatch.setattr(service, "_assemble", assemble)
        monkeypatch.setattr(service._thread_files, "touch", forbidden)
        monkeypatch.setattr(service, "_session_context", forbidden)
        monkeypatch.setattr(service, "_publish_state", forbidden)
        monkeypatch.setattr(app._store.environment_states, "get", forbidden)
        monkeypatch.setattr(app._store.envd_sessions, "list", forbidden)
        monkeypatch.setattr(app._store.objects, "publish_model", forbidden)
        if cancel:
            scanning = Event()

            async def block_scan(self, *, environment):
                scanning.set()
                await sleep_forever()

            monkeypatch.setattr(SkillManager, "scan_environment", block_scan)
            async with create_task_group() as group:
                group.start_soon(app.skill_catalog)
                await scanning.wait()
                assert roots[0].exists()
                group.cancel_scope.cancel()
        else:
            first = await app.skill_catalog()
            assert any(item.name == "review" for item in first.items)
            _skill(tmp_path / "workspace", "review", "Changed instructions.")
            second = await app.skill_catalog()
            assert next(item.description for item in second.items if item.name == "review") == "Changed instructions."
            assert second.catalog_id != first.catalog_id
        assert roots and all(not root.exists() for root in roots)
        assert not missing_user.exists()
        assert (await app.list_threads()).total == 0


async def test_submission_preview_resolves_next_agent_and_complete_environment_without_writes(tmp_path, monkeypatch):
    path = _configuration(tmp_path)
    agent = tmp_path / "agents" / "assistant.yaml"
    (tmp_path / "agents" / "other.yaml").write_text(
        agent.read_text()
        .replace("agent-assistant", "agent-other")
        .replace(
            "  - capability: skills", "  - capability: skills\n    configuration:\n      roots: [/workspace/extra]"
        )
    )
    selected = tmp_path / "selected"
    _skill(selected, "selected")
    extra = selected / "extra" / "explicit" / "SKILL.md"
    extra.parent.mkdir(parents=True)
    extra.write_text("---\nname: explicit\ndescription: Full selected Agent.\n---\n")
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=path) as app:
        thread = await app.create_thread()
        original = await app._threads.get(thread.thread_id)
        mutation = ThreadConfigurationMutation(
            expected_version=thread.configuration.version,
            patch=ThreadConfigurationPatch(agent_source=AgentResourceSource(id="agent-other")),
        )
        # Virtual layout is selected by the actual adapter, not Host path rewriting.
        from a13n_harness_ui.extensions.environment_adapters import NativeProjectAdapter

        monkeypatch.setattr(NativeProjectAdapter, "preserves_host_paths", property(lambda self: False))
        preview = await app._terminal_projections.skill_catalog(
            thread_id=thread.thread_id,
            mutation=mutation,
            environment=EnvironmentSelectionPatch(
                local_roots=(str(selected),),
                environment_profile_id="environment-native",
                environment_bindings=(),
                default_environment=None,
            ),
        )
        assert {"selected", "explicit"} <= {item.name for item in preview.items}
        saved = await app._threads.get(thread.thread_id)
        assert saved.configuration == original.configuration
        assert await app._store.envd_sessions.list(thread.thread_id) == {}


@requires_envd
@pytest.mark.parametrize("cancel", [False, True])
async def test_local_envd_preview_closes_ephemeral_sessions_before_removing_scratch(tmp_path, monkeypatch, cancel):
    path = _configuration(tmp_path)
    path.write_text(path.read_text() + f"envd:\n  executable: {os.environ['A13N_ENVD_TEST_BINARY']}\n")
    _skill(tmp_path / "workspace", "remote-read")
    sessions = []
    original_open = EIPDeviceConnection.open_session

    async def open_session(self, *args, **kwargs):
        session = await original_open(self, *args, **kwargs)
        sessions.append(session)
        return session

    monkeypatch.setattr(EIPDeviceConnection, "open_session", open_session)
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=path) as app:
        service = app._root_runs._executor._environments
        roots = []
        original_assemble = service._assemble

        async def assemble(composition, *, thread_root, preview):
            roots.append(thread_root)
            return await original_assemble(composition, thread_root=thread_root, preview=preview)

        monkeypatch.setattr(service, "_assemble", assemble)
        defaults = NewThreadDefaults(environment_profile_id="environment-envd")
        if cancel:
            original_scan = SkillManager.scan_environment
            scanning = Event()

            async def block_scan(self, *, environment):
                catalog = await original_scan(self, environment=environment)
                assert any(item.name == "remote-read" for item in catalog.items)
                scanning.set()
                await sleep_forever()

            monkeypatch.setattr(SkillManager, "scan_environment", block_scan)

            async def preview():
                await app.skill_catalog(defaults=defaults)

            async with create_task_group() as group:
                group.start_soon(preview)
                await scanning.wait()
                group.cancel_scope.cancel()
        else:
            catalog = await app.skill_catalog(defaults=defaults)
            assert any(item.name == "remote-read" for item in catalog.items)
        assert sessions and all(session.is_closed for session in sessions)
        assert roots and all(not root.exists() for root in roots)
        assert not service._sessions
        assert (await app.list_threads()).total == 0
        runtimes = service._reconstructor.envd._runtimes
        device = next(iter(runtimes.values())).running_device()
        assert device is not None and not device.is_closed


@requires_envd
@pytest.mark.parametrize("carrier", ["http", "websocket"])
async def test_device_preview_borrows_carrier_without_publishing_or_disrupting_other_sessions(
    tmp_path, monkeypatch, carrier
):
    import asyncio
    import socket

    from a13n_harness_ui.composition.models import ResolvedEnvironmentBinding
    from a13n_harness_ui.environment_bindings import EnvironmentBindingSelection

    from .test_device_runtime import TOKEN, app_listener, daemon, device_path, resource

    monkeypatch.setenv("DEVICE_TOKEN", TOKEN)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        endpoint = f"http://127.0.0.1:{sock.getsockname()[1]}"
    recipe = resource(carrier, endpoint)
    async with app_listener(tmp_path, recipe) as (app, _http, ws):
        agent = tmp_path / "agents" / "assistant.yaml"
        agent.write_text(agent.read_text() + "capabilities:\n  - capability: skills\n")
        await app.reload_configuration()
        async with daemon(tmp_path, carrier, endpoint=endpoint if carrier == "http" else ws) as remote:
            async with asyncio.timeout(10):
                while not (await app.device_info(recipe.id)).available:
                    await asyncio.sleep(0.03)
            _skill(remote, "device-skill")
            binding = EnvironmentBindingSelection(
                device_id=recipe.id, alias="build", working_directory=device_path(remote)
            )
            connector = await app._devices.bind(
                ResolvedEnvironmentBinding(device=recipe, selection=binding), environment_id="other-owner"
            )
            opened = await connector.open()
            assert opened.operations.files is not None
            try:
                defaults = NewThreadDefaults(
                    local_roots=(), environment_bindings=(binding,), default_environment="build"
                )
                service = app._root_runs._executor._environments

                async def forbidden(*args, **kwargs):
                    pytest.fail("Device preview must not acquire durable Session ownership or publish state")

                monkeypatch.setattr(service, "_session_context", forbidden)
                monkeypatch.setattr(service, "_publish_state", forbidden)
                monkeypatch.setattr(app._store.environment_states, "get", forbidden)
                catalog = await app.skill_catalog(defaults=defaults)
                item = next(item for item in catalog.items if item.name == "device-skill")
                assert item.source_id == "a13n-harness-ui:device:build"
                assert item.logical_path == f"/environment/build{device_path(remote)}/.agents/skills/device-skill"
                # The daemon allows only two Sessions. A second preview needs the first one's slot released.
                assert (await app.skill_catalog(defaults=defaults)).catalog_id == catalog.catalog_id
                text = await opened.operations.files.read_text(
                    device_path(remote / ".agents/skills/device-skill/SKILL.md")
                )
                assert "Preview instructions." in text.text
                assert (await app.device_info(recipe.id)).available
                assert (await app.list_threads()).total == 0
                assert not service._sessions
            finally:
                await opened.close()


@requires_envd
@pytest.mark.parametrize(
    "profile,delay", [("environment-native", 0), ("environment-envd", 0), ("environment-envd", 0.05)]
)
async def test_first_model_preparation_freezes_real_catalog_without_preview(tmp_path, monkeypatch, profile, delay):
    """Print descriptive timings; request counts and frozen content are the regression gate."""
    import json
    from collections import Counter
    from time import perf_counter

    from a13n_envd_client.requester import SessionRequester
    from a13n_harness_ui.model_runtime import HarnessUiModelResolver
    from a13n_harness_ui.surfaces import RootOperationStatus
    from pydantic_ai.models.function import FunctionModel

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    path = _configuration(tmp_path)
    path.write_text(path.read_text() + f"envd:\n  executable: {os.environ['A13N_ENVD_TEST_BINARY']}\n")
    for index in range(100):
        _skill(tmp_path / "workspace", f"measured-{index:03}")
    calls = Counter()
    original = SessionRequester.request

    async def request(self, method, params):
        calls[method.name] += 1
        if delay:
            from anyio import sleep

            await sleep(delay)
        return await original(self, method, params)

    monkeypatch.setattr(SessionRequester, "request", request)
    first_model = []

    async def model(messages, info):
        first_model.append((perf_counter(), dict(calls)))
        yield "done"

    async def resolve(self, context, model_id):
        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=path) as app:
        thread = await app.create_thread(defaults=NewThreadDefaults(environment_profile_id=profile))

        def forbidden_preview(*args, **kwargs):
            pytest.fail("Ordinary input must not scan an admission preview")

        monkeypatch.setattr(app._root_runs._executor._environments, "preview", forbidden_preview)
        for sample in range(3):
            calls.clear()
            started = perf_counter()
            receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="hello")
            operation = await app.wait_root_operation(receipt.receipt_id)
            assert operation.status is RootOperationStatus.completed
            observed, rpc = first_model[-1]
            # Initial attachment can probe readiness once; file operations do not.
            assert rpc.get("environment.readiness", 0) <= 1
            if profile == "environment-envd":
                assert 1 <= rpc.get("file.discover_documents", 0) <= 3
                assert rpc.get("file.stat", 0) < 10
            catalog = app._root_runs.skill_catalog(receipt.receipt_id)
            assert catalog is not None
            assert sum(item.name.startswith("measured-") for item in catalog.items) == 100
            print(
                json.dumps(
                    {
                        "case": "ui-first-model",
                        "profile": profile,
                        "delay_ms": delay * 1000,
                        "sample": sample,
                        "seconds": observed - started,
                        "rpc": rpc,
                    }
                )
            )
