"""Exercise the Roots capability and change notifications across real MCP transports."""

from __future__ import annotations

import asyncio
import json
import socket
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import uvicorn
from a13n_harness.configuration import RunConfiguration
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.configuration.mutation import ResourceMutationRequest
from a13n_harness_ui.mcp_adapters import HarnessUiMCP
from a13n_harness_ui.thread_service import RootThreadDefaults

from .fixtures.mcp_roots_server import roots_server
from .test_app import _settings, _write_configuration
from .test_device_configuration import binding, device

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("migrated_databases")]


def _configure(tmp_path: Path, transport: dict, expose_roots: bool | None, *, retained: bool) -> tuple[Path, dict]:
    root = _write_configuration(tmp_path)
    directory = tmp_path / "mcp"
    directory.mkdir()
    (tmp_path / "devices").mkdir()
    (tmp_path / "devices/build.yaml").write_text(device().model_dump_json())
    project = {
        "schema_version": "1",
        "kind": "project",
        "id": "project-main",
        "name": "Main",
        "roots": [{"path": str(tmp_path / "a # % 中文")}, {"path": str(tmp_path / "another")}],
        "defaults": {"environment_bindings": [binding().model_dump(mode="json")], "default_environment": "build"},
    }
    (tmp_path / "projects/main.yaml").write_text(json.dumps(project))
    entry = dict(transport)
    if expose_roots is not None:
        entry["expose_roots"] = expose_roots
    (directory / "roots.json").write_text(json.dumps({"mcpServers": {"roots": entry}}))
    agent = tmp_path / "agents/assistant.yaml"
    agent.write_text(agent.read_text() + "mcp_servers: [mcp-roots]\n")
    root.write_text(
        root.read_text()
        + "mcp:\n  protocol_overrides: {mcp-roots: legacy}\n"
        + ("  host_owned_servers: [mcp-roots]\n" if retained else "")
    )
    return root, project


async def _toolset(app, thread_id):
    source = await app.current_configuration()
    assert source is not None
    recipe = app._mcp_app_owners.resolver._mcp_recipes(source, ("mcp-roots",))[0]
    capability = HarnessUiMCP(recipe, connections=app._mcp_connections)
    cleanup = []
    ctx = SimpleNamespace(
        deps=SimpleNamespace(
            thread_id=thread_id,
            configuration=RunConfiguration(),
            _register_run_cleanup=lambda _, close: cleanup.append(close),
        )
    )
    factory = await capability._prepare_toolset(ctx)
    return await factory(), cleanup


def _root(path: str) -> dict[str, str]:
    local = Path(path)
    return {"uri": local.as_uri(), "name": local.name}


@pytest.mark.parametrize("retained", [False, True])
async def test_stdio_roots_follow_accepted_project_and_notify_live_client(tmp_path, monkeypatch, retained):
    root, project = _configure(
        tmp_path,
        {"command": sys.executable, "args": [str(Path(__file__).with_name("fixtures") / "mcp_roots_server.py")]},
        None,
        retained=retained,
    )
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root) as app:
        assert (status := await app.status()).accepted_generation_digest, status.candidate_error_message
        thread = await app.create_thread()
        toolset, cleanup = await _toolset(app, thread.thread_id)
        try:
            async with toolset:
                first = await toolset.direct_call_tool("inspect", {})
                assert first == {
                    "advertised": True,
                    "list_changed": True,
                    "changes": 0,
                    "roots": [_root(entry["path"]) for entry in project["roots"]],
                }
                project["defaults"]["environment_bindings"] = [binding("/C:/different").model_dump(mode="json")]
                await app.mutate_configuration(
                    relative_path="projects/main.yaml", request=ResourceMutationRequest(content=json.dumps(project))
                )
                assert (await toolset.direct_call_tool("inspect", {}))["changes"] == 0

                project["roots"] = [{"path": str(tmp_path / "new root")}]
                (tmp_path / "projects/main.yaml").write_text(json.dumps(project))
                await app.reload_configuration()
                async with asyncio.timeout(5):
                    while (current := await toolset.direct_call_tool("inspect", {}))["changes"] == 0:
                        await asyncio.sleep(0.01)
                assert current["changes"] == 1
                assert current["roots"] == [_root(project["roots"][0]["path"])]

                async def broken_notification():
                    raise RuntimeError("transport closed")

                monkeypatch.setattr(toolset.connection.client, "send_roots_list_changed", broken_notification)
                project["roots"] = []
                await app.mutate_configuration(
                    relative_path="projects/main.yaml", request=ResourceMutationRequest(content=json.dumps(project))
                )
                assert (await toolset.direct_call_tool("inspect", {}))["roots"] == []
        finally:
            for close in cleanup:
                await close()


@pytest.mark.parametrize("expose_roots", [None, False])
async def test_stdio_roots_without_project_or_explicit_opt_out(tmp_path, expose_roots):
    root, _ = _configure(
        tmp_path,
        {"command": sys.executable, "args": [str(Path(__file__).with_name("fixtures") / "mcp_roots_server.py")]},
        expose_roots,
        retained=False,
    )
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root) as app:
        thread = await app.create_thread(
            defaults=RootThreadDefaults(project_id=None if expose_roots is None else "project-main")
        )
        toolset, cleanup = await _toolset(app, thread.thread_id)
        try:
            async with toolset:
                result = await toolset.direct_call_tool("inspect", {})
                assert result["advertised"] is (expose_roots is None)
                assert result["roots"] == []
        finally:
            for close in cleanup:
                await close()


@pytest.mark.parametrize("expose_roots", [None, True])
async def test_http_roots_require_explicit_opt_in(tmp_path, expose_roots):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        endpoint = f"http://127.0.0.1:{port}/mcp"
        server = roots_server()
        runner = uvicorn.Server(uvicorn.Config(server.streamable_http_app(), lifespan="on", log_config=None))
        task = asyncio.create_task(runner.serve(sockets=[listener]))
        try:
            async with asyncio.timeout(5):
                while not runner.started:
                    await asyncio.sleep(0.01)
            root, project = _configure(tmp_path, {"url": endpoint}, expose_roots, retained=False)
            async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root) as app:
                thread = await app.create_thread()
                toolset, cleanup = await _toolset(app, thread.thread_id)
                try:
                    async with toolset:
                        result = await toolset.direct_call_tool("inspect", {})
                        assert result["advertised"] is (expose_roots is True)
                        assert result["roots"] == (
                            [_root(entry["path"]) for entry in project["roots"]] if expose_roots else []
                        )
                        if expose_roots:
                            # A Run-local session stays alive, but the new configuration
                            # can revoke its access before any newly accepted path is read.
                            (tmp_path / "mcp/roots.json").write_text(
                                json.dumps({"mcpServers": {"roots": {"url": endpoint, "expose_roots": False}}})
                            )
                            project["roots"] = [{"path": str(tmp_path / "private replacement")}]
                            (tmp_path / "projects/main.yaml").write_text(json.dumps(project))
                            await app.reload_configuration()
                            result = await toolset.direct_call_tool("inspect", {})
                            assert result["roots"] == []
                            assert result["changes"] == 0
                finally:
                    for close in cleanup:
                        await close()
        finally:
            runner.should_exit = True
            await task
