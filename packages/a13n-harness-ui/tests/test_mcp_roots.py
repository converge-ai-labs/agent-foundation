"""Exercise the Roots capability and change notifications across real MCP transports."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from a13n_harness.configuration import RunConfiguration
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.configuration.mutation import ResourceMutationRequest
from a13n_harness_ui.mcp_adapters import HarnessUiMCP
from a13n_harness_ui.storage.contracts import ThreadConfigurationMutation, ThreadConfigurationPatch
from a13n_harness_ui.surfaces import ProjectDefaultsApply
from a13n_harness_ui.thread_service import RootThreadDefaults

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


async def _toolset(app, thread_id, *, with_registry=True):
    source = await app.current_configuration()
    assert source is not None
    recipe = app._mcp_app_owners.resolver._mcp_recipes(source, ("mcp-roots",))[0]
    capability = HarnessUiMCP(recipe, connections=app._mcp_connections if with_registry else None)
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
async def test_stdio_roots_follow_saved_thread_and_notify_live_client(tmp_path, monkeypatch, retained):
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
                assert await toolset.direct_call_tool("inspect", {}) == first
                preview = await app.preview_project_defaults(thread_id=thread.thread_id, environments_only=True)
                await app.apply_project_defaults(
                    thread_id=thread.thread_id,
                    request=ProjectDefaultsApply(
                        expected_version=preview.expected_version, defaults_digest=preview.defaults_digest
                    ),
                    environments_only=True,
                )
                async with asyncio.timeout(5):
                    while (current := await toolset.direct_call_tool("inspect", {}))["changes"] == 0:
                        await asyncio.sleep(0.01)
                assert current["changes"] == 1
                assert current["roots"] == [_root(project["roots"][0]["path"])]

                async def broken_notification():
                    raise RuntimeError("transport closed")

                monkeypatch.setattr(toolset.connection.client, "send_roots_list_changed", broken_notification)
                await app.update_thread_configuration(
                    thread_id=thread.thread_id,
                    mutation=ThreadConfigurationMutation(
                        expected_version=preview.expected_version + 1, patch=ThreadConfigurationPatch(local_roots=())
                    ),
                )
                assert (await toolset.direct_call_tool("inspect", {}))["roots"] == []
        finally:
            for close in cleanup:
                await close()


@pytest.mark.parametrize(
    ("project_id", "explicit_path", "expose_roots", "with_registry", "advertised"),
    [
        (None, False, None, True, True),
        (None, True, None, True, True),
        ("project-main", True, None, True, True),
        ("project-main", True, False, True, False),
        ("project-main", True, None, False, False),
    ],
)
async def test_stdio_roots_use_thread_selection(
    tmp_path, project_id, explicit_path, expose_roots, with_registry, advertised
):
    root, _ = _configure(
        tmp_path,
        {"command": sys.executable, "args": [str(Path(__file__).with_name("fixtures") / "mcp_roots_server.py")]},
        expose_roots,
        retained=False,
    )
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root) as app:
        path = str(tmp_path / "custom worktree")
        thread = await app.create_thread(
            defaults=RootThreadDefaults(project_id=project_id, local_roots=(path,) if explicit_path else None)
        )
        toolset, cleanup = await _toolset(app, thread.thread_id, with_registry=with_registry)
        try:
            async with toolset:
                result = await toolset.direct_call_tool("inspect", {})
                assert result["advertised"] is advertised
                assert result["roots"] == ([_root(path)] if advertised and explicit_path else [])
        finally:
            for close in cleanup:
                await close()


async def test_modern_stdio_refreshes_roots_without_legacy_notification(tmp_path, monkeypatch):
    root, project = _configure(
        tmp_path,
        {"command": sys.executable, "args": [str(Path(__file__).with_name("fixtures") / "mcp_roots_server.py")]},
        None,
        retained=True,
    )
    root.write_text(root.read_text().replace("mcp-roots: legacy", 'mcp-roots: "2026-07-28"'))
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root) as app:
        thread = await app.create_thread()
        toolset, _ = await _toolset(app, thread.thread_id)
        async with toolset:
            assert toolset.connection.client.protocol_version == "2026-07-28"
            assert (await toolset.direct_call_tool("inspect", {}))["roots"] == [
                _root(item["path"]) for item in project["roots"]
            ]
            sent = []

            async def record_notification():
                sent.append(True)

            monkeypatch.setattr(toolset.connection.client, "send_roots_list_changed", record_notification)
            path = str(tmp_path / "modern replacement")
            await app.update_thread_configuration(
                thread_id=thread.thread_id,
                mutation=ThreadConfigurationMutation(
                    expected_version=1, patch=ThreadConfigurationPatch(local_roots=(path,))
                ),
            )
            assert not sent
            assert (await toolset.direct_call_tool("inspect", {}))["roots"] == [_root(path)]


@pytest.mark.parametrize("expose_roots", [None, True])
async def test_http_roots_require_explicit_opt_in(tmp_path, expose_roots):
    endpoint = "http://127.0.0.1:1/mcp"
    root, project = _configure(tmp_path, {"url": endpoint}, expose_roots, retained=False)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root) as app:
        thread = await app.create_thread()
        source = await app.current_configuration()
        assert source is not None
        recipe = app._mcp_app_owners.resolver._mcp_recipes(source, ("mcp-roots",))[0]
        assert recipe.roots_enabled is (expose_roots is True)
        scope = app._mcp_connections._roots_scope(thread.thread_id, "mcp-roots", recipe.transport)
        assert scope.enabled() is (expose_roots is True)
        if expose_roots:
            assert list(await scope.paths()) == [entry["path"] for entry in project["roots"]]
            # A live session stays open, but reloading configuration with opt-out
            # immediately revokes root disclosure before any newly accepted path is read.
            (tmp_path / "mcp/roots.json").write_text(
                json.dumps({"mcpServers": {"roots": {"url": endpoint, "expose_roots": False}}})
            )
            project["roots"] = [{"path": str(tmp_path / "private replacement")}]
            (tmp_path / "projects/main.yaml").write_text(json.dumps(project))
            await app.reload_configuration()
            assert scope.enabled() is False
