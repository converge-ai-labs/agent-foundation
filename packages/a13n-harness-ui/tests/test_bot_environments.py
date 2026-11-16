"""Bot uses captured Project targets through ordinary Environment tools."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import yaml
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.bot_models import BOT_THREAD_ID, BotSubmit
from a13n_harness_ui.composition import ThreadCompositionSelection
from a13n_harness_ui.environment_runtime import _target_composition, target_mount_aliases
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from .test_app import _settings
from .test_bot import _write_configuration, consumed, ready
from .test_device_runtime import requires_envd

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("migrated_databases")]


def project(tmp_path: Path, name: str, *, root: Path, profile: str = "environment-native") -> None:
    (tmp_path / "projects" / f"{name}.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "1",
                "kind": "project",
                "id": f"project-{name}",
                "name": name,
                "roots": [{"path": str(root)}],
                "defaults": {"environment_profile": profile},
            }
        )
    )


async def test_bot_opens_projects_and_uses_native_files_and_shell_without_new_threads(tmp_path, monkeypatch):
    root = _write_configuration(tmp_path)
    project(tmp_path, "missing", root=tmp_path / "not-present")
    step = 0
    route = {}

    async def model(messages, info):
        nonlocal step
        tools = {tool.name for tool in info.function_tools}
        assert {"shell_exec", "list_environment_targets", "open_environment_target"} <= tools
        assert "computer_observe" not in tools
        returned = [part for message in messages for part in message.parts if isinstance(part, ToolReturnPart)]
        if step == 0:
            name, args = "list_environment_targets", {}
        elif step == 1:
            targets = returned[-1].content["targets"]
            assert {item["target_id"] for item in targets} == {"project-main", "project-missing", "host-desktop"}
            name, args = "open_environment_target", {"target_id": "project-main"}
        elif step == 2:
            result = returned[-1].content
            assert result["ok"] and result["default_unchanged"]
            route.update(result["mounts"][0])
            name, args = "write", {"file_path": f"{route['path']}/result.txt", "content": "native Bot write"}
        elif step == 3:
            name, args = "shell_exec", {"alias": route["alias"], "command": "pwd"}
        elif step == 4:
            assert str(tmp_path / "workspace") in str(returned[-1].content)
            name, args = "open_environment_target", {"target_id": "project-missing"}
        elif step == 5:
            assert returned[-1].content["ok"] is False
            name, args = "open_environment_target", {"target_id": "project-main"}
        else:
            assert step == 6
            assert returned[-1].content["ok"]
            yield "Verified native Project access."
            return
        step += 1
        yield {0: DeltaToolCall(name=name, json_args=json.dumps(args), tool_call_id=f"target-{step}")}

    async def resolve(self, model_id, *, thread_id):
        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "resolve", resolve)
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=root, host_mode="webui") as app:
        environments = app._root_runs._executor._environments
        runtime = AsyncMock(side_effect=AssertionError("Unused desktop must remain unprepared"))
        monkeypatch.setattr(environments._reconstructor, "sandbox_runtime", runtime)
        bot = await ready(app)
        await bot.submit(BotSubmit(input_id="targets", text="Perform a short task in Main"))
        item = await consumed(app, "targets")
        operation = await app._root_runs.latest(BOT_THREAD_ID)
        assert item.state == "completed", operation.failure if operation else item.error
        assert (tmp_path / "workspace/result.txt").read_text() == "native Bot write"
        assert not (tmp_path / "bot/result.txt").exists()
        assert (await app.list_threads()).total == 1  # Only the reserved Bot root exists.
        runtime.assert_not_awaited()


async def test_bot_captures_independent_session_selections_and_isolates_unavailable_target(tmp_path):
    root = _write_configuration(tmp_path)
    for name in ("first", "second"):
        folder = tmp_path / name
        folder.mkdir()
        project(tmp_path, name, root=folder, profile="environment-envd")
    project(tmp_path, "missing", root=tmp_path / "absent", profile="environment-sandbox")
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=root) as app:
        thread = await app._threads.bot_thread()
        source = await app.current_configuration()
        executor = app._root_runs._executor
        service = executor._environments
        recipe = app._mcp_app_owners.resolver.resolve_run(source, ThreadCompositionSelection.from_thread(thread))
        admitted = await service.admit(recipe)
        targets = {item.target_id: item for item in admitted.environment_targets}
        first, second = targets["project-first"], targets["project-second"]
        assert first.environment_profile.profile_id == second.environment_profile.profile_id == "environment-envd"
        assert first.session_versions and second.session_versions
        assert first.session_versions.keys().isdisjoint(second.session_versions)
        assert targets["project-missing"].unavailable is not None
        assert not first.unavailable and not second.unavailable
        assert target_mount_aliases(first) != target_mount_aliases(second)
        for target in (first, second, targets["host-desktop"]):
            await service.validate_admission(_target_composition(admitted, target))
        # Repeated root admission retains each target's selection instead of removing other scopes.
        repeated = await service.admit(recipe)
        assert [target.session_versions for target in repeated.environment_targets] == [
            target.session_versions for target in admitted.environment_targets
        ]
        assert all(head.session is None for head in (await app._store.envd_sessions.list(BOT_THREAD_ID)).values())
        # The captured recipe is independent of subsequent accepted resource edits.
        (tmp_path / "projects/first.yaml").unlink()
        assert targets["project-first"].project_roots == (str(tmp_path / "first"),)


@pytest.mark.parametrize("profile", ["environment-native", pytest.param("environment-envd", marks=requires_envd)])
async def test_bot_recovery_uses_captured_target_alias_and_original_session_only(tmp_path, monkeypatch, profile):
    import os

    from a13n_envd_client import EIPDeviceConnection
    from a13n_harness import AgentIdentityRef, AgentInstanceContext
    from a13n_harness.environment.models import EnvironmentError

    root = _write_configuration(tmp_path)
    if profile == "environment-envd":
        root.write_text(root.read_text() + f"envd:\n  executable: {os.environ['A13N_ENVD_TEST_BINARY']}\n")
    retained, unrelated = tmp_path / "retained", tmp_path / "unrelated"
    retained.mkdir()
    unrelated.mkdir()
    (retained / "evidence.txt").write_text("original Bot target")
    project(tmp_path, "recovery", root=retained, profile=profile)
    path = tmp_path / "projects/recovery.yaml"
    value = yaml.safe_load(path.read_text())
    value["roots"].insert(0, {"path": str(unrelated)})
    path.write_text(yaml.safe_dump(value))
    project(tmp_path, "twin", root=retained, profile=profile)
    opened = []
    original_open = EIPDeviceConnection.open_session

    async def count_open(self, *args, **kwargs):
        session = await original_open(self, *args, **kwargs)
        opened.append(session.session_id)
        return session

    monkeypatch.setattr(EIPDeviceConnection, "open_session", count_open)
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=root) as app:
        thread = await app._threads.bot_thread()
        source = await app.current_configuration()
        service = app._root_runs._executor._environments
        captured = await service.admit(
            app._mcp_app_owners.resolver.resolve_run(source, ThreadCompositionSelection.from_thread(thread))
        )
        target = next(item for item in captured.environment_targets if item.target_id == "project-recovery")
        twin = next(item for item in captured.environment_targets if item.target_id == "project-twin")
        alias = target_mount_aliases(target)["workspace-2"]
        twin_alias = target_mount_aliases(twin)["workspace"]
        instance = AgentInstanceContext(
            identity=AgentIdentityRef(issuer="test", subject=BOT_THREAD_ID), agent_instance_id="agent-target-recovery"
        )
        plan = await service.prepare(captured)
        async with plan.runtime.bind(
            thread_id=BOT_THREAD_ID, run_id="run-initial", instance=instance, host_refs={}
        ) as bound:
            await plan.runtime._activate()
            for selected in (target, twin):
                await service.mount_target(
                    captured, selected, plan.runtime, existing_aliases={item.name for item in bound.snapshot.mounts}
                )
            for selected_alias in (alias, twin_alias):
                assert (
                    await bound.files.read_text(f"/environment/{selected_alias}/evidence.txt")
                ).text == "original Bot target"
        original_session = "native"
        if profile == "environment-envd":
            assert len(set(opened)) == 2  # Same root and launcher, separate Project Session selections.
            key = next(iter(target.session_versions))
            head = await app._store.envd_sessions.get(BOT_THREAD_ID, key)
            original_session = head.session.descriptor.session_id
        unrelated.rmdir()
        runtime = await service.prepare_recovery(captured, alias, original_session_id=original_session)
        async with runtime.bind(
            thread_id=BOT_THREAD_ID, run_id="run-recover", instance=instance, host_refs={}
        ) as bound:
            await runtime._activate()
            assert [mount.name for mount in bound.snapshot.mounts] == [alias]
            assert (await bound.files.read_text(f"/environment/{alias}/evidence.txt")).text == "original Bot target"
        if profile == "environment-envd":
            assert len(set(opened)) == 2
            invalid = await service.prepare_recovery(captured, alias, original_session_id="not-the-original-session")
            async with invalid.bind(
                thread_id=BOT_THREAD_ID, run_id="run-invalid-recovery", instance=instance, host_refs={}
            ) as bound:
                await invalid._activate()
                with pytest.raises(EnvironmentError):
                    await bound.files.read_text(f"/environment/{alias}/evidence.txt")
            assert len(set(opened)) == 2  # Recovery never creates a replacement Session.
