"""The UI Host contains the whole Device, including its file and process APIs."""

import os
import sys
from pathlib import Path

import pytest
from a13n_harness import RunBindings
from a13n_harness.environment import EnvironmentMount
from a13n_harness.environment.advanced import create_environment_runtime
from a13n_harness.providers.environment.local_envd.configuration import LocalEnvdEnvironmentConfiguration
from a13n_harness.providers.environment.local_envd.provider import LocalEnvdEnvironment
from a13n_harness.providers.environment.models import EnvironmentError
from a13n_harness_ui.environment_bindings import EnvironmentSelectionPatch
from a13n_harness_ui.errors import EnvironmentLifecycleError
from a13n_harness_ui.sandbox import SandboxLaunch, create_sandbox_runtime, validate_sandbox_runtime

pytestmark = pytest.mark.anyio


@pytest.fixture
def binary():
    if sys.platform != "linux":
        pytest.skip("Linux outer boundary integration")
    configured = os.environ.get("A13N_ENVD_TEST_BINARY")
    if configured is None:
        pytest.skip("Set A13N_ENVD_TEST_BINARY for the real Sandbox tests")
    return Path(configured).resolve()


def test_unsupported_platform_fails_before_resolving_posix_paths(tmp_path, monkeypatch):
    monkeypatch.setattr("a13n_harness_ui.sandbox.sys.platform", "win32")
    with pytest.raises(EnvironmentLifecycleError, match="not available") as failure:
        create_sandbox_runtime(tmp_path / "envd", roots=(tmp_path / "missing",))
    assert failure.value.code == "sandbox_unavailable"


@pytest.mark.skipif(sys.platform not in {"linux", "darwin"}, reason="POSIX outer sandbox")
def test_protected_host_state_is_not_granted_by_a_broad_project_root(tmp_path):
    state = tmp_path / "state"
    thread_files = state / "threads" / "thread-test"
    thread_files.mkdir(parents=True)
    with pytest.raises(EnvironmentLifecycleError, match="overlaps"):
        create_sandbox_runtime(tmp_path / "envd", roots=(tmp_path,), protected_roots=(state,))
    runtime = create_sandbox_runtime(
        tmp_path / "envd", roots=(thread_files,), protected_roots=(state,), thread_files_root=thread_files
    )
    assert isinstance(runtime.launch_factory, SandboxLaunch)


