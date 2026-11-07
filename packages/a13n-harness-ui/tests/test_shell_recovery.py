"""Real external Envd supervision across App lifetimes and competing Apps."""

import asyncio
import json
import os
import shlex
import socket
import sys

import pytest
from a13n_envd_client import EIPDeviceConnection
from a13n_harness.environment.coordinator import ManagedEnvironmentRuntime
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.environment_bindings import EnvironmentBindingSelection, EnvironmentSelectionPatch
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from a13n_harness_ui.surfaces import RootOperationStatus
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from .test_app import _settings, _write_configuration
from .test_device_runtime import TOKEN, daemon, device_path, requires_envd, resource

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("migrated_databases")]


@requires_envd
async def test_owned_daemon_shutdown_loses_old_job_and_reuses_replacement(tmp_path, monkeypatch):
    from a13n_harness_ui.surfaces import NewThreadDefaults

    path = _write_configuration(tmp_path)
    path.write_text(
        path.read_text()
        + f"envd:\n  executable: {os.environ['A13N_ENVD_TEST_BINARY']}\n"
        + "tools:\n  execution: {mode: auto, foreground_wait_seconds: 0}\n"
    )
    agent = tmp_path / "agents/assistant.yaml"
    agent.write_text(agent.read_text() + "capabilities:\n  - capability: dynamic_environment\n")
    workspace = tmp_path / "workspace"
    script = "import os,pathlib,time; pathlib.Path('admitted').write_text(str(os.getpid())); time.sleep(60)"
    commands = [f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}"]
    calls = 0

    async def model(messages, info):
        nonlocal calls
        calls += 1
        if commands:
            command = commands.pop(0)
            yield {
                0: DeltaToolCall(
                    name="shell_exec", json_args=json.dumps({"command": command}), tool_call_id=f"shell-{calls}"
                )
            }
        else:
            yield "Observed."

    async def resolve(self, context, model_id):
        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    opened = []
    original_open = EIPDeviceConnection.open_session

    async def open_session(self, *args, **kwargs):
        result = await original_open(self, *args, **kwargs)
        opened.append(result.session_id)
        return result

    monkeypatch.setattr(EIPDeviceConnection, "open_session", open_session)
    settings = _settings(tmp_path / "state")
    async with open_harness_ui_app(settings, configuration_path=path) as owner:
        thread = await owner.create_thread(defaults=NewThreadDefaults(environment_profile_id="environment-envd"))
        receipt = await owner.submit_thread(thread_id=thread.thread_id, prompt="Start job")
        assert (await owner.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.completed
        async with asyncio.timeout(10):
            while not (workspace / "admitted").exists():
                await asyncio.sleep(0.02)
            while True:
                records = await owner._store.tool_executions.active()
                assert len(records) == 1
                original = await owner._store.tool_executions.get(thread.thread_id, records[0].execution_id)
                if original.shell is not None and original.shell.process.identity is not None:
                    break
                await asyncio.sleep(0.02)
        pid = int((workspace / "admitted").read_text())
        assert len(opened) == 1
    if os.name != "nt":
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    calls_before_restart = calls

    async def defer_delivery(*args):
        return False

    async with open_harness_ui_app(settings, configuration_path=path) as successor:
        successor._tool_executions.deliver = defer_delivery
        async with asyncio.timeout(15):
            while True:
                lost = await successor._store.tool_executions.get(thread.thread_id, original.record.execution_id)
                if lost.record.terminal:
                    break
                await asyncio.sleep(0.02)
        assert lost.record.status == "lost", lost.record
        assert lost.shell.process == original.shell.process
        assert calls == calls_before_restart
        # Exact recovery never creates a replacement for a lost original Session.
        assert len(opened) == 1

        for _ in range(2):
            commands.append("printf replacement-ready")
            receipt = await successor.submit_thread(thread_id=thread.thread_id, prompt="Use replacement")
            assert (await successor.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.completed
            await successor._tool_executions.runtime.join()
        assert len(opened) == 2
        assert (workspace / "admitted").read_text() == str(pid)
        completed = [
            record for record in await successor.tool_executions(thread.thread_id) if record.status == "succeeded"
        ]
        assert len(completed) == 2
        for record in completed:
            result = await successor._store.tool_executions.get(thread.thread_id, record.execution_id)
            assert "replacement-ready" in str(result.result)
    async with open_harness_ui_app(settings, configuration_path=path) as reader:
        # Results belong to Host storage, not the now-destroyed replacement daemon.
        for record in completed:
            result = await reader._store.tool_executions.get(thread.thread_id, record.execution_id)
            assert "replacement-ready" in str(result.result)


@requires_envd
@pytest.mark.parametrize(
    "cancel_from_other_app,foreground_wait,pause_activation,transient_attach",
    [
        (False, 0, False, False),
        (True, 0, False, False),
        (False, 60, False, False),
        (True, 0, True, False),
        (False, 0, False, True),
    ],
)
async def test_external_shell_survives_app_restart_without_new_run_or_command(
    tmp_path, monkeypatch, cancel_from_other_app, foreground_wait, pause_activation, transient_attach
):
    monkeypatch.setenv("DEVICE_TOKEN", TOKEN)

    path = _write_configuration(tmp_path)
    path.write_text(
        path.read_text() + f"tools:\n  execution: {{mode: auto, foreground_wait_seconds: {foreground_wait}}}\n"
    )
    agent = tmp_path / "agents/assistant.yaml"
    agent.write_text(agent.read_text() + "capabilities:\n  - capability: dynamic_environment\n")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        endpoint = f"http://127.0.0.1:{sock.getsockname()[1]}"
    recipe = resource("http", endpoint)
    (tmp_path / "devices").mkdir()
    (tmp_path / "devices/build.yaml").write_text(recipe.model_dump_json())
    opened, attached = [], []
    original_open, original_attach = EIPDeviceConnection.open_session, EIPDeviceConnection.attach_session

    async def open_session(self, *args, **kwargs):
        value = await original_open(self, *args, **kwargs)
        opened.append(value.session_id)
        return value

    async def attach_session(self, *args, **kwargs):
        value = await original_attach(self, *args, **kwargs)
        attached.append(value.session_id)
        return value

    monkeypatch.setattr(EIPDeviceConnection, "open_session", open_session)
    monkeypatch.setattr(EIPDeviceConnection, "attach_session", attach_session)
    resumed = asyncio.Event()
    stage = 0

    async with daemon(tmp_path, "http", endpoint=endpoint, full_control=True) as root:
        script = (
            "import pathlib,time,os; p=pathlib.Path('.'); "
            "f=open('admissions','a'); f.write(str(os.getpid())+'\\n'); f.close(); "
            "print('before-handoff',flush=True); "
            "exec(\"while not (p/'finish').exists(): time.sleep(0.02)\"); "
            "print('after-handoff',flush=True)"
        )
        command = f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}"

        async def model(messages, info):
            nonlocal stage
            stage += 1
            if stage == 1:
                assert "shell_exec" in {tool.name for tool in info.function_tools}
                yield {
                    index: DeltaToolCall(
                        name="shell_exec",
                        json_args=json.dumps({"command": command, "alias": "build"}),
                        tool_call_id=f"shell-{index}",
                    )
                    for index in range(2)
                }
            else:
                if "Tool executions completed" in str(messages):
                    resumed.set()
                yield "Jobs admitted." if stage == 2 else "Completion received."

        async def resolve(self, context, model_id):
            return FunctionModel(stream_function=model)

        monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
        settings = _settings(tmp_path / "data")
        async with open_harness_ui_app(settings, configuration_path=path) as owner:
            async with asyncio.timeout(10):
                while not (await owner.device_info(recipe.id)).available:
                    await asyncio.sleep(0.02)
            thread = await owner.create_thread()
            receipt = await owner.submit_thread(
                thread_id=thread.thread_id,
                prompt="Start two jobs.",
                environment=EnvironmentSelectionPatch(
                    default_environment="build",
                    environment_bindings=(
                        EnvironmentBindingSelection(
                            device_id=recipe.id, alias="build", working_directory=device_path(root)
                        ),
                    ),
                ),
            )
            if not foreground_wait:
                assert (await owner.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.completed
            async with asyncio.timeout(10):
                while not (root / "admissions").exists() or len((root / "admissions").read_text().splitlines()) != 2:
                    observations = await owner._store.tool_executions.list(thread.thread_id, offset=0, limit=10)
                    for item in observations:
                        if item.terminal:
                            pytest.fail(
                                repr(await owner._store.tool_executions.get(thread.thread_id, item.execution_id))
                            )
                    await asyncio.sleep(0.02)
                while True:
                    records = await owner._store.tool_executions.active()
                    saved = [
                        await owner._store.tool_executions.get(thread.thread_id, item.execution_id) for item in records
                    ]
                    if len(saved) == 2 and all(
                        item.shell is not None and item.shell.process.identity for item in saved
                    ):
                        break
                    await asyncio.sleep(0.02)
            identities = {item.record.execution_id: item.shell.process for item in saved}
            assert len(set(opened)) == 1
            async with open_harness_ui_app(settings, configuration_path=path) as observer:
                await observer._tool_executions.recover()
                assert observer._tool_executions.runtime.active == ()
                assert attached == []
                assert all(not item.terminal for item in await observer._store.tool_executions.active())
        # App shutdown relinquishes supervision. The external daemon and both jobs survive.
        expected_stage = 1 if foreground_wait else 2
        assert stage == expected_stage
        activation_entered, activate = asyncio.Event(), asyncio.Event()
        original_activate = ManagedEnvironmentRuntime._activate
        if pause_activation:

            async def blocked_activate(self):
                activation_entered.set()
                async with asyncio.timeout(10):
                    await activate.wait()
                return await original_activate(self)

            monkeypatch.setattr(ManagedEnvironmentRuntime, "_activate", blocked_activate)
        unavailable_attempt = asyncio.Event()
        if transient_attach:
            from a13n_envd_client import EIPTransportError

            async def unavailable(*args, **kwargs):
                unavailable_attempt.set()
                raise EIPTransportError("Temporary carrier outage")

            monkeypatch.setattr(EIPDeviceConnection, "attach_session", unavailable)
        async with open_harness_ui_app(settings, configuration_path=path) as successor:
            if transient_attach:
                async with asyncio.timeout(10):
                    await unavailable_attempt.wait()
                    await successor._tool_executions.runtime.join()
                for execution_id in identities:
                    pending = await successor._store.tool_executions.get(thread.thread_id, execution_id)
                    assert not pending.record.terminal
                    assert pending.shell.process == identities[execution_id]
                monkeypatch.setattr(EIPDeviceConnection, "attach_session", attach_session)
            async with asyncio.timeout(10):
                while len(successor._tool_executions.runtime.active) != 2:
                    await asyncio.sleep(0.02)
            assert set(item.execution_id for item in successor._tool_executions.runtime.active) == set(identities)
            assert stage == expected_stage  # Recovery does not admit a model Run.
            assert all(item.foreground_released for item in successor._tool_executions.runtime.active)
            if cancel_from_other_app:
                async with open_harness_ui_app(settings, configuration_path=path) as observer:
                    for execution_id in identities:
                        await observer.cancel_tool_execution(thread.thread_id, execution_id)
                if pause_activation:
                    try:
                        async with asyncio.timeout(10):
                            await activation_entered.wait()
                        await successor._tool_executions.recover()
                        assert all(
                            item.cancel_requested and not item.terminal
                            for item in successor._tool_executions.runtime.active
                        )
                    finally:
                        activate.set()
            else:
                (root / "finish").touch()
            async with asyncio.timeout(15):
                await successor._tool_executions.runtime.join()
                if not foreground_wait:
                    await resumed.wait()
            if foreground_wait:
                # Shutdown cancelled the foreground Root, not its external jobs.
                latest = await successor._store.threads.get(thread.thread_id)
                assert latest.last_execution.status == "cancelled"
                assert not await successor._deliver_tool_completion(
                    thread.thread_id, "Tool executions completed", tuple(identities)
                )
                assert stage == expected_stage and not resumed.is_set()
            for execution_id, original in identities.items():
                saved = await successor._store.tool_executions.get(thread.thread_id, execution_id)
                assert saved.record.status == ("cancelled" if cancel_from_other_app else "succeeded"), saved.record
                assert saved.shell.process.identity == original.identity
                assert saved.shell.process.session_id == original.session_id
                if cancel_from_other_app:
                    assert saved.record.cancel_requested
                else:
                    assert "before-handoff" in str(saved.result)
                    assert "after-handoff" in str(saved.result)
            admissions = (root / "admissions").read_text().splitlines()
            assert len(admissions) == 2
            if cancel_from_other_app and os.name != "nt":
                for pid in admissions:
                    with pytest.raises(ProcessLookupError):
                        os.kill(int(pid), 0)
            assert len(set(opened)) == 1
            assert set(attached) == set(opened)


@requires_envd
async def test_binding_owner_excludes_other_apps_but_shares_local_borrowers(tmp_path, monkeypatch):
    from a13n_harness.environment.models import EnvironmentError
    from a13n_harness_ui.composition.models import ResolvedEnvironmentBinding

    monkeypatch.setenv("DEVICE_TOKEN", TOKEN)
    path = _write_configuration(tmp_path)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        endpoint = f"http://127.0.0.1:{sock.getsockname()[1]}"
    recipe = resource("http", endpoint)
    (tmp_path / "devices").mkdir()
    (tmp_path / "devices/build.yaml").write_text(recipe.model_dump_json())
    opened, attached = [], []
    original_open, original_attach = EIPDeviceConnection.open_session, EIPDeviceConnection.attach_session

    async def open_session(self, *args, **kwargs):
        result = await original_open(self, *args, **kwargs)
        opened.append(result.session_id)
        return result

    async def attach_session(self, *args, **kwargs):
        result = await original_attach(self, *args, **kwargs)
        attached.append(result.session_id)
        return result

    monkeypatch.setattr(EIPDeviceConnection, "open_session", open_session)
    monkeypatch.setattr(EIPDeviceConnection, "attach_session", attach_session)
    settings = _settings(tmp_path / "data")
    async with daemon(tmp_path, "http", endpoint=endpoint) as root:
        async with open_harness_ui_app(settings, configuration_path=path) as owner:
            thread = await owner.create_thread()
            binding = ResolvedEnvironmentBinding(
                selection=EnvironmentBindingSelection(
                    device_id=recipe.id, alias="build", working_directory=device_path(root)
                ),
                device=recipe,
            )
            service = owner._root_runs._executor._environments
            async with asyncio.timeout(10):
                while not (await owner.device_info(recipe.id)).available:
                    await asyncio.sleep(0.02)
            from a13n_harness_ui.root_execution import _selection

            published = await owner._root_runs._executor._compositions.publish(
                await owner.current_configuration(), _selection(await owner._threads.get(thread.thread_id))
            )
            composition = await service.admit(
                published.value.model_copy(
                    update={
                        "environment_bindings": (binding,),
                        "default_environment": "build",
                    }
                )
            )
            first, _ = await service._prepare_device_mount(composition, binding)
            second, _ = await service._prepare_device_mount(composition, binding)
            async with open_harness_ui_app(settings, configuration_path=path) as observer:
                other = observer._root_runs._executor._environments
                async with asyncio.timeout(10):
                    while not (await observer.device_info(recipe.id)).available:
                        await asyncio.sleep(0.02)
                recovered, _ = await other._prepare_device_mount(composition, binding)
                assert not service._owners._held and not other._owners._held
                assert not await owner._store.tool_executions.active()  # No execution lock participates.
                first_open = await first.environment.open()
                second_open = await second.environment.open()
                try:
                    assert len(opened) == 1
                    assert attached == []
                    with pytest.raises(EnvironmentError) as busy:
                        await recovered.environment.open()
                    assert busy.value.code == "environment_busy"
                    await first_open.close()
                    with pytest.raises(EnvironmentError) as busy:
                        await recovered.environment.open()
                    assert busy.value.code == "environment_busy"
                    assert second_open.operations.files is not None
                    assert (await second_open.operations.files.list(device_path(root), max_results=10)).entries
                    # Renaming a directory view preserves Session and ownership identity.
                    changed = binding.model_copy(
                        update={"selection": binding.selection.model_copy(update={"alias": "other"})}
                    )
                    renamed, _ = await other._prepare_device_mount(composition, changed)
                    with pytest.raises(EnvironmentError) as busy:
                        await renamed.environment.open()
                    assert busy.value.code == "environment_busy"
                    local_renamed, _ = await service._prepare_device_mount(composition, changed)
                    renamed_open = await local_renamed.environment.open()
                    await renamed_open.close()
                    assert len(set(opened)) == 1
                finally:
                    await first_open.close()
                    await second_open.close()
                assert not service._owners._held
                recovered_open = await recovered.environment.open()
                try:
                    assert attached == [opened[0]]
                    assert len(opened) == 1
                finally:
                    await recovered_open.close()
                # The original connector reloads B's newer epoch before taking over again.
                reopened = await first.environment.open()
                await reopened.close()
                assert attached == [opened[0], opened[0]]
                assert not service._owners._held and not other._owners._held


@requires_envd
@pytest.mark.parametrize("lose_response", [False, True])
async def test_external_shell_survives_hard_app_death_and_lost_start_response(tmp_path, monkeypatch, lose_response):
    from pathlib import Path

    monkeypatch.setenv("DEVICE_TOKEN", TOKEN)
    path = _write_configuration(tmp_path)
    path.write_text(path.read_text() + "tools:\n  execution: {mode: auto, foreground_wait_seconds: 0}\n")
    agent = tmp_path / "agents/assistant.yaml"
    agent.write_text(agent.read_text() + "capabilities:\n  - capability: dynamic_environment\n")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        endpoint = f"http://127.0.0.1:{sock.getsockname()[1]}"
    recipe = resource("http", endpoint)
    (tmp_path / "devices").mkdir()
    (tmp_path / "devices/build.yaml").write_text(recipe.model_dump_json())
    ready, log = tmp_path / "ready.json", tmp_path / "worker.log"
    settings = _settings(tmp_path / "data")
    resumed = asyncio.Event()

    async def model(messages, info):
        assert "Continue explicitly" in str(messages)
        resumed.set()
        yield "Completion received."

    async def resolve(self, context, model_id):
        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    async with daemon(tmp_path, "http", endpoint=endpoint, full_control=True) as root:
        script = (
            "import pathlib,time,os; p=pathlib.Path('.'); "
            "f=open('admissions','a'); f.write(str(os.getpid())+'\\n'); f.close(); "
            "print('before-crash',flush=True); "
            "exec(\"while not (p/'finish').exists(): time.sleep(0.02)\"); "
            "print('after-crash',flush=True)"
        )
        command = f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}"
        with log.open("wb") as output:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                str(Path(__file__).with_name("shell_recovery_worker.py")),
                str(path),
                str(settings.storage.data_root),
                str(ready),
                recipe.id,
                device_path(root),
                command,
                str(lose_response).lower(),
                stdout=output,
                stderr=output,
            )
            try:
                async with asyncio.timeout(25):
                    while not ready.exists() or not (root / "admissions").exists():
                        assert process.returncode is None, log.read_text()
                        await asyncio.sleep(0.02)
                original = json.loads(ready.read_text())
                process.kill()
                async with asyncio.timeout(5):
                    await process.wait()
                # Completion occurs while no Host owns the native process.
                (root / "finish").touch()
                async with open_harness_ui_app(settings, configuration_path=path) as successor:
                    async with asyncio.timeout(20):
                        while True:
                            saved = await successor._store.tool_executions.get(
                                original["thread_id"], original["execution_id"]
                            )
                            if saved.record.terminal:
                                break
                            await asyncio.sleep(0.02)
                        await successor._tool_executions.runtime.join()
                    assert not await successor._deliver_tool_completion(
                        original["thread_id"], "Tool executions completed", (original["execution_id"],)
                    )
                    assert not resumed.is_set()
                    assert not saved.record.delivery_acknowledged
                    receipt = await successor.submit_thread(
                        thread_id=original["thread_id"], prompt="Continue explicitly"
                    )
                    assert (
                        await successor.wait_root_operation(receipt.receipt_id)
                    ).status is RootOperationStatus.completed
                    assert resumed.is_set()
                    assert saved.record.status == "succeeded", saved.record
                    assert saved.shell.process.identity.model_dump(mode="json") == original["process"]["identity"]
                    assert saved.shell.process.session_id == original["process"]["session_id"]
                    assert "before-crash" in str(saved.result)
                    assert "after-crash" in str(saved.result)
                    assert len((root / "admissions").read_text().splitlines()) == 1
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
