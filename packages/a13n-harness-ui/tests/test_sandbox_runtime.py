"""UI selects grants; Envd enforces them across file and process APIs."""

import os
import sys
from pathlib import Path

import pytest
from a13n_environment.local_envd.configuration import LocalEnvdEnvironmentConfiguration
from a13n_environment.local_envd.provider import LOCAL_ENVD
from a13n_environment.models import EnvironmentError
from a13n_harness import RunBindings
from a13n_harness.environment import EnvironmentMount, EnvironmentPermissionSet
from a13n_harness.environment.advanced import create_environment_runtime
from a13n_harness_ui.environment_bindings import EnvironmentSelectionPatch
from a13n_harness_ui.environment_runtime import _PreparedMount
from a13n_harness_ui.errors import EnvironmentLifecycleError
from a13n_harness_ui.sandbox import create_sandbox_runtime, validate_sandbox_runtime

pytestmark = pytest.mark.anyio


@pytest.fixture
def binary():
    if sys.platform not in {"linux", "darwin"}:
        pytest.skip("Native Sandbox integration requires Linux or macOS")
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
    assert runtime.configuration.sandbox.mode == "restricted"
    assert runtime.configuration.egress.mode == "deny"
    assert [(grant.path, grant.access) for grant in runtime.configuration.sandbox.grants] == [
        ((thread_files / "attachments").as_posix(), "read_only"),
        ((thread_files / "tmp").as_posix(), "read_write"),
    ]


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
    first_connector, second_connector = [
        LOCAL_ENVD.execution_connector(
            LocalEnvdEnvironmentConfiguration(working_directory=root.as_posix()), runtime=owner, environment_id=name
        )
        for root, name in ((project, "first"), (scratch, "second"))
    ]
    async with owner:
        try:
            first = await first_connector.open()
            second = await second_connector.open()
            device = await owner.acquire_device()
            assert len(device._sessions) == 2
            assert first.descriptor.generation != second.descriptor.generation
            assert first.operations.files is not None and second.operations.files is not None
            # Sibling grants, unlike unrelated paths, remain accessible regardless of cwd.
            await first.operations.files.write_text((scratch / "attachment").as_posix(), "shared", mode="create")
            for path in (secret, project / "escape"):
                with pytest.raises(EnvironmentError):
                    await first.operations.files.read_text(path.as_posix())
            await first.close()
            runtime = create_environment_runtime(
                mounts={
                    "workspace": EnvironmentMount(
                        _PreparedMount("workspace", first_connector, EnvironmentPermissionSet(), None),
                        mount_path=project.as_posix(),
                        provider_root=project.as_posix(),
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


async def test_runtime_cache_separates_launch_policy_but_not_session_destinations(tmp_path, monkeypatch):
    from a13n_harness_ui.composition.models import ResolvedEnvironmentProfile
    from a13n_harness_ui.environment_runtime import EnvironmentSnapshotReconstructor

    root = tmp_path / "project"
    root.mkdir()
    reconstructor = EnvironmentSnapshotReconstructor()

    async def executable():
        return tmp_path / "envd"

    monkeypatch.setattr(reconstructor, "resolve_sandbox_executable", executable)

    def profile(mode, hosts=()):
        return ResolvedEnvironmentProfile(
            profile_id="environment-test",
            behavior_digest="a" * 64,
            provider_key="local_envd",
            adapter_key="local_envd_project",
            provider_configuration={
                "launch": {"sandbox": {"mode": "restricted", "grants": []}, "egress": {"mode": mode}},
                "session": {"egress": {"destinations": {"mode": "allowlist", "hosts": list(hosts)}}}
                if mode == "controlled"
                else {},
            },
        )

    try:
        denied = await reconstructor.sandbox_runtime((root,), profile=profile("deny"))
        inherited = await reconstructor.sandbox_runtime((root,), profile=profile("inherit"))
        controlled = await reconstructor.sandbox_runtime((root,), profile=profile("controlled", ("a.example.com",)))
        assert denied is not inherited and controlled not in (denied, inherited)
        assert controlled is await reconstructor.sandbox_runtime(
            (root,), profile=profile("controlled", ("b.example.com",))
        )
        assert denied is await reconstructor.sandbox_runtime((root,), profile=profile("deny"))
    finally:
        await reconstructor.close()


async def test_thread_attachment_grant_is_read_only(binary, tmp_path):
    thread = tmp_path / "thread"
    (thread / "attachments").mkdir(parents=True)
    (thread / "tmp").mkdir()
    attachment = thread / "attachments/input"
    attachment.write_text("submitted input")
    owner = create_sandbox_runtime(binary, roots=(thread,), thread_files_root=thread)
    environment = LOCAL_ENVD.execution_connector(
        LocalEnvdEnvironmentConfiguration(working_directory=thread.as_posix()),
        runtime=owner,
        environment_id="thread-files",
    )
    async with owner:
        try:
            environment = await environment.open()
            files = environment.operations.files
            assert files is not None
            assert (await files.read_text(attachment.as_posix())).text == "submitted input"
            with pytest.raises(EnvironmentError):
                await files.write_text(attachment.as_posix(), "overwritten", mode="replace")
            await files.write_text((thread / "tmp/result").as_posix(), "output", mode="create")
            assert environment.descriptor.execution_boundary["egress"] == "deny"
        finally:
            await environment.close()
    assert attachment.read_text() == "submitted input"
    assert (thread / "tmp/result").read_text() == "output"