async def test_production_sandbox_preflight_probes_real_boundaries(binary, tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    await validate_sandbox_runtime(binary, root)
    assert list(root.iterdir()) == []


async def test_shared_sandbox_sessions_preserve_host_paths_and_hide_unrelated_files(binary, tmp_path, monkeypatch):
    project = tmp_path / "project with 'quotes'"
    scratch = tmp_path / "thread-files"
    forbidden = tmp_path / "configuration"
    for root in (project, scratch, forbidden):
        root.mkdir()
    secret = forbidden / "secret"
    secret.write_text("not granted")
    (project / "escape").symlink_to(secret)
    monkeypatch.setenv("PRIVATE_HOST_TOKEN", "not inherited")
    owner = create_sandbox_runtime(binary, roots=(project, scratch), protected_roots=(forbidden,))
    first, second = [
        LocalEnvdEnvironment(
            LocalEnvdEnvironmentConfiguration(working_directory=root.as_posix()), owner, environment_id=name
        )
        for root, name in ((project, "first"), (scratch, "second"))
    ]
    async with owner:
        try:
            await first.prepare()
            await second.prepare()
            device = await owner.acquire_device()
            assert len(device._sessions) == 2
            assert first.descriptor.generation != second.descriptor.generation
            assert first.operations.files is not None and second.operations.files is not None
            # Sibling grants, unlike unrelated paths, remain accessible regardless of cwd.
            await first.operations.files.write_text((scratch / "attachment").as_posix(), "shared", mode="create")
            for path in (secret, project / "escape"):
                with pytest.raises(EnvironmentError):
                    await first.operations.files.read_text(path.as_posix())
            runtime = create_environment_runtime(
                mounts={
                    "workspace": EnvironmentMount(
                        first, mount_path=project.as_posix(), provider_root=project.as_posix()
                    )
                },
                default_mount="workspace",
            )
            async with runtime.bind(
                thread_id="thread-test", run_id="run-test", instance=RunBindings.embedded().instance, host_refs={}
            ) as bound:
                await runtime._activate()
                assert bound.resolve_path("result").path == (project / "result").as_posix()
                assert bound.resolve_path((project / "result").as_posix()).path == (project / "result").as_posix()
                await bound.files.write_text((project / "result").as_posix(), "kept", mode="create")
                assert (await bound.files.stat((project / "result").as_posix())).path == (project / "result").as_posix()
                selected = bound.select_files((project / "result").as_posix())
                async with bound.open_files(selected) as files:
                    assert (await files.read_text((project / "result").as_posix())).text == "kept"
            assert len(device._sessions) == 1
            assert (await second.operations.files.read_text((project / "result").as_posix())).text == "kept"
            assert (await owner.describe()).device_id == device.descriptor.device_id
        finally:
            await first.close()
            await second.close()
        assert not device._sessions
    assert (project / "result").read_text() == "kept"
    assert secret.read_text() == "not granted"


async def test_root_runs_use_captured_sandbox_with_fresh_sessions(binary, tmp_path, monkeypatch):
    """Exercise admission through actual model-issued tools, not a replaced Run plan."""
    import asyncio
    import json
    import shlex

    from a13n_envd_client import EIPDeviceConnection
    from a13n_harness_ui.app import open_harness_ui_app
    from a13n_harness_ui.model_runtime import HarnessUiModelResolver
    from a13n_harness_ui.settings import EnvdRuntimeSettings
    from a13n_harness_ui.surfaces import RootOperationStatus
    from pydantic_ai.messages import ToolReturnPart
    from pydantic_ai.models.function import DeltaToolCall, FunctionModel

    from .test_app import _settings, _write_configuration

    configuration = tmp_path / "configuration"
    configuration.mkdir()
    config = _write_configuration(configuration)
    agent = configuration / "agents/assistant.yaml"
    agent.write_text(agent.read_text() + "capabilities:\n  - capability: dynamic_environment\n")
    project = tmp_path / "project"
    project.mkdir()
    project_resource = configuration / "projects/main.yaml"
    project_resource.write_text(project_resource.read_text().replace(str(configuration / "workspace"), str(project)))
    secret = configuration / "host-secret"
    secret.write_text("Host-only fixture")
    settings = _settings(tmp_path / "state").model_copy(update={"envd_runtime": EnvdRuntimeSettings(executable=binary)})
    opened = []
    original = EIPDeviceConnection.open_session

    async def open_session(device, **kwargs):
        session = await original(device, **kwargs)
        opened.append((device.descriptor.device_id, session.session_id))
        return session

    monkeypatch.setattr(EIPDeviceConnection, "open_session", open_session)
    async with open_harness_ui_app(settings, configuration_path=config) as app:
        thread = await app.create_thread()
        scratch = await app._thread_files.touch(thread.thread_id)
        calls = (
            ("write", {"file_path": "result.txt", "content": "Sandbox model result"}),
            ("view", {"file_path": str(project / "result.txt")}),
            (
                "shell_exec",
                {
                    "command": f"test ! -r {shlex.quote(str(secret))} && printf sandbox-shell-ok",
                    "cwd": str(project),
                    "yield_time_seconds": 1,
                },
            ),
            ("write", {"file_path": str(scratch / "tmp/result.txt"), "content": "Sandbox thread files"}),
            ("view", {"file_path": str(scratch / "tmp/result.txt")}),
        )
        expected = ("result.txt", "Sandbox model result", "sandbox-shell-ok", "result.txt", "Sandbox thread files")
        model_calls = 0

        async def model(messages, info):
            nonlocal model_calls
            step = model_calls % (len(calls) + 1)
            model_calls += 1
            assert {"write", "view", "shell_exec"} <= {tool.name for tool in info.function_tools}
            if step:
                returns = [
                    part
                    for message in messages
                    if message.kind == "request"
                    for part in message.parts
                    if isinstance(part, ToolReturnPart)
                ]
                assert returns, messages[-1]
                assert expected[step - 1] in str(returns[-1].content), returns[-1]
            if step < len(calls):
                name, arguments = calls[step]
                yield {0: DeltaToolCall(name=name, json_args=json.dumps(arguments), tool_call_id=f"call-{model_calls}")}
            else:
                yield "Verified captured Sandbox file and shell tools."

        async def resolve(self, context, model_id):
            return FunctionModel(stream_function=model)

        monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
        for index in range(2):
            receipt = await app.submit_thread(
                thread_id=thread.thread_id,
                prompt="Exercise Sandbox",
                environment=EnvironmentSelectionPatch(environment_profile_id="environment-sandbox"),
            )
            async with asyncio.timeout(30):
                result = await app.wait_root_operation(receipt.receipt_id)
            assert result.status is RootOperationStatus.completed, result
            capture = await app.inspect_operation_configuration(receipt.receipt_id)
            assert capture is not None and capture.environment_profile_id == "environment-sandbox"
            assert len(opened) == 2 * (index + 1)
            assert len({device for device, _ in opened}) == 1
            assert len({session for _, session in opened}) == len(opened)
        assert (project / "result.txt").read_text() == "Sandbox model result"
        assert (scratch / "tmp/result.txt").read_text() == "Sandbox thread files"
        assert (
            await app.get_thread(thread.thread_id)
        ).thread.configuration.environment_profile_id == "environment-native"
    assert secret.read_text() == "Host-only fixture"
