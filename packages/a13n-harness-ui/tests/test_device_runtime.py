"""Real daemon carriers through the App and authenticated browser boundary."""

from __future__ import annotations

import asyncio
import json
import os
import socket
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
import uvicorn
from a13n_envd_client import EIPDeviceConnection
from a13n_harness import RunBindings
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.composition.models import ResolvedEnvironmentBinding
from a13n_harness_ui.configuration.models import DeviceResource
from a13n_harness_ui.configuration.mutation import ResourceMutationRequest
from a13n_harness_ui.environment_bindings import EnvironmentBindingSelection, EnvironmentSelectionPatch
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from a13n_harness_ui.root_execution import _selection
from a13n_harness_ui.surfaces import NewThreadDefaults, RootOperationStatus
from a13n_harness_ui.webui import create_webui
from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

from .test_app import _settings, _write_configuration

pytestmark = pytest.mark.anyio
TOKEN = "fixture-device-token"
HEADERS = {"Authorization": "Bearer test-only-key"}
# Decide before any App or listener starts, so the default suite pays nothing for these tests.
requires_envd = pytest.mark.skipif(
    "A13N_ENVD_TEST_BINARY" not in os.environ, reason="Set A13N_ENVD_TEST_BINARY for real Device integration tests"
)


def device_path(path: Path) -> str:
    value = path.resolve().as_posix()
    return f"/{value}" if os.name == "nt" else value


def resource(carrier: str, endpoint: str) -> DeviceResource:
    return DeviceResource.model_validate(
        {
            "schema_version": "1",
            "kind": "device",
            "id": "device-build",
            "name": "Build",
            "device_id": "native-build",
            "transport": {
                "kind": carrier,
                "configuration": {"endpoint": endpoint} if carrier == "http" else {"connection_timeout": 1},
            },
            "authentication": {"kind": "api_key", "env": "DEVICE_TOKEN"},
        }
    )


@asynccontextmanager
async def app_listener(tmp_path, recipe=None):
    root = _write_configuration(tmp_path)
    if recipe is not None:
        (tmp_path / "devices").mkdir()
        (tmp_path / "devices/build.yaml").write_text(recipe.model_dump_json())
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:

        @asynccontextmanager
        async def opened():
            yield app

        server = create_webui(opened, api_key="test-only-key")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
            native = uvicorn.Server(uvicorn.Config(server, log_level="error", ws="websockets-sansio"))
            task = asyncio.create_task(native.serve(sockets=[sock]))
            try:
                async with asyncio.timeout(10):
                    while not native.started:
                        if task.done():
                            await task
                            pytest.fail("Listener stopped before startup")
                        await asyncio.sleep(0.01)
                yield app, f"http://127.0.0.1:{port}", f"ws://127.0.0.1:{port}"
            finally:
                native.should_exit = True
                async with asyncio.timeout(10):
                    await task


@asynccontextmanager
async def daemon(tmp_path, carrier, *, endpoint):
    executable = Path(os.environ["A13N_ENVD_TEST_BINARY"]).resolve()
    root = tmp_path / "remote"
    root.mkdir()
    (root / "alpha").mkdir()
    (root / "beta").mkdir()
    credential = tmp_path / "credential"
    credential.write_text(TOKEN)
    config = tmp_path / "envd.json"
    config.write_text(
        json.dumps(
            {
                "device_id": "native-build",
                "default_working_directory": str(root),
                "limits": {"max_sessions": 2},
            }
        )
    )
    environment = {"A13N_ENVD_RUNTIME_DIR": str(tmp_path / "runtime"), "LANG": "C.UTF-8"}
    if os.name == "nt":
        environment["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
    if carrier == "http":
        environment.update(
            {
                "A13N_ENVD_TRANSPORT": "http",
                "A13N_ENVD_HTTP_BIND": endpoint.removeprefix("http://"),
                "A13N_ENVD_HTTP_CREDENTIAL_FILE": str(credential),
                "A13N_ENVD_HTTP_PLAINTEXT_SCOPE": "loopback",
            }
        )
    else:
        environment.update(
            {
                "A13N_ENVD_TRANSPORT": "reverse_websocket",
                "A13N_ENVD_REVERSE_WS_URL": endpoint + "/api/devices/device-build/connect",
                "A13N_ENVD_REVERSE_WS_CREDENTIAL_FILE": str(credential),
            }
        )
    process = await asyncio.create_subprocess_exec(
        str(executable),
        "--config",
        str(config),
        env=environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        yield root
    finally:
        if process.returncode is None:
            process.terminate()
        try:
            _, stderr = await asyncio.wait_for(process.communicate(), 5)
        except TimeoutError:
            process.kill()
            await process.communicate()
            raise
        assert process.returncode == 0, stderr.decode(errors="replace")
        assert TOKEN.encode() not in stderr


@requires_envd
@pytest.mark.parametrize("carrier", ["http", "websocket"])
async def test_device_browsing_and_independent_sessions_share_carrier(tmp_path, monkeypatch, carrier):
    monkeypatch.setenv("DEVICE_TOKEN", TOKEN)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        endpoint = f"http://127.0.0.1:{sock.getsockname()[1]}"
    recipe = resource(carrier, endpoint)
    opened = []
    original = EIPDeviceConnection.open_session

    async def open_session(self, *args, **kwargs):
        session = await original(self, *args, **kwargs)
        opened.append(session.session_id)
        return session

    monkeypatch.setattr(EIPDeviceConnection, "open_session", open_session)
    async with app_listener(tmp_path, recipe) as (app, http, ws):
        async with daemon(tmp_path, carrier, endpoint=endpoint if carrier == "http" else ws) as root:
            async with httpx.AsyncClient(base_url=http, headers=HEADERS, trust_env=False) as api:
                async with asyncio.timeout(10):
                    while not (await app.device_info(recipe.id)).available:
                        await asyncio.sleep(0.03)
                assert (await api.get("/api/devices", headers={"Authorization": "Bearer wrong"})).status_code == 401
                assert (await api.get("/api/devices")).json() == [
                    {"id": recipe.id, "name": "Build", "transport": carrier, "registration": "configured"}
                ]
                info = await api.get(f"/api/devices/{recipe.id}")
                assert info.status_code == 200, info.text
                assert set(info.json()) == {
                    "id",
                    "name",
                    "transport",
                    "available",
                    "registration",
                    "path_style",
                    "default_working_directory",
                    "directory_discovery",
                    "error_code",
                }
                assert info.json()["default_working_directory"] == device_path(root)
                page = await api.get(f"/api/devices/{recipe.id}/directories", params={"limit": 1})
                assert page.status_code == 200, page.text
                assert len(page.json()["entries"]) == 1
                assert page.json()["next_offset"] == 1
                for forbidden in (TOKEN, "session_id", "generation", "credential_ref", "endpoint"):
                    assert forbidden not in info.text + page.text
                for path, code in (
                    ("relative", "device_directory_query_invalid"),
                    (device_path(root / "absent"), "device_directory_unavailable"),
                ):
                    failed = await api.get(f"/api/devices/{recipe.id}/directories", params={"path": path})
                    assert failed.status_code >= 400, failed.text
                    assert failed.json()["error"]["code"] == code
                assert (await app.device_info(recipe.id)).available
                assert opened == []
                binding = ResolvedEnvironmentBinding(
                    device=recipe,
                    selection=EnvironmentBindingSelection(
                        device_id=recipe.id, alias="build", working_directory=device_path(root)
                    ),
                )
                first, second = await asyncio.gather(
                    *[app._devices.bind(binding, environment_id=name) for name in ("first", "second")]
                )
                try:
                    first, second = await asyncio.gather(first.open(), second.open())
                    assert len(set(opened)) == 2
                    assert first.descriptor.generation != second.descriptor.generation
                    assert first.operations.files is not None and second.operations.files is not None
                    path = device_path(root / "retained.txt")
                    await first.operations.files.write_text(path, "retained", mode="create")
                    await first.close()
                    assert (await second.operations.files.read_text(path)).text == "retained"
                    assert (await app.device_info(recipe.id)).available
                    await second.close()
                    third = await app._devices.bind(binding, environment_id="third")
                    try:
                        third = await third.open()
                        assert len(set(opened)) == 3
                    finally:
                        await third.close()
                finally:
                    await first.close()
                    await second.close()
                assert (root / "retained.txt").read_text() == "retained"
                assert (await app.list_threads()).total == 0
                thread = await app.create_thread(
                    defaults=NewThreadDefaults(
                        project_id=None, environment_bindings=(binding.selection,), default_environment="build"
                    )
                )
                executor = app._root_runs._executor
                captured = await executor._compositions.publish(
                    await app.current_configuration(), _selection(await app._threads.get(thread.thread_id))
                )
                service = executor._environments
                plan = await service.prepare(captured.value)
                try:
                    assert plan.default_environment == "build"
                    async with plan.runtime.bind(
                        thread_id=thread.thread_id,
                        run_id="run-device",
                        instance=RunBindings.embedded().instance,
                        host_refs={},
                    ) as bound:
                        await plan.runtime._activate()
                        assert bound.resolve_path("retained.txt").path == device_path(root / "retained.txt")
                        assert (await bound.files.read_text("retained.txt")).text == "retained"
                finally:
                    finalized = plan.finalization
                assert finalized.cleanup_errors == ()
                publication = next(item for item in finalized.state_publications if item.key.alias == "build")
                assert publication.status == "published"
                assert publication.key.adapter_key == f"{carrier}_envd"
                assert publication.key.normalized_root == device_path(root)
                # A second owner restores only Device identity, never a live Session.
                restored = await service.prepare(captured.value)
                try:
                    assert len(set(opened)) == 4
                finally:
                    finalized = restored.finalization
                assert (
                    next(item for item in finalized.state_publications if item.key.alias == "build").status
                    == "unchanged"
                )


@pytest.mark.parametrize(
    "token,query,protocols",
    [
        ("wrong", "", ["eip.v1"]),
        ("test-only-key", "", ["eip.v1"]),
        (TOKEN, "?token=anything", ["eip.v1"]),
        (TOKEN, "", []),
    ],
)
async def test_reverse_attachment_rejects_invalid_auth_before_upgrade(tmp_path, monkeypatch, token, query, protocols):
    monkeypatch.setenv("DEVICE_TOKEN", TOKEN)
    async with app_listener(tmp_path, resource("websocket", "")) as (_, _, ws):
        with pytest.raises(InvalidStatus) as failure:
            async with connect(
                ws + "/api/devices/device-build/connect" + query,
                additional_headers={"Authorization": f"Bearer {token}"},
                subprotocols=protocols or None,
                proxy=None,
            ):
                pytest.fail("Invalid Device attachment was accepted")
        assert failure.value.response.status_code == 403


@requires_envd
@pytest.mark.parametrize("carrier", ["http", "websocket"])
@pytest.mark.parametrize("default_environment", ["workspace", "build"])
@pytest.mark.parametrize("mode", ["normal", "goal"])
async def test_root_runs_use_remote_tools_skills_and_fresh_sessions(
    tmp_path, monkeypatch, carrier, default_environment, mode
):
    """Only the model is scripted; App preparation and every file call use real providers."""
    monkeypatch.setenv("DEVICE_TOKEN", TOKEN)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        endpoint = f"http://127.0.0.1:{sock.getsockname()[1]}"
    recipe = resource(carrier, endpoint)
    opened = []
    original = EIPDeviceConnection.open_session

    async def open_session(self, *args, **kwargs):
        session = await original(self, *args, **kwargs)
        opened.append(session.session_id)
        return session

    monkeypatch.setattr(EIPDeviceConnection, "open_session", open_session)
    async with app_listener(tmp_path, recipe) as (app, _, ws):
        async with daemon(tmp_path, carrier, endpoint=endpoint if carrier == "http" else ws) as root:
            async with asyncio.timeout(10):
                while not (await app.device_info(recipe.id)).available:
                    await asyncio.sleep(0.03)
            workspace = tmp_path / "workspace"
            (workspace / "source.txt").write_text("Local workspace source")
            (root / "source.txt").write_text("Remote device source")
            skill = root / ".agents/skills/remote-build/SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_text(
                "---\nname: remote-build\ndescription: Inspect the remote build fixture.\n---\n\n"
                "Remote build instructions from the Device.\n"
            )
            agent = tmp_path / "agents/assistant.yaml"
            await app.mutate_configuration(
                relative_path="agents/assistant.yaml",
                request=ResourceMutationRequest(
                    content=agent.read_text()
                    + "capabilities:\n  - capability: skills\n"
                    + "  - capability: dynamic_environment\n    configuration: {files_enabled: true}\n"
                ),
            )
            selection = EnvironmentBindingSelection(
                device_id=recipe.id, alias="build", working_directory=device_path(root)
            )
            thread = await app.create_thread(defaults=NewThreadDefaults(project_id="project-main"))
            # Explicit aggregate paths prove these calls use EIP even though the
            # fixture daemon and Host happen to share this machine's filesystem.
            remote_output = f"/environment/build{device_path(root / 'output.txt')}"
            outside = root.parent / "outside-working-directory.txt"
            outside.write_text("Device files are not confined to the selected cwd")
            calls = (
                ("view", {"file_path": "source.txt"}),
                ("view", {"file_path": f"/environment/build{device_path(skill)}"}),
                ("view", {"file_path": str(workspace / "source.txt")}),
                ("write", {"file_path": remote_output, "content": "Written through a model tool"}),
                ("view", {"file_path": remote_output}),
                ("view", {"file_path": f"/environment/build{device_path(outside)}"}),
            )
            expected = (
                "Remote device source" if default_environment == "build" else "Local workspace source",
                "Remote build instructions from the Device.",
                "Local workspace source",
                "output.txt",
                "Written through a model tool",
                "Device files are not confined to the selected cwd",
            )
            model_calls = 0
            observed = []

            async def model(messages, info):
                nonlocal model_calls
                step = model_calls % (len(calls) + 1)
                model_calls += 1
                if step == 0:
                    assert "remote-build" in str(messages) + str(info)
                    assert {"view", "write"} <= {tool.name for tool in info.function_tools}
                else:
                    returns = [
                        part
                        for message in messages
                        if message.kind == "request"
                        for part in message.parts
                        if isinstance(part, ToolReturnPart)
                    ]
                    assert expected[step - 1] in str(returns[-1].content)
                    observed.append(calls[step - 1][0])
                if step < len(calls):
                    name, arguments = calls[step]
                    yield {
                        0: DeltaToolCall(name=name, json_args=json.dumps(arguments), tool_call_id=f"call-{model_calls}")
                    }
                else:
                    output = "Read local and remote files and wrote the remote result."
                    # One Goal follow-up repeats real file operations in the same Session.
                    if mode == "goal" and model_calls % (2 * (len(calls) + 1)) == 0:
                        output += "\n[GOAL_COMPLETE]"
                    yield output

            async def resolve(self, context, model_id):
                return FunctionModel(stream_function=model)

            monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
            captures = []
            for index in range(2):
                receipt = await app.submit_thread(
                    thread_id=thread.thread_id,
                    prompt="Use remote-build and inspect both environments.",
                    mode=mode,
                    environment=EnvironmentSelectionPatch(
                        local_roots=(str(workspace),),
                        environment_bindings=(selection,),
                        default_environment=default_environment,
                    ),
                )
                async with asyncio.timeout(30):
                    operation = await app.wait_root_operation(receipt.receipt_id)
                assert operation.status is RootOperationStatus.completed, operation
                assert operation.outcome.execution.output.startswith("Read local and remote")
                if mode == "goal":
                    assert operation.goal.status == "verified"
                    assert operation.goal.iteration == 1
                else:
                    assert operation.goal is None
                capture = await app.inspect_operation_configuration(receipt.receipt_id)
                assert capture is not None
                assert capture.environment_bindings == (selection,)
                assert capture.default_environment == default_environment
                captures.append(capture)
                assert len(set(opened)) == index + 1
                assert (root / "output.txt").read_text() == "Written through a model tool"
                assert not (workspace / "output.txt").exists()
            assert (await app.get_thread(thread.thread_id)).thread.configuration == thread.configuration
            assert observed == [name for name, _ in calls] * (4 if mode == "goal" else 2)
            assert (await app.get_thread(thread.thread_id)).continuation_id is not None
            assert (await app.device_info(recipe.id)).available
            assert captures[0].environment_bindings == captures[1].environment_bindings


@requires_envd
@pytest.mark.parametrize("mode", ["normal", "goal"])
async def test_restart_reuses_captured_device_recipe_and_opens_a_fresh_session(tmp_path, monkeypatch, mode):
    """A restart restores accepted bindings, not the edited configuration catalog."""
    from anyio import Event, create_task_group, fail_after, sleep
    from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart

    from .test_restart_recovery import install_model, release_during_shutdown

    monkeypatch.setenv("DEVICE_TOKEN", TOKEN)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        endpoint = f"http://127.0.0.1:{sock.getsockname()[1]}"
    recipe = resource("http", endpoint)
    configuration = _write_configuration(tmp_path)
    (tmp_path / "devices").mkdir()
    device_file = tmp_path / "devices/build.yaml"
    device_file.write_text(recipe.model_dump_json())
    agent_file = tmp_path / "agents/assistant.yaml"
    agent_file.write_text(
        agent_file.read_text()
        + "capabilities:\n  - capability: dynamic_environment\n    configuration: {files_enabled: true}\n"
    )
    settings = _settings(tmp_path / "state")
    started, release = Event(), Event()
    opened, requests = [], []
    original = EIPDeviceConnection.open_session

    async def open_session(self, *args, **kwargs):
        session = await original(self, *args, **kwargs)
        opened.append(session.session_id)
        return session

    monkeypatch.setattr(EIPDeviceConnection, "open_session", open_session)
    async with daemon(tmp_path, "http", endpoint=endpoint) as root:
        (root / "proof.txt").write_text("Captured remote binding survived restart")

        async def model(messages, info):
            requests.append(messages)
            if len(requests) == 1:
                started.set()
                await release.wait()
                return ModelResponse(parts=[ToolCallPart("store", {"key": "once", "value": True}, tool_call_id="once")])
            if len(requests) == 2:
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            "view",
                            {"file_path": f"/environment/build{device_path(root / 'proof.txt')}"},
                            tool_call_id="remote",
                        )
                    ]
                )
            returns = [part for message in messages for part in message.parts if isinstance(part, ToolReturnPart)]
            assert sum(part.tool_call_id == "once" for part in returns) == 1
            assert "Captured remote binding survived restart" in str(returns[-1].content)
            return ModelResponse(parts=[TextPart("Restored\n[GOAL_COMPLETE]" if mode == "goal" else "Restored")])

        install_model(monkeypatch, model)
        async with (
            create_task_group() as group,
            open_harness_ui_app(settings, configuration_path=configuration, host_mode="webui") as app,
        ):
            with fail_after(10):
                while not (await app.device_info(recipe.id)).available:
                    await sleep(0.02)
            thread = await app.create_thread(
                defaults=NewThreadDefaults(
                    environment_bindings=(
                        EnvironmentBindingSelection(
                            device_id=recipe.id, alias="build", working_directory=device_path(root)
                        ),
                    ),
                    default_environment="build",
                )
            )
            await app.submit_thread(thread_id=thread.thread_id, prompt="Read once, then continue", mode=mode)
            with fail_after(10):
                await started.wait()
            group.start_soon(release_during_shutdown, app, release)
        assert len(opened) == 1
        device_file.unlink()
        async with open_harness_ui_app(settings, configuration_path=configuration, host_mode="webui") as app:
            with fail_after(10):
                while (await app.get_thread(thread.thread_id)).thread.completion is None:
                    await sleep(0.02)
            assert len(requests) == 3
            assert len(set(opened)) == 2
            goal = (await app.get_thread(thread.thread_id)).thread.goal
            if mode == "goal":
                assert goal.status == "verified"
                assert goal.objective == "Read once, then continue"
                assert goal.iteration == 0
            else:
                assert goal is None
