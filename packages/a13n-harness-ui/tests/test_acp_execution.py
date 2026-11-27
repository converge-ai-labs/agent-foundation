"""Exercise outbound ACP through the normal Harness middleware and state boundary."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest
from a13n_harness import HarnessBuilder
from a13n_harness.acp import AcpSessionRuntime, AcpSessionSetup
from a13n_harness.context import AgentContext
from a13n_harness_ui.acp_auth import AcpAuthRequest, acp_auth, auth_input_scope
from a13n_harness_ui.acp_execution import AcpTurn
from a13n_harness_ui.acp_inputs import AcpInputs
from a13n_harness_ui.acp_sessions import AcpOptionChange, AcpSessions, local_transport
from a13n_harness_ui.configuration.models import AcpAgentResource
from a13n_harness_ui.errors import HarnessUiError
from a13n_harness_ui.mcp_runtime.inputs import Inputs, McpInputResponse
from acp import schema
from pydantic_ai import RunContext
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.messages import BinaryContent, TextContent
from pydantic_ai.toolsets import FunctionToolset

pytestmark = pytest.mark.anyio

_PEER = Path(__file__).resolve().parents[2] / "a13n-harness/tests/fixtures/acp_peer.py"


def _resource(*peer_flags: str) -> AcpAgentResource:
    return AcpAgentResource(
        schema_version="1",
        kind="acp_agent",
        id="acp-fixture",
        name="Fixture",
        command=sys.executable,
        args=(str(_PEER), "http", *peer_flags),
    )


def _ui_peer_config(tmp_path: Path, *flags: str) -> Path:
    config = tmp_path / "a13n-harness-ui.yaml"
    config.write_text('schema_version: "1"\ndefaults:\n  agent: acp-fixture\n')
    resources = tmp_path / "acp_agents"
    resources.mkdir()
    resource = _resource().model_copy(update={"args": (str(_PEER), "ui", "use-http", *flags)})
    (resources / "fixture.yaml").write_text(resource.model_dump_json(exclude_none=True))
    return config


def test_native_launch_inherits_local_cli_environment(tmp_path: Path) -> None:
    user_bin = tmp_path / ".local" / "bin"
    user_bin.mkdir(parents=True)
    inherited = {
        "PATH": "/bin",
        "HOME": str(tmp_path),
        "DBUS_SESSION_BUS_ADDRESS": "unix:path=/tmp/test-bus",
        "HTTPS_PROXY": "http://localhost:8080",
        "GEMINI_HOME": "/test/profile",
    }
    transport = local_transport(
        _resource(),
        "/workspace",
        environ={**inherited, "A13N_ACP_MCP_URL": "internal", "A13N_ACP_MCP_TOKEN": "internal"},
    )
    assert not transport.inherit_environment
    assert transport.environment == {
        **inherited,
        "PATH": f"/bin{os.pathsep}{user_bin}" if os.name == "posix" else "/bin",
    }


async def test_acp_auth_probe_and_browser_login(tmp_path: Path) -> None:
    resource = _resource().model_copy(update={"args": (str(_PEER), "auth")})
    changed = asyncio.Event()

    async def notify(_: str) -> None:
        changed.set()

    inputs = Inputs(notify)
    missing = resource.model_copy(update={"command": str(tmp_path / "missing")})
    assert (await acp_auth(missing, tmp_path, AcpAuthRequest(action="probe"), inputs)).state == "executable_missing"
    status = await acp_auth(resource, tmp_path, AcpAuthRequest(action="probe"), inputs)
    assert status.state == "ready" and status.logout_supported
    logged_out = resource.model_copy(update={"args": (str(_PEER), "auth-required")})
    assert (await acp_auth(logged_out, tmp_path, AcpAuthRequest(action="probe"), inputs)).state == "auth_required"
    browser, terminal = status.methods
    assert browser.id == "browser" and browser.kind == "agent"
    assert terminal.kind == "terminal" and terminal.argv == (sys.executable, str(_PEER), "auth", "--login")
    assert terminal.environment_keys == ("FIXTURE_LOGIN",) and "fixture-secret" not in status.model_dump_json()
    with pytest.raises(HarnessUiError, match="protocol login method"):
        await acp_auth(resource, tmp_path, AcpAuthRequest(action="login", method_id="terminal"), inputs)
    try:
        async with asyncio.TaskGroup() as group:
            login = group.create_task(
                acp_auth(resource, tmp_path, AcpAuthRequest(action="login", method_id="browser"), inputs)
            )
            async with asyncio.timeout(10):
                await changed.wait()
            scope = {auth_input_scope(resource.id)}
            (request,) = inputs.requests(scope)
            assert request.mode == "url" and request.state == "pending"
            assert request.url == "https://example.com/sign-in"
            assert not login.done()
            await inputs.respond(scope, request.request_id, McpInputResponse(action="accept"))
        assert login.result().state == "login_completed"
        assert (await acp_auth(resource, tmp_path, AcpAuthRequest(action="logout"), inputs)).state == "logout_completed"
    finally:
        await inputs.close()


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (McpInputResponse(action="accept", content={}), "yes-id"),
        (McpInputResponse(action="decline"), "no-id"),
        (McpInputResponse(action="cancel"), None),
    ],
)
async def test_binary_acp_permission_uses_form_actions(response: McpInputResponse, expected: str | None) -> None:
    ready = asyncio.Event()

    async def notify(_: str) -> None:
        ready.set()

    inputs = Inputs(notify)
    permissions = AcpInputs(inputs, "thread", "external", lambda: None)
    options = (
        schema.PermissionOption(kind="reject_once", name="Deny", option_id="no-id"),
        schema.PermissionOption(kind="allow_once", name="Allow", option_id="yes-id"),
    )
    async with asyncio.TaskGroup() as group:
        answer = group.create_task(permissions.permission(schema.ToolCallUpdate(tool_call_id="call"), options))
        async with asyncio.timeout(5):
            await ready.wait()
        (request,) = inputs.requests({"thread"})
        assert request.schema_ == {"type": "object", "properties": {}}
        await inputs.respond({"thread"}, request.request_id, response)
    assert answer.result() == expected


async def test_acp_turn_runs_in_harness_and_reuses_session(tmp_path: Path) -> None:
    sessions = AcpSessions()
    toolset: FunctionToolset[AgentContext] = FunctionToolset()
    seen: list[tuple[str, str]] = []

    @toolset.tool
    async def identify(ctx: RunContext[AgentContext]) -> str:
        assert ctx.tool_call_id is not None
        seen.append((ctx.deps.run_id, ctx.tool_call_id))
        return ctx.tool_call_id

    events: list[dict[str, Any]] = []
    changed = asyncio.Event()

    async def notify(_: str) -> None:
        changed.set()

    inputs = Inputs(notify)

    def turn() -> AcpTurn:
        return AcpTurn(
            sessions,
            _resource("questions"),
            "digest",
            AcpSessionSetup(cwd=str(tmp_path), instructions=("guide",)),
            events.append,
            toolsets=(toolset,),
            inputs=inputs,
        )

    def executable(plugin: AcpTurn):
        return HarnessBuilder(configured_plugins_enabled=False).build(AgentSpec(), output_type=str, plugins=(plugin,))

    try:
        first = await executable(turn()).run("hello")
        assert first.status == "completed"
        assert first.output is not None and first.output.endswith("done:hello")
        assert first.state is not None
        assert first.state.agent_context_state.get("a13n.acp") is not None
        assert len(first.new_messages()) == 2
        assert "HIDDEN ECHO" not in str(first.all_messages())
        assert [call for _, call in seen] == ["one", "exact", first.run_id, first.run_id]
        assert len([event for event in events if event["type"] == "TOOL_CALL_START"]) == 3

        plugin = turn()
        async with asyncio.TaskGroup() as group:
            running = group.create_task(executable(plugin).run("wait", previous_state=first.state))
            async with asyncio.timeout(10):
                await changed.wait()
            (request,) = inputs.requests({first.thread_id})
            assert request.run_id is not None and request.run_id != first.run_id
            assert request.tool_call_id == "permission"
            assert request.state == "pending"
            assert await plugin.steer("Keep going") == "accepted"
            await inputs.respond(
                {first.thread_id}, request.request_id, McpInputResponse(action="accept", content={"option": "allow"})
            )
        second = running.result()
        assert second.status == "completed" and second.output == "allowed"
        assert len(second.all_messages()) == 5
        assert "Keep going" in str(second.all_messages()[-2])

        changed.clear()
        async with asyncio.TaskGroup() as group:
            running = group.create_task(executable(turn()).run("questions", previous_state=second.state))
            async with asyncio.timeout(10):
                await changed.wait()
            (request,) = [view for view in inputs.requests({first.thread_id}) if view.state == "pending"]
            assert request.mode == "question" and request.tool_call_id == "question"
            assert request.questions[0].options[0].label == "JSON"
            with pytest.raises(HarnessUiError, match="does not answer"):
                await inputs.respond(
                    {first.thread_id},
                    request.request_id,
                    McpInputResponse(action="accept", content={"answers": {"unknown": "JSON"}}),
                )
            await inputs.respond(
                {first.thread_id},
                request.request_id,
                McpInputResponse(action="accept", content={"answers": {"Which format?": "JSON"}}),
            )
        assert running.result().output == "JSON"
        assert [event["toolCallId"] for event in events if event["type"] == "TOOL_CALL_START"].count("question") == 1
    finally:
        await inputs.close()
        await sessions.close()


async def test_acp_goal_audit_and_first_turn_stop(tmp_path: Path) -> None:
    from a13n_harness_ui.goal import GoalCapability, GoalView, saved_goal

    sessions = AcpSessions()
    resource = _resource().model_copy(update={"args": (str(_PEER), "ui")})
    setup = AcpSessionSetup(cwd=str(tmp_path))
    ready = asyncio.Event()

    def observe(event: dict[str, Any]) -> None:
        if event.get("delta") == "partial reply":
            ready.set()

    def turn(**kwargs: Any) -> AcpTurn:
        return AcpTurn(sessions, resource, "digest", setup, observe, **kwargs)

    def executable(plugin: AcpTurn):
        return HarnessBuilder(configured_plugins_enabled=False).build(
            AgentSpec(), output_type=str | None, plugins=(plugin,)
        )

    try:
        result = await executable(turn(goal=GoalCapability(GoalView(objective="Verify the work")))).run("goal")
        assert result.status == "completed"
        assert result.output == "Audit complete.\n[GOAL_COMPLETE]"
        assert result.state is not None
        goal = saved_goal(result.state)
        assert goal is not None and goal.status == "verified" and goal.iteration == 1
        assert "acp_compaction" not in str(result.all_messages())

        plugin = turn()
        async with asyncio.TaskGroup() as group:
            pending = group.create_task(executable(plugin).run("cancel-me"))
            async with asyncio.timeout(10):
                await ready.wait()
            assert await plugin.stop()
        stopped = pending.result()
        assert stopped.status == "cancelled"
        assert stopped.state is not None and stopped.state.agent_context_state.get("a13n.acp") is not None
        assert "partial reply" in str(stopped.state.message_history)
        followup = await executable(turn()).run("again", previous_state=stopped.state)
        assert followup.output == "done:again"
    finally:
        await sessions.close()


async def test_acp_thread_without_native_model_runs_and_saves_display(tmp_path: Path) -> None:
    import json

    from a13n_harness_ui.app import open_harness_ui_app
    from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings
    from a13n_harness_ui.surfaces import RootOperationStatus

    root = tmp_path / "a13n-harness-ui.yaml"
    root.write_text('schema_version: "1"\ndefaults:\n  agent: acp-fixture\n')
    directory = tmp_path / "acp_agents"
    directory.mkdir()
    resource = _resource().model_copy(update={"args": (str(_PEER), "ui", "resident")})
    (directory / "fixture.yaml").write_text(json.dumps(resource.model_dump(mode="json", exclude_none=True)))
    settings = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"))
    async with open_harness_ui_app(settings, configuration_path=root) as app:
        thread = await app.create_thread()
        assert (await app._store.threads.get(thread.thread_id)).configuration.agent_source.kind == "acp_agent"
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="hello")
        outcome = await app.wait_root_operation(receipt.receipt_id)
        assert outcome.status is RootOperationStatus.completed, outcome.failure
        assert (await app.get_thread(thread.thread_id)).thread.excerpt.latest_reply == "done:hello"
        captured = await app.inspect_operation_configuration(receipt.receipt_id)
        assert captured is not None
        assert captured.agent.source_kind == "acp_agent"
        assert captured.agent.source_id == "acp-fixture"
        assert captured.agent.model_id is None

        rejected = await app.submit_thread(
            thread_id=thread.thread_id,
            prompt=(BinaryContent(data=b"example", media_type="application/pdf"),),
        )
        assert (await app.wait_root_operation(rejected.receipt_id)).status is RootOperationStatus.failed
        again = await app.submit_thread(thread_id=thread.thread_id, prompt="again")
        assert (await app.wait_root_operation(again.receipt_id)).status is RootOperationStatus.completed
        assert (await app.get_thread(thread.thread_id)).thread.excerpt.latest_reply == "done:again"

        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="observe")
        observed = await app.wait_root_operation(receipt.receipt_id)
        assert observed.status is RootOperationStatus.completed
        assert (await app.get_thread(thread.thread_id)).thread.title == "Peer title"
        usage = await app.context_usage(thread.thread_id)
        assert (usage.latest_request_tokens, usage.context_window) == (24, 1000)
        assert [command.name for command in await app.acp_commands(thread.thread_id)] == ["plan"]

        stopped_thread = await app.create_thread()
        async with app.live_events(root_thread_id=stopped_thread.thread_id) as live:
            receipt = await app.submit_thread(thread_id=stopped_thread.thread_id, prompt="cancel-me")
            async with asyncio.timeout(15):
                while True:
                    event = await live.receive()
                    if "partial reply" in str(event.payload):
                        break
            # Queued steering on this peer leaves the active prompt running until explicit Stop.
            assert (
                await app.steer_root_operation(
                    receipt_id=receipt.receipt_id, message="steer", allow_acp_interrupt=False
                )
            ).accepted
            assert (await app.cancel_root_operation(receipt.receipt_id)).accepted
        stopped = await app.wait_root_operation(receipt.receipt_id)
        assert stopped.status is RootOperationStatus.cancelled
        assert (await app.get_thread(stopped_thread.thread_id)).continuation_id is not None
        resume = await app.submit_thread(thread_id=stopped_thread.thread_id, prompt="again")
        assert (await app.wait_root_operation(resume.receipt_id)).status is RootOperationStatus.completed
        assert (await app.get_thread(stopped_thread.thread_id)).thread.excerpt.latest_reply == "done:again"


@pytest.mark.parametrize("input_tokens,output_tokens", [(8, 2), (13, 5)])
@pytest.mark.parametrize(
    "current_value,model_name", [(" model-a ", "model-a"), ("", None), (" \t", None), (True, None)]
)
async def test_acp_usage_includes_cache_and_thinking_without_double_counting(
    input_tokens, output_tokens, current_value, model_name
):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    from acp import schema

    ledger = SimpleNamespace(
        run_id="run-test",
        instance=SimpleNamespace(agent_instance_id="agent-test", parent_agent_instance_id=None, delegation_id=None),
        next_model_ordinal=lambda: 1,
        finish=Mock(return_value=None),
        _flush=AsyncMock(),
    )
    turn = AcpTurn(
        resource=_resource(), sessions=AcpSessions(), setup=AcpSessionSetup(cwd="/workspace"), digest="fixture"
    )
    await turn._record_usage(
        SimpleNamespace(usage_attribution=ledger),
        SimpleNamespace(config_options=(SimpleNamespace(id="model", category=None, current_value=current_value),)),
        schema.PromptResponse(
            stop_reason="end_turn",
            usage=schema.Usage(
                total_tokens=18,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_read_tokens=5,
                thought_tokens=3,
            ),
        ),
    )
    record = ledger.finish.call_args.args[1]
    assert record.model_name == model_name
    usage = record.request_usage
    assert (usage.input_tokens, usage.output_tokens, usage.cache_read_tokens) == (13, 5, 5)
    assert usage.details == {"thoughts_tokens": 3}
    ledger._flush.assert_awaited_once()


@pytest.mark.parametrize("flow", ["files", "inbox"])
async def test_acp_host_inputs_and_inbox_links(tmp_path: Path, flow: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from a13n_harness_ui.app import open_harness_ui_app
    from a13n_harness_ui.browser import HostBrowsers
    from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings
    from a13n_harness_ui.surfaces import RootOperationStatus
    from a13n_harness_ui.thread_files import AttachmentUpload

    root = tmp_path / "a13n-harness-ui.yaml"
    root.write_text('schema_version: "1"\ndefaults:\n  agent: acp-fixture\ninput:\n  long_text_threshold_chars: 512\n')
    directory = tmp_path / "acp_agents"
    directory.mkdir()
    peer_flags = (flow,)
    if flow == "inbox":
        # Advertise a ready binding without launching Chromium; the stdio peer
        # checks the real Browser capabilities via MCP tools/list on turn one.
        async def admit_browser(_self: HostBrowsers, _thread_id: str) -> str:
            return "fixture-browser"

        monkeypatch.setattr(HostBrowsers, "admit", admit_browser)
        peer_flags += ("browser",)
    resource = _resource().model_copy(update={"args": (str(_PEER), "ui", *peer_flags)})
    (directory / "fixture.yaml").write_text(resource.model_dump_json(exclude_none=True))
    async with open_harness_ui_app(
        HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data")),
        configuration_path=root,
        host_mode="webui",
    ) as app:
        thread = await app.create_thread()
        if flow == "files":
            attachment = await app.stage_thread_attachment(
                thread_id=thread.thread_id,
                upload=AttachmentUpload("sample.csv", b"name,value\nexample,42\n", "text/csv"),
            )
            async with app.live_events(root_thread_id=thread.thread_id) as live:
                receipt = await app.submit_thread(
                    thread_id=thread.thread_id, prompt="initial line\n" * 80, attachment_ids=(attachment.attachment_id,)
                )
                async with asyncio.timeout(15):
                    while "Files read" not in str((await live.receive()).payload):
                        pass
                steer = await app.steer_root_operation(receipt_id=receipt.receipt_id, message="steered line\n" * 80)
                assert steer.accepted
                outcome = await app.wait_root_operation(receipt.receipt_id)
                assert outcome.status is RootOperationStatus.completed, outcome.failure
            assert (await app.get_thread(thread.thread_id)).thread.excerpt.latest_reply == "Steering file read"
            transcript = await app.get_thread_transcript(thread_id=thread.thread_id)
            replies = [part.text for entry in transcript.entries for part in entry.parts if part.kind == "assistant"]
            assert replies == ["Files read", "Steering file read"]
        else:
            options = await app.acp_options(thread.thread_id)
            assert [(item.id, item.value) for item in options] == [("mode", "chat")]
            prewarmed = await app._store.threads.acp_state(thread.thread_id)
            assert prewarmed is not None
            assert (await app.get_thread(thread.thread_id)).continuation_id is None
            changed = await app.acp_options(thread.thread_id, AcpOptionChange(id="mode", value="plan"))
            assert changed[0].value == "plan"
            for prompt in ("inbox exact", "inbox fallback"):
                receipt = await app.submit_thread(thread_id=thread.thread_id, prompt=prompt)
                outcome = await app.wait_root_operation(receipt.receipt_id)
                assert outcome.status is RootOperationStatus.completed, outcome.failure
            options = await app.acp_options(thread.thread_id)
            assert [(item.id, item.value) for item in options] == [("mode", "plan")]
            assert app._acp_sessions.matches(
                thread.thread_id, json.loads(prewarmed)["digest"], json.loads(prewarmed)["session_id"]
            )
            history = await app.get_thread_transcript(thread_id=thread.thread_id)
            items = (await app.inbox().page(state="open", before=None, limit=10)).items
            assert len(items) == len(history.turns) == 2
            assert items[1].tool_call_id == "inbox-exact"
            assert items[0].tool_call_id == history.turns[1].turn_id
            for item, turn in zip(reversed(items), history.turns, strict=True):
                location = await app.inbox().location(item.item_id)
                assert (location.continuation_id, location.turn_id) == (history.continuation_id, turn.turn_id)


@pytest.mark.parametrize("peer_flag", ["resident", "never-persisted"])
async def test_acp_prewarm_replays_options_after_host_restart(tmp_path: Path, peer_flag: str) -> None:
    from a13n_harness_ui.app import open_harness_ui_app
    from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings

    config = tmp_path / "a13n-harness-ui.yaml"
    config.write_text('schema_version: "1"\ndefaults:\n  agent: acp-fixture\n')
    resources = tmp_path / "acp_agents"
    resources.mkdir()
    resource = _resource().model_copy(update={"args": (str(_PEER), "ui", peer_flag)})
    (resources / "fixture.yaml").write_text(resource.model_dump_json(exclude_none=True))
    settings = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"))
    async with open_harness_ui_app(settings, configuration_path=config) as app:
        from a13n_harness_ui.acp_sessions import AcpOptionPreviewChange, AcpOptionSelection

        catalog = await app.acp_agent_options("acp-fixture", None)
        assert [command.name for command in catalog.commands] == ["plan"]
        chosen = await app.acp_agent_options(
            "acp-fixture",
            None,
            AcpOptionPreviewChange(
                selection=AcpOptionSelection(scope=catalog.scope),
                change=AcpOptionChange(id="mode", value="plan"),
            ),
        )
        thread = await app.create_thread(acp_options=AcpOptionSelection(scope=chosen.scope, values=chosen.selections))
        assert (await app.acp_options(thread.thread_id))[0].value == "plan"
    async with open_harness_ui_app(settings, configuration_path=config) as app:
        assert (await app.acp_options(thread.thread_id))[0].value == "plan"
        assert (await app.get_thread(thread.thread_id)).continuation_id is None


async def test_acp_preview_retries_incomplete_catalog(tmp_path: Path) -> None:
    from a13n_harness_ui.app import open_harness_ui_app
    from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings

    gate = tmp_path / "model-ready"
    config = _ui_peer_config(tmp_path, f"model-gate={gate}")
    settings = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"))
    async with open_harness_ui_app(settings, configuration_path=config) as app:
        preview = await app.acp_agent_options("acp-fixture", None)
        assert [option.id for option in preview.options] == ["mode"]
        gate.touch()
        preview = await app.acp_agent_options("acp-fixture", None)
        assert [(option.id, option.value) for option in preview.options] == [("mode", "chat"), ("model", "")]
        gate.unlink()
        assert await app.acp_agent_options("acp-fixture", None) == preview


async def test_acp_preview_waits_for_delayed_model(tmp_path: Path) -> None:
    from a13n_harness_ui.app import open_harness_ui_app
    from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings

    config = _ui_peer_config(tmp_path, "delayed-model", "null-model")
    settings = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"))
    async with open_harness_ui_app(settings, configuration_path=config) as app:
        assert [(option.id, option.value) for option in (await app.acp_agent_options("acp-fixture", None)).options] == [
            ("mode", "chat"),
            ("model", ""),
        ]


@pytest.mark.parametrize("prompted", [False, True])
async def test_acp_prewarm_preserves_and_restores_a_model_advertised_late(tmp_path: Path, prompted: bool) -> None:
    from a13n_harness_ui.acp_sessions import AcpSessionState
    from a13n_harness_ui.app import open_harness_ui_app
    from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings

    gate = tmp_path / "model-ready"
    gate.touch()
    config = _ui_peer_config(tmp_path, f"model-gate={gate}", "late-model-on-mode-change")
    settings = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"))
    async with open_harness_ui_app(settings, configuration_path=config) as app:
        thread_id = (await app.create_thread()).thread_id
        assert (await app.acp_options(thread_id))[-1].value == ""
        with pytest.raises(HarnessUiError, match="did not offer"):
            await app.acp_options(thread_id, AcpOptionChange(id="model", value=""))
        assert (await app.acp_options(thread_id, AcpOptionChange(id="model", value="b")))[-1].value == "b"
        if prompted:
            receipt = await app.submit_thread(thread_id=thread_id, prompt="verify-restored-model")
            assert (await app.wait_root_operation(receipt.receipt_id)).status == "completed"
    gate.unlink()
    async with open_harness_ui_app(settings, configuration_path=config) as app:
        assert [option.id for option in await app.acp_options(thread_id)] == ["mode"]
        gate.touch()
        await app.acp_options(thread_id, AcpOptionChange(id="mode", value="plan"))
        stored = await app._store.threads.acp_state(thread_id)
        assert stored is not None
        saved = AcpSessionState.model_validate_json(stored)
        assert saved.options["model"] == "b"
        assert saved.explicit_options == (set() if prompted else {"model", "mode"})
        async with asyncio.timeout(5):
            while not any(option.is_model for option in await app.acp_options(thread_id)):
                await asyncio.sleep(0.01)
        receipt = await app.submit_thread(thread_id=thread_id, prompt="verify-restored-model")
        assert (await app.wait_root_operation(receipt.receipt_id)).status == "completed"
        assert (await app.get_thread(thread_id)).thread.excerpt.latest_reply == "Model B restored"


async def test_acp_does_not_persist_an_unconfirmed_model_change(tmp_path: Path) -> None:
    from a13n_harness_ui.acp_sessions import AcpSessionState
    from a13n_harness_ui.app import open_harness_ui_app
    from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings

    config = _ui_peer_config(tmp_path, "null-model", "unconfirmed-model-change")
    settings = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"))
    async with open_harness_ui_app(settings, configuration_path=config) as app:
        thread_id = (await app.create_thread()).thread_id
        await app.acp_options(thread_id, AcpOptionChange(id="model", value="a"))
        with pytest.raises(HarnessUiError, match="did not confirm"):
            await app.acp_options(thread_id, AcpOptionChange(id="model", value="b"))
        stored = await app._store.threads.acp_state(thread_id)
        assert stored is not None
        saved = AcpSessionState.model_validate_json(stored)
        assert saved.options["model"] == "a"
        assert saved.explicit_options == {"model"}


@pytest.mark.parametrize(
    ("message", "resumable"),
    [
        ("reject-prompt", False),
        ("fail-after-progress", True),
        ("fail-after-accept", True),
        ("fail-after-tool", True),
        ("disconnect-prompt", True),
    ],
)
async def test_acp_first_prompt_recovery_distinguishes_rejection_from_progress_or_unknown_outcome(
    tmp_path: Path, message: str, resumable: bool
) -> None:
    from a13n_harness_ui.acp_sessions import AcpSessionState
    from a13n_harness_ui.app import open_harness_ui_app
    from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings
    from a13n_harness_ui.surfaces import RootOperationStatus

    config = _ui_peer_config(tmp_path, "unique-session", "null-model")
    settings = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"))
    async with open_harness_ui_app(settings, configuration_path=config) as app:
        thread_id = (await app.create_thread()).thread_id
        changed = await app.acp_options(thread_id, AcpOptionChange(id="model", value="b"))
        assert changed[-1].value == "b"
        receipt = await app.submit_thread(thread_id=thread_id, prompt=message)
        assert (await app.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.failed
        stored = await app._store.threads.acp_state(thread_id)
        assert stored is not None
        failed = AcpSessionState.model_validate_json(stored)
        assert failed.prompted is resumable
        assert failed.explicit_options == (set() if resumable else {"model"})
        assert failed.options["model"] == "b"

        assert (await app.acp_options(thread_id))[-1].value == "b"
        stored = await app._store.threads.acp_state(thread_id)
        assert stored is not None
        recovered = AcpSessionState.model_validate_json(stored)
        assert (recovered.session_id == failed.session_id) is resumable
        receipt = await app.submit_thread(thread_id=thread_id, prompt="try again")
        assert (await app.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.completed


async def test_acp_shell_review_on_app_callbacks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    from a13n_harness.capabilities.tool_review import (
        AgentToolReviewer,
        ToolReviewAssessment,
        ToolReviewRequest,
        ToolReviewResult,
    )
    from a13n_harness_ui.app import open_harness_ui_app
    from a13n_harness_ui.model_runtime import HarnessUiModelResolver
    from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings
    from a13n_harness_ui.surfaces import RootOperationStatus
    from pydantic_ai.models.test import TestModel

    reviewed: list[ToolReviewRequest] = []

    async def review(self, request: ToolReviewRequest, **_: Any) -> ToolReviewResult:
        reviewed.append(request)
        risk = "high" if request.arguments["command"] == ["denied"] else "low"
        return ToolReviewResult(assessment=ToolReviewAssessment(risk=risk, reason="Fixture assessment"))

    async def resolve(self, model_id: str, **_: Any) -> TestModel:
        return TestModel()

    monkeypatch.setattr(AgentToolReviewer, "review", review)
    monkeypatch.setattr(HarnessUiModelResolver, "resolve", resolve)
    root = tmp_path / "a13n-harness-ui.yaml"
    root.write_text(
        'schema_version: "1"\ndefaults:\n  agent: acp-fixture\nsecurity:\n  shell_review:\n'
        "    enable: true\n    model: model-review\n    risk_threshold: high\n    on_flagged: deny\n"
    )
    directory = tmp_path / "acp_agents"
    directory.mkdir()
    resource = _resource().model_copy(update={"args": (str(_PEER), "ui")})
    (directory / "fixture.yaml").write_text(json.dumps(resource.model_dump(mode="json", exclude_none=True)))
    models = tmp_path / "models"
    models.mkdir()
    (models / "review.yaml").write_text(
        'schema_version: "1"\nkind: model\nid: model-review\nname: Review\nroute: openai:gpt-5\n'
        "authentication: {kind: api_key, env: FIXTURE_REVIEW_KEY}\n"
    )
    settings = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"))
    async with open_harness_ui_app(settings, configuration_path=root) as app:
        thread = await app.create_thread()
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="review callbacks")
        async with asyncio.timeout(15):
            outcome = await app.wait_root_operation(receipt.receipt_id)
        assert outcome.status is RootOperationStatus.completed, outcome.failure
        assert len(reviewed) == 3
        assert reviewed[0].tool_call_id == "review-execute"
        assert len({request.tool_call_id for request in reviewed}) == 3
        assert all(request.source is None for request in reviewed)
        assert reviewed[2].arguments["command"] == [sys.executable, "-c", "print('reviewed')"]


async def test_acp_receives_only_unsynced_visible_history(tmp_path: Path) -> None:
    from a13n_harness import HarnessState
    from pydantic_ai.messages import ModelRequest, ModelResponse, SystemPromptPart, TextPart, UserPromptPart

    sessions = AcpSessions()
    resource = _resource().model_copy(update={"args": (str(_PEER), "ui", "resident")})

    def executable():
        turn = AcpTurn(sessions, resource, "digest", AcpSessionSetup(cwd=str(tmp_path)), lambda _: None)
        return HarnessBuilder(configured_plugins_enabled=False).build(AgentSpec(), output_type=str, plugins=(turn,))

    original = HarnessState.new(
        message_history=(
            ModelRequest(
                parts=[SystemPromptPart(content="private system text"), UserPromptPart(content="native-visible")]
            ),
            ModelResponse(parts=[TextPart(content="native answer")]),
        )
    )
    try:
        outcome = await executable().run("handoff-check", previous_state=original)
        assert outcome.output == "verified handoff"
        assert outcome.state.message_history[-1].parts[0].content == "verified handoff"
        resumed = await executable().run("handoff-next", previous_state=outcome.state)
        assert resumed.output == "verified handoff"
    finally:
        await sessions.close()


@pytest.mark.skipif(os.name != "posix", reason="Antigravity browser handoff uses POSIX browser shims")
async def test_agy_oauth_callback_is_forwarded_and_released(tmp_path: Path) -> None:
    from a13n_harness_ui.acp_auth import _agy_browser_login
    from a13n_harness_ui.acp_inputs import AcpInputs

    executable = tmp_path / "agy"
    executable.write_text(
        '#!/bin/sh\n[ "$*" = "acp --login" ] || exit 1\n'
        'printf "%s\\n" "https://accounts.google.com/o/oauth2/auth?state=fixture"\n'
        'IFS= read -r code\n[ "$code" = synthetic-code ]\n'
    )
    executable.chmod(0o700)
    changed = asyncio.Event()

    async def notify(_: str) -> None:
        changed.set()

    inputs = Inputs(notify)
    scope = auth_input_scope("acp-fixture")
    interaction = AcpInputs(inputs, scope, "acp-fixture", lambda: None, run_id="login", timeout_seconds=10)
    transport = local_transport(_resource(), str(tmp_path))
    async with asyncio.TaskGroup() as group:
        login = group.create_task(_agy_browser_login(str(executable), transport, interaction))
        await asyncio.wait_for(changed.wait(), 10)
        (request,) = inputs.requests({scope})
        assert request.private_form and request.url and request.url.startswith("https://accounts.google.com/")
        await inputs.respond(
            {scope}, request.request_id, McpInputResponse(action="accept", content={"callback": "synthetic-code"})
        )
    assert login.result()
    assert not inputs.thread_ids()
    await inputs.close()


async def test_acp_thread_fork_branch_and_bidirectional_agent_switching(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    from a13n_harness_ui.app import open_harness_ui_app
    from a13n_harness_ui.model_runtime import HarnessUiModelResolver
    from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings
    from a13n_harness_ui.storage import ThreadConfigurationMutation, ThreadConfigurationPatch
    from a13n_harness_ui.storage.contracts import AcpAgentSource, AgentResourceSource
    from a13n_harness_ui.surfaces import BranchRequest, RootOperationStatus
    from a13n_harness_ui.thread_files import AttachmentUpload
    from pydantic_ai.messages import ModelMessagesTypeAdapter
    from pydantic_ai.models.function import FunctionModel

    from .test_app import _write_configuration

    native_seen: list[str] = []

    async def resolve_native(_self: HarnessUiModelResolver, _context: object, _model_id: str) -> FunctionModel:
        async def stream(messages: list[object], _info: object):
            native_seen.append(ModelMessagesTypeAdapter.dump_json(messages).decode())
            yield "native reply on fork"

        return FunctionModel(stream_function=stream)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve_native)

    root = _write_configuration(tmp_path)
    root.write_text('schema_version: "1"\ndefaults:\n  project: project-main\n  agent: acp-fixture\n')
    acp_dir = tmp_path / "acp_agents"
    acp_dir.mkdir()
    (acp_dir / "fixture.yaml").write_text(
        json.dumps(
            _resource()
            .model_copy(update={"args": (str(_PEER), "ui", "unique-session", "null-model")})
            .model_dump(mode="json", exclude_none=True)
        )
    )

    settings = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"), pricing_auto_update=False)
    async with open_harness_ui_app(settings, configuration_path=root, instrumentation=None) as app:
        source = await app.create_thread(title="ACP chat")
        changed = await app.acp_options(source.thread_id, AcpOptionChange(id="mode", value="plan"))
        assert changed[0].value == "plan"

        uploaded = await app.stage_thread_attachment(
            thread_id=source.thread_id,
            upload=AttachmentUpload("context.txt", b"turn-one-attachment", "text/plain"),
        )
        first = await app.submit_thread(
            thread_id=source.thread_id,
            prompt="echo-prior:turn-one",
            attachment_ids=(uploaded.attachment_id,),
        )
        assert (await app.wait_root_operation(first.receipt_id)).status is RootOperationStatus.completed
        source_reply_one = (await app.get_thread(source.thread_id)).thread.excerpt.latest_reply
        assert source_reply_one is not None and ";mode=plan;prior=none" in source_reply_one
        source_session = source_reply_one.split(";", 1)[0].removeprefix("session=")

        second = await app.submit_thread(thread_id=source.thread_id, prompt="echo-prior:turn-two")
        assert (await app.wait_root_operation(second.receipt_id)).status is RootOperationStatus.completed

        # 1. Full fork retains ACP options/commands, opens a distinct ACP session, and injects prior turns once.
        forked = await app.fork_thread(source.thread_id)
        assert forked.title == "ACP chat (2)"
        assert [cmd.name for cmd in await app.acp_commands(forked.thread_id)] == ["plan"]
        assert (await app.acp_options(forked.thread_id))[0].value == "plan"

        fork_run = await app.submit_thread(thread_id=forked.thread_id, prompt="echo-prior:fork-turn")
        assert (await app.wait_root_operation(fork_run.receipt_id)).status is RootOperationStatus.completed
        fork_reply = (await app.get_thread(forked.thread_id)).thread.excerpt.latest_reply
        assert fork_reply is not None
        fork_session = fork_reply.split(";", 1)[0].removeprefix("session=")
        assert fork_session != source_session
        assert ";mode=plan;" in fork_reply
        assert "echo-prior:turn-one" in fork_reply and "echo-prior:turn-two" in fork_reply

        fork_next = await app.submit_thread(thread_id=forked.thread_id, prompt="echo-prior:fork-next")
        assert (await app.wait_root_operation(fork_next.receipt_id)).status is RootOperationStatus.completed
        assert (
            f"session={fork_session};mode=plan;prior=none"
            == (await app.get_thread(forked.thread_id)).thread.excerpt.latest_reply
        )

        # Source Thread continues on its own original session without interference from the fork.
        source_next = await app.submit_thread(thread_id=source.thread_id, prompt="echo-prior:source-next")
        assert (await app.wait_root_operation(source_next.receipt_id)).status is RootOperationStatus.completed
        assert (
            f"session={source_session};mode=plan;prior=none"
            == (await app.get_thread(source.thread_id)).thread.excerpt.latest_reply
        )

        # 2. Message branching ("after" turn 1, and "before"/edit turn 2) truncates cleanly and retains attachments.
        transcript = await app.get_thread_transcript(thread_id=source.thread_id)
        points = await app.branch_points(source.thread_id)
        turn_one_id = transcript.turns[0].turn_id
        turn_two_id = transcript.turns[1].turn_id
        assert points.points[turn_one_id].branch and not points.points[turn_one_id].edit
        assert points.points[turn_two_id].branch and points.points[turn_two_id].edit

        branched = await app.branch_thread(
            source.thread_id,
            BranchRequest(turn_key=turn_one_id, expected_continuation_id=points.source, side="after"),
        )
        assert (await app._thread_files.read(branched.thread_id, uploaded.attachment_id))[1] == b"turn-one-attachment"
        branch_run = await app.submit_thread(thread_id=branched.thread_id, prompt="echo-prior:after-turn-one")
        assert (await app.wait_root_operation(branch_run.receipt_id)).status is RootOperationStatus.completed
        branch_reply = (await app.get_thread(branched.thread_id)).thread.excerpt.latest_reply
        assert branch_reply is not None
        assert ";mode=plan;" in branch_reply
        assert "echo-prior:turn-one" in branch_reply and "echo-prior:turn-two" not in branch_reply

        edited = await app.branch_thread(
            source.thread_id,
            BranchRequest(turn_key=turn_two_id, expected_continuation_id=points.source, side="before"),
        )
        edit_run = await app.submit_thread(thread_id=edited.thread_id, prompt="echo-prior:edited-turn-two")
        assert (await app.wait_root_operation(edit_run.receipt_id)).status is RootOperationStatus.completed
        edit_reply = (await app.get_thread(edited.thread_id)).thread.excerpt.latest_reply
        assert edit_reply is not None
        assert "echo-prior:turn-one" in edit_reply and "echo-prior:turn-two" not in edit_reply

        # 3. Bidirectional Agent switching across forks (ACP -> fork -> Native -> fork -> ACP).
        acp_to_native = await app.fork_thread(forked.thread_id)
        switched_native = await app.update_thread_configuration(
            thread_id=acp_to_native.thread_id,
            mutation=ThreadConfigurationMutation(
                expected_version=acp_to_native.configuration.version,
                patch=ThreadConfigurationPatch(
                    agent_source=AgentResourceSource(id="agent-assistant"),
                    default_model_id="model-primary",
                ),
            ),
        )
        assert switched_native.configuration.agent_source.kind == "agent"
        native_run = await app.submit_thread(thread_id=acp_to_native.thread_id, prompt="continue natively")
        assert (await app.wait_root_operation(native_run.receipt_id)).status is RootOperationStatus.completed
        assert "echo-prior:fork-turn" in native_seen[-1]

        native_to_acp = await app.fork_thread(acp_to_native.thread_id)
        switched_acp = await app.update_thread_configuration(
            thread_id=native_to_acp.thread_id,
            mutation=ThreadConfigurationMutation(
                expected_version=native_to_acp.configuration.version,
                patch=ThreadConfigurationPatch(agent_source=AcpAgentSource(id="acp-fixture")),
            ),
        )
        assert switched_acp.configuration.agent_source.kind == "acp_agent"
        back_to_acp = await app.submit_thread(thread_id=native_to_acp.thread_id, prompt="echo-prior:back-on-acp")
        assert (await app.wait_root_operation(back_to_acp.receipt_id)).status is RootOperationStatus.completed
        back_reply = (await app.get_thread(native_to_acp.thread_id)).thread.excerpt.latest_reply
        assert back_reply is not None and "native reply on fork" in back_reply


@pytest.mark.anyio
async def test_acp_presets_resolve_npx_adapters_and_options_cache_ttl_expires(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio
    import json

    from a13n_harness.acp import AcpSessionRuntime
    from a13n_harness_ui.acp_sessions import acp_presets, resolve_acp_argv
    from a13n_harness_ui.app import open_harness_ui_app
    from a13n_harness_ui.configuration.models import AcpAgentResource
    from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings
    from a13n_harness_ui.surfaces import RootOperationStatus
    from acp import schema

    # 1. Built-in presets cover mainstream ACP CLIs and fall back to npx for adapter packages.
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    for name in ("claude", "npx", "copilot", "goose", "kimi"):
        exe = fake_bin / name
        exe.write_text("#!/bin/sh\nexit 0\n")
        exe.chmod(0o755)
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("PATH", str(fake_bin))
        mp.setattr("a13n_harness_ui.acp_sessions.local_environment", lambda _env=None: {"PATH": str(fake_bin)})
        presets = {item.id: item for item in acp_presets()}
        assert {
            "antigravity",
            "claude_code",
            "codex",
            "gemini_cli",
            "github_copilot",
            "opencode",
            "goose",
            "kimi_cli",
        } <= presets.keys()
        assert presets["antigravity"].args == ("acp",)
        assert presets["claude_code"].detected_path == str(fake_bin / "npx")
        assert presets["claude_code"].executable == "npx"
        assert presets["claude_code"].args == ("-y", "@zed-industries/claude-agent-acp")
        assert presets["codex"].detected_path is None  # codex CLI itself is not installed in fake_bin
        assert presets["github_copilot"].detected_path == str(fake_bin / "copilot")
        assert resolve_acp_argv(
            AcpAgentResource(
                schema_version="1",
                kind="acp_agent",
                id="acp-claude",
                name="Claude Code",
                preset="claude_code",
                args=("--verbose",),
            ),
            path=str(fake_bin),
        ) == ("npx", "-y", "@zed-industries/claude-agent-acp", "--verbose")
        assert resolve_acp_argv(
            AcpAgentResource(schema_version="1", kind="acp_agent", id="acp-codex", name="Codex", preset="codex"),
            path=str(fake_bin),
        ) == ("codex-acp",)

    # 2. Options preview cache honors TTL expiry, and concurrent updates preserve arrival order across async persistence.
    root = tmp_path / "a13n-harness-ui.yaml"
    root.write_text('schema_version: "1"\ndefaults:\n  project: project-main\n  agent: acp-fixture\n')
    projects = tmp_path / "projects"
    projects.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (projects / "main.yaml").write_text(
        f'schema_version: "1"\nkind: project\nid: project-main\nname: Main\nroots:\n  - path: {workspace}\n'
    )
    acp_dir = tmp_path / "acp_agents"
    acp_dir.mkdir()
    (acp_dir / "fixture.yaml").write_text(
        json.dumps(
            _resource()
            .model_copy(update={"args": (str(_PEER), "ui", "unique-session", "null-model")})
            .model_dump(mode="json", exclude_none=True)
        )
    )
    settings = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"), pricing_auto_update=False)
    async with open_harness_ui_app(settings, configuration_path=root, instrumentation=None) as app:
        first_preview = await app.acp_agent_options("acp-fixture", "project-main")
        assert len(app._acp_options_cache) == 1
        scope = next(iter(app._acp_options_cache))
        app._acp_options_cache[scope] = (app._acp_options_cache[scope][0] - 120.0, first_preview)
        refreshed_preview = await app.acp_agent_options("acp-fixture", "project-main")
        assert refreshed_preview.options == first_preview.options

        ordered_thread = await app.create_thread()
        commands_entered = asyncio.Event()
        release_commands = asyncio.Event()
        original_save_acp = app._store.threads.save_acp_state

        async def slow_commands_save(tid: str, payload: str) -> None:
            if tid == ordered_thread.thread_id and "cmd-slow" in payload and not commands_entered.is_set():
                commands_entered.set()
                await release_commands.wait()
            await original_save_acp(tid, payload)

        async def two_chunk_prompt(self, prompt, *, binding, context=()):
            self.client.available_commands = [schema.AvailableCommand(name="cmd-slow", description="slow")]
            t0 = asyncio.create_task(
                binding.on_update(
                    schema.AvailableCommandsUpdate(
                        session_update="available_commands_update",
                        available_commands=self.client.available_commands,
                    )
                )
            )
            t1 = asyncio.create_task(
                binding.on_update(
                    schema.AgentMessageChunk(
                        session_update="agent_message_chunk", content=schema.TextContentBlock(text="A")
                    )
                )
            )
            await commands_entered.wait()
            t2 = asyncio.create_task(
                binding.on_update(
                    schema.AgentMessageChunk(
                        session_update="agent_message_chunk", content=schema.TextContentBlock(text="B")
                    )
                )
            )
            await asyncio.sleep(0)
            release_commands.set()
            await asyncio.gather(t0, t1, t2)
            return schema.PromptResponse(stop_reason="end_turn")

        monkeypatch.setattr(app._store.threads, "save_acp_state", slow_commands_save)
        monkeypatch.setattr(AcpSessionRuntime, "prompt", two_chunk_prompt)
        ordered = await app.submit_thread(thread_id=ordered_thread.thread_id, prompt="order check")
        assert (await app.wait_root_operation(ordered.receipt_id)).status is RootOperationStatus.completed
        assert (await app.get_thread(ordered_thread.thread_id)).thread.excerpt.latest_reply == "AB"


async def test_acp_compaction_negotiation_projection_goal_semantics_and_context_invalidation(tmp_path: Path) -> None:
    from a13n_harness_ui.app import open_harness_ui_app
    from a13n_harness_ui.display_projection import original_window_text
    from a13n_harness_ui.goal import GoalCapability, GoalView, saved_goal
    from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings
    from a13n_harness_ui.storage.display import read_display_windows
    from a13n_harness_ui.surfaces import RootOperationStatus

    # Non-completed compaction statuses must NOT trigger Goal restore audit.
    sessions = AcpSessions()
    try:
        turn = AcpTurn(
            sessions,
            _resource().model_copy(update={"args": (str(_PEER), "ui", "require-compaction-cap")}),
            "digest",
            AcpSessionSetup(cwd=str(tmp_path)),
            lambda _: None,
            goal=GoalCapability(GoalView(objective="Verify the work")),
        )
        executable = HarnessBuilder(configured_plugins_enabled=False).build(
            AgentSpec(), output_type=str | None, plugins=(turn,)
        )
        goal_result = await executable.run("goal-failed-compaction")
        assert goal_result.status == "completed"
        assert goal_result.output == "\n[GOAL_COMPLETE]"
        goal = saved_goal(goal_result.state)
        assert goal is not None and goal.status == "verified" and goal.iteration == 0
    finally:
        await sessions.close()

    # Full App flow: capability negotiation, transcript projection, live resident invalidation,
    # summary-less completed compaction, cancelled compaction, and persistence on failed turn.
    config = _ui_peer_config(tmp_path, "require-compaction-cap")
    settings = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"), pricing_auto_update=False)
    async with open_harness_ui_app(settings, configuration_path=config, instrumentation=None) as app:
        thread = await app.create_thread()

        # Seed prior occupancy first.
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="observe")
        assert (await app.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.completed
        usage = await app.context_usage(thread.thread_id)
        assert (usage.latest_request_tokens, usage.context_window) == (24, 1000)

        # Completed compaction without a newer usage_update invalidates stale occupancy and projects cleanly.
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="compact-live")
        assert (await app.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.completed
        usage = await app.context_usage(thread.thread_id)
        assert (usage.latest_request_tokens, usage.context_window) == (None, None)

        transcript = await app.get_thread_transcript(thread_id=thread.thread_id)
        assert transcript.turns[-1].output_preview == "Done after compact."
        compaction_entry, compaction_index, compaction_part = next(
            (entry, idx, part)
            for entry in transcript.entries
            for idx, part in enumerate(entry.parts)
            if (part.metadata.model_extra or {}).get("operation_id") == "c-1"
        )
        extra = compaction_part.metadata.model_extra or {}
        assert compaction_part.text == "Part 1. Part 2."
        assert extra["a13n.context"] == "compaction"
        assert extra["a13n.context.state"] == "completed"
        assert extra["a13n.acp.observation"] == "a13n.acp.compaction"
        # original_window_text must not expose compaction summary as an ordinary assistant reply.
        stored_thread = await app._store.threads.get(thread.thread_id)
        assert stored_thread is not None and stored_thread.continuation is not None
        continuation = await app._store.read_continuation(
            thread.thread_id, stored_thread.continuation, include_display=True
        )
        assert continuation.display is not None
        windows = await read_display_windows(app._store.objects, continuation.display)
        assert original_window_text(windows, compaction_entry.position, compaction_index) is None

        # Summary-less completed compaction followed by newer usage_update refreshes occupancy.
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="compact-then-usage")
        assert (await app.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.completed
        usage = await app.context_usage(thread.thread_id)
        assert (usage.latest_request_tokens, usage.context_window) == (150, 1000)
        transcript = await app.get_thread_transcript(thread_id=thread.thread_id)
        c2 = next(
            part
            for entry in transcript.entries
            for part in entry.parts
            if (part.metadata.model_extra or {}).get("operation_id") == "c-2"
        )
        assert c2.text == ""
        assert (c2.metadata.model_extra or {})["a13n.context.state"] == "completed"

        # Cancelled compaction preserves existing context occupancy and projects cancelled state.
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="compact-cancelled")
        assert (await app.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.completed
        usage = await app.context_usage(thread.thread_id)
        assert (usage.latest_request_tokens, usage.context_window) == (150, 1000)
        transcript = await app.get_thread_transcript(thread_id=thread.thread_id)
        c_cancel = next(
            part
            for entry in transcript.entries
            for part in entry.parts
            if (part.metadata.model_extra or {}).get("operation_id") == "c-cancel"
        )
        assert (c_cancel.metadata.model_extra or {})["a13n.context.state"] == "cancelled"

        # Failed turn retains latest observed occupancy in DB via shielded finally persistence.
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="usage-then-fail")
        assert (await app.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.failed
        # Session was retired on failure, so context_usage reads persisted DB state.
        usage = await app.context_usage(thread.thread_id)
        assert (usage.latest_request_tokens, usage.context_window) == (420, 1000)


@pytest.mark.parametrize("refresh_on_model_change", [False, True])
async def test_acp_prewarm_and_model_switch_context_window_authority(
    tmp_path: Path, refresh_on_model_change: bool
) -> None:
    from a13n_harness_ui.acp_sessions import AcpSessionState
    from a13n_harness_ui.app import open_harness_ui_app
    from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings

    flags = ("null-model", "usage-on-open") + (("usage-on-model-change",) if refresh_on_model_change else ())
    config = _ui_peer_config(tmp_path, *flags)
    settings = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"), pricing_auto_update=False)
    async with open_harness_ui_app(settings, configuration_path=config, instrumentation=None) as app:
        thread = await app.create_thread()
        await app.acp_options(thread.thread_id)
        assert (await app.get_thread(thread.thread_id)).continuation_id is None
        # Prewarmed ACP thread without continuation exposes live usage_update from open_session.
        usage = await app.context_usage(thread.thread_id)
        assert (usage.latest_request_tokens, usage.context_window) == (12, 1000)

        await app.acp_options(thread.thread_id, AcpOptionChange(id="model", value="a"))
        expected = (5, 2000) if refresh_on_model_change else (None, None)
        usage = await app.context_usage(thread.thread_id)
        assert (usage.latest_request_tokens, usage.context_window) == expected
        raw = await app._store.threads.acp_state(thread.thread_id)
        assert raw is not None
        assert AcpSessionState.model_validate_json(raw).context_window == (
            expected if refresh_on_model_change else None
        )


async def test_tui_backend_syncs_acp_context_window_on_run_and_resume(tmp_path: Path) -> None:
    from a13n_harness_ui.app import open_harness_ui_app
    from a13n_harness_ui.cli import CliRequest
    from a13n_harness_ui.interactive.backend import SessionBackend
    from a13n_harness_ui.interactive.rendering import Status, StreamRenderer
    from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings

    config = _ui_peer_config(tmp_path, "resident")
    settings = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"), pricing_auto_update=False)
    async with open_harness_ui_app(settings, configuration_path=config, instrumentation=None) as app:
        status = Status()
        backend = SessionBackend(app, CliRequest(command="chat"), tmp_path, status)
        assert await backend.initialize()
        await backend.execute(StreamRenderer(status), prompt="observe")
        assert (status.context_tokens, status.context_window) == (24, 1000)
        thread_id = backend.thread_id
        assert thread_id is not None

        resumed_status = Status()
        resumed = SessionBackend(app, CliRequest(command="chat", thread_id=thread_id), tmp_path, resumed_status)
        assert await resumed.initialize()
        assert (resumed_status.context_tokens, resumed_status.context_window) == (24, 1000)

        await resumed.execute(StreamRenderer(resumed_status), prompt="compact-live")
        assert (resumed_status.context_tokens, resumed_status.context_window) == (None, None)


def test_acp_workspace_agents_md_policy_by_preset(tmp_path: Path) -> None:
    from a13n_harness_ui.acp_execution import workspace_agents_instructions

    primary = tmp_path / "primary"
    extra = tmp_path / "extra"
    primary.mkdir()
    extra.mkdir()
    (primary / "AGENTS.md").write_text("Primary workspace rules.\n", encoding="utf-8")
    (extra / "AGENTS.md").write_text("Extra root rules.\n", encoding="utf-8")

    for native_preset in ("antigravity", "codex", "opencode", "github_copilot", "goose", "kimi_cli"):
        sections = workspace_agents_instructions(native_preset, str(primary), (str(extra),))
        assert not any("Primary workspace rules." in section for section in sections)
        assert sections == (f"Additional project root AGENTS.md files: {extra / 'AGENTS.md'}.",)

    for inject_preset in ("claude_code", "gemini_cli"):
        sections = workspace_agents_instructions(inject_preset, str(primary), (str(extra),))
        assert len(sections) == 2
        assert "Primary workspace rules." in sections[0]
        assert str(extra / "AGENTS.md") in sections[1]

    custom_sections = workspace_agents_instructions("custom", str(primary), (str(extra),))
    assert len(custom_sections) == 2
    assert str(primary / "AGENTS.md") in custom_sections[0]
    assert "Primary workspace rules." not in custom_sections[0]
    assert str(extra / "AGENTS.md") in custom_sections[1]

    # Oversized AGENTS.md is bounded at 32 KiB for injected presets.
    (primary / "AGENTS.md").write_text("x" * (40 * 1024), encoding="utf-8")
    bounded = workspace_agents_instructions("claude_code", str(primary), ())
    assert len(bounded) == 1
    assert "[Truncated at 32 KiB;" in bounded[0]
    assert len(bounded[0].encode("utf-8")) < 34 * 1024


async def _latest_reply_text(app: Any, thread_id: str) -> str:
    transcript = await app.get_thread_transcript(thread_id=thread_id)
    turn = transcript.turns[-1]
    assert turn.output_position is not None
    entry = next(item for item in transcript.entries if item.position == turn.output_position)
    return "".join(part.text or "" for part in entry.parts if part.kind == "assistant")


@pytest.mark.parametrize(
    "peer_mode, model_id",
    [
        ("standard", "fixture-model"),
        ("legacy", "fixture-model"),
        ("delayed", "fixture-model"),
        ("absent", None),
        ("unselected", None),
    ],
)
async def test_acp_projects_peer_model_on_first_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, peer_mode: str, model_id: str | None
) -> None:
    from a13n_harness_ui.app import open_harness_ui_app
    from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings
    from a13n_harness_ui.surfaces import RootOperationStatus

    waits: list[float] = []
    wait_for_model_option = AcpSessionRuntime.wait_for_model_option

    async def observe_model_wait(runtime: AcpSessionRuntime, timeout: float) -> None:
        waits.append(timeout)
        await wait_for_model_option(runtime, timeout)

    monkeypatch.setattr(AcpSessionRuntime, "wait_for_model_option", observe_model_wait)
    config = _ui_peer_config(tmp_path)
    peer = Path(__file__).with_name("fixtures") / "acp_model_peer.py"
    resource = _resource().model_copy(
        update={"args": (str(peer), peer_mode), "preset": "antigravity" if peer_mode == "delayed" else "custom"}
    )
    (tmp_path / "acp_agents/fixture.yaml").write_text(resource.model_dump_json(exclude_none=True))
    settings = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"), pricing_auto_update=False)
    async with open_harness_ui_app(settings, configuration_path=config) as app:
        thread = await app.create_thread()
        assert await app._store.threads.acp_state(thread.thread_id) is None
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="echo first prompt")
        outcome = await app.wait_root_operation(receipt.receipt_id)
        assert outcome.status is RootOperationStatus.completed, outcome.failure
        reply = await _latest_reply_text(app, thread.thread_id)
        model_lines = (
            f"Current Model: {model_id}\nUse the current Model name when attributing work to a model.\n"
            if model_id is not None
            else ""
        )
        assert (
            '<agent-context source="a13n-harness-ui">\n'
            f"Current Thread: {thread.thread_id}\n{model_lines}</agent-context>"
        ) in reply
        assert len(waits) == (1 if peer_mode == "delayed" else 0)


async def test_acp_prompt_context_deduplication_pruning_and_compaction_reset(tmp_path: Path) -> None:
    from a13n_harness_ui.acp_sessions import AcpSessionState
    from a13n_harness_ui.app import open_harness_ui_app
    from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings
    from a13n_harness_ui.surfaces import RootOperationStatus

    config = _ui_peer_config(tmp_path, "null-model")
    (tmp_path / "AGENTS.md").write_text("Global Harness UI guidance.\n", encoding="utf-8")
    config.write_text(
        'schema_version: "1"\ndefaults:\n  agent: acp-fixture\ntools:\n  enable_ask_user_question: false\n'
    )
    settings = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"), pricing_auto_update=False)
    async with open_harness_ui_app(settings, configuration_path=config, host_mode="webui") as app:
        thread = await app.create_thread()

        async def reply(prompt: str) -> str:
            receipt = await app.submit_thread(thread_id=thread.thread_id, prompt=prompt, input_surface="webui")
            outcome = await app.wait_root_operation(receipt.receipt_id)
            assert outcome.status is RootOperationStatus.completed, outcome.failure
            return await _latest_reply_text(app, thread.thread_id)

        async def echoed_prompt(prompt: str = "echo-prompt") -> str:
            blocks = json.loads(await reply(prompt))
            return "\n".join(item["text"] for item in blocks if item.get("type") == "text")

        await app.acp_options(thread.thread_id, AcpOptionChange(id="model", value="a"))
        first_text = await echoed_prompt()
        assert '<agent-context source="a13n-harness-ui">' in first_text
        assert "Current Model: a\nUse the current Model name when attributing work to a model.\n" in first_text

        unchanged_text = await echoed_prompt()
        assert "Current Environment mounts" not in unchanged_text
        assert "This input was submitted through Harness UI WebUI." not in unchanged_text
        assert "<agent-context" not in unchanged_text
        assert "<surface-context" not in unchanged_text
        assert "<inbox-reminder>" not in unchanged_text

        await app.acp_options(thread.thread_id, AcpOptionChange(id="model", value="b"))
        changed_text = await echoed_prompt()
        assert '<agent-context source="a13n-harness-ui">' in changed_text
        assert "Current Model: b\n" in changed_text
        assert "Current Model: a\n" not in changed_text
        assert "<surface-context" not in changed_text

        await echoed_prompt("compact-and-echo")
        stored = await app._store.threads.acp_state(thread.thread_id)
        assert stored is not None
        assert AcpSessionState.model_validate_json(stored).delivered_context == ()

        refreshed_text = await echoed_prompt()
        assert '<agent-context source="a13n-harness-ui">' in refreshed_text
        assert "Current Model: b\n" in refreshed_text
        assert "<surface-context" in refreshed_text
        assert "<inbox-reminder>" in refreshed_text
        assert "Current Environment mounts" not in refreshed_text
        assert "This input was submitted through Harness UI WebUI." not in refreshed_text

        system_prompt = json.loads(await reply("echo-instructions"))
        joined_instructions = "\n".join(block["text"] for block in system_prompt["content"])
        assert "Global Harness UI guidance." in joined_instructions
        assert "Use harness-host post_to_inbox to report results or blockers." in joined_instructions
        assert "Use harness-host ask_user_question" not in joined_instructions


async def test_acp_skills_discovery_index_direct_slash_and_host_paths(tmp_path: Path) -> None:
    from a13n_harness_ui.app import open_harness_ui_app
    from a13n_harness_ui.environment_paths import (
        BUILTIN_COORDINATOR_SKILLS_PATH,
        BUILTIN_COORDINATOR_SKILLS_ROOT,
        BUILTIN_SKILLS_PATH,
        BUILTIN_SKILLS_ROOT,
        acp_skill_host_path,
    )
    from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings
    from a13n_harness_ui.surfaces import NewThreadDefaults, RootOperationStatus, SkillReference

    assert acp_skill_host_path(f"{BUILTIN_COORDINATOR_SKILLS_PATH}/coordinate-worktree-pr") == str(
        (BUILTIN_COORDINATOR_SKILLS_ROOT.resolve() / "coordinate-worktree-pr").as_posix()
    )

    workspace = tmp_path / "workspace"
    project_skill = workspace / ".agents" / "skills" / "proj-workflow"
    project_skill.mkdir(parents=True)
    (project_skill / "SKILL.md").write_text(
        "---\nname: proj-workflow\ndescription: Project workflow guide.\n---\nFollow project steps.\n",
        encoding="utf-8",
    )
    user_skills = tmp_path / "user-skills"
    personal_skill = user_skills / "personal-note"
    personal_skill.mkdir(parents=True)
    (personal_skill / "SKILL.md").write_text(
        "---\nname: personal-note\ndescription: Personal note helper.\n---\nUse personal notes.\n",
        encoding="utf-8",
    )

    config = _ui_peer_config(tmp_path)
    config.write_text('schema_version: "1"\ndefaults:\n  project: project-main\n  agent: acp-fixture\n')
    projects = tmp_path / "projects"
    projects.mkdir()
    (projects / "main.yaml").write_text(
        json.dumps(
            {
                "schema_version": "1",
                "kind": "project",
                "id": "project-main",
                "name": "Main",
                "roots": [{"path": str(workspace)}],
            }
        )
    )
    settings = HarnessUiSettings(
        storage=StorageSettings(data_root=tmp_path / "data"),
        pricing_auto_update=False,
    )
    async with open_harness_ui_app(settings, configuration_path=config, host_mode="webui") as app:
        app._root_runs._executor._environments._user_skills_root = user_skills
        preview = await app.skill_catalog(defaults=NewThreadDefaults(project_id="project-main"))
        names = {item.name for item in preview.items}
        assert {"proj-workflow", "personal-note", "harness-ui-configuration", "harness-ui-operations"} <= names

        thread = await app.create_thread()
        idle_catalog = await app.skill_catalog(thread_id=thread.thread_id)
        assert {item.name for item in idle_catalog.items} == names

        # Session instructions include <available-skills source="harness-ui"> with real Host paths.
        first = await app.submit_thread(thread_id=thread.thread_id, prompt="echo-instructions")
        assert (await app.wait_root_operation(first.receipt_id)).status is RootOperationStatus.completed
        system_prompt = json.loads(await _latest_reply_text(app, thread.thread_id) or "{}")
        joined = "\n".join(block["text"] for block in system_prompt["content"])
        assert '<available-skills source="harness-ui">' in joined
        assert "proj-workflow" in joined
        assert "personal-note" in joined
        assert "harness-ui-configuration" in joined
        assert BUILTIN_SKILLS_PATH not in joined
        assert str((BUILTIN_SKILLS_ROOT.resolve() / "harness-ui-configuration" / "SKILL.md").as_posix()) in joined

        # Direct leading /skill-name submits resolve built-in skills to real Host paths for ACP.
        builtin_item = next(item for item in idle_catalog.items if item.name == "harness-ui-configuration")
        second = await app.submit_thread(
            thread_id=thread.thread_id,
            prompt="/harness-ui-configuration echo-prompt",
            skill_references=(
                SkillReference(
                    catalog_id=idle_catalog.catalog_id,
                    item_id=builtin_item.item_id,
                    name=builtin_item.name,
                ),
            ),
        )
        assert (await app.wait_root_operation(second.receipt_id)).status is RootOperationStatus.completed
        transcript = await app.get_thread_transcript(thread_id=thread.thread_id)
        user_entry = next(
            entry for entry in transcript.entries if entry.position == transcript.turns[-1].input_position
        )
        assert any(
            (part.metadata.model_extra or {}).get("harness_ui", {}).get("skills")
            == [
                {
                    "name": "harness-ui-configuration",
                    "source_id": builtin_item.source_id,
                    "start": 0,
                    "end": len("/harness-ui-configuration"),
                }
            ]
            for part in user_entry.parts
        )
        # Verify the peer received the real Host path in <skill-selection>, not /environment/builtin-skills.
        prompt_text = await app.submit_thread(
            thread_id=thread.thread_id,
            prompt="echo-prompt",
            skill_references=(
                SkillReference(
                    catalog_id=idle_catalog.catalog_id,
                    item_id=builtin_item.item_id,
                    name=builtin_item.name,
                ),
            ),
        )
        assert (await app.wait_root_operation(prompt_text.receipt_id)).status is RootOperationStatus.completed
        blocks = json.loads(await _latest_reply_text(app, thread.thread_id) or "[]")
        joined_prompt = "\n".join(item["text"] for item in blocks if item.get("type") == "text")
        assert '<skill-selection source="harness-ui">' in joined_prompt
        assert (
            str((BUILTIN_SKILLS_ROOT.resolve() / "harness-ui-configuration" / "SKILL.md").as_posix()) in joined_prompt
        )
        assert BUILTIN_SKILLS_PATH not in joined_prompt

        # Active ACP Run publishes its frozen skill catalog for steering validation.
        async with app.live_events(root_thread_id=thread.thread_id) as live:
            waiting = await app.submit_thread(thread_id=thread.thread_id, prompt="wait-steer")
            async with asyncio.timeout(15):
                while "waiting" not in str((await live.receive()).payload):
                    pass
            active_catalog = await app.skill_catalog(thread_id=thread.thread_id)
            assert active_catalog.context_kind == "active"
            proj_item = next(item for item in active_catalog.items if item.name == "proj-workflow")
            steered = await app.steer_root_operation(
                receipt_id=waiting.receipt_id,
                message="$proj-workflow apply",
                skill_references=(
                    SkillReference(
                        catalog_id=active_catalog.catalog_id,
                        item_id=proj_item.item_id,
                        name=proj_item.name,
                    ),
                ),
            )
            assert steered.accepted
            assert (await app.cancel_root_operation(waiting.receipt_id)).accepted
            await app.wait_root_operation(waiting.receipt_id)

        # Coordinator ACP Thread exposes Coordinator built-in Skills with real Host paths.
        coord_thread = await app.create_thread()
        await app.promote_coordinator(coord_thread.thread_id)
        coord_catalog = await app.skill_catalog(thread_id=coord_thread.thread_id)
        assert "coordinate-worktree-pr" in {item.name for item in coord_catalog.items}
        coord_first = await app.submit_thread(thread_id=coord_thread.thread_id, prompt="echo-instructions")
        assert (await app.wait_root_operation(coord_first.receipt_id)).status is RootOperationStatus.completed
        coord_system_prompt = json.loads(await _latest_reply_text(app, coord_thread.thread_id) or "{}")
        coord_joined = "\n".join(block["text"] for block in coord_system_prompt["content"])
        assert BUILTIN_COORDINATOR_SKILLS_PATH not in coord_joined
        assert (
            str((BUILTIN_COORDINATOR_SKILLS_ROOT.resolve() / "coordinate-worktree-pr" / "SKILL.md").as_posix())
            in coord_joined
        )


async def test_acp_attachments_embedded_context_image_fallback_and_multimodal_steering(tmp_path: Path) -> None:
    from io import BytesIO

    from a13n_harness_ui.app import open_harness_ui_app
    from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings
    from a13n_harness_ui.surfaces import RootOperationStatus
    from a13n_harness_ui.thread_files import AttachmentUpload
    from PIL import Image

    png_bytes = BytesIO()
    Image.new("RGB", (2, 2), "red").save(png_bytes, format="PNG")
    raw_png = png_bytes.getvalue()

    # 1. Peer with embeddedContext (and without promptCapabilities.image): small text embeds, retained image falls back to disk path.
    dir_embed = tmp_path / "embed"
    dir_embed.mkdir()
    config_embed = _ui_peer_config(dir_embed, "embedded-context")
    settings_embed = HarnessUiSettings(storage=StorageSettings(data_root=dir_embed / "data"), pricing_auto_update=False)
    async with open_harness_ui_app(settings_embed, configuration_path=config_embed, host_mode="webui") as app:
        thread = await app.create_thread()
        csv_att = await app.stage_thread_attachment(
            thread_id=thread.thread_id,
            upload=AttachmentUpload("metrics.csv", b"k,v\na,1\n", "text/csv"),
        )
        img_att = await app.stage_thread_attachment(
            thread_id=thread.thread_id,
            upload=AttachmentUpload("shot.png", raw_png, "image/png"),
        )
        assert csv_att.storage_name == "content.csv"
        assert img_att.storage_name == "content.png"
        receipt = await app.submit_thread(
            thread_id=thread.thread_id,
            prompt="echo-prompt",
            attachment_ids=(csv_att.attachment_id, img_att.attachment_id),
        )
        outcome = await app.wait_root_operation(receipt.receipt_id)
        assert outcome.status is RootOperationStatus.completed, outcome.failure
        blocks = json.loads(await _latest_reply_text(app, thread.thread_id) or "[]")
        embedded = [block for block in blocks if block.get("type") == "resource"]
        assert len(embedded) == 1
        assert embedded[0]["resource"]["text"] == "k,v\na,1\n"
        assert embedded[0]["resource"]["uri"].endswith("/content.csv")
        text_blocks = "\n".join(block["text"] for block in blocks if block.get("type") == "text")
        assert "on the thread-files Environment mount" not in text_blocks
        assert "content.png" in text_blocks
        assert "Inspect it from disk" in text_blocks

    # 2. Peer with promptCapabilities.image and _session/steering (without embeddedContext):
    #    non-image uses resource_link; steering delivers multimodal image block!
    dir_steer = tmp_path / "steer"
    dir_steer.mkdir()
    config_steer = _ui_peer_config(dir_steer, "prompt-image", "standard-steer")
    settings_steer = HarnessUiSettings(storage=StorageSettings(data_root=dir_steer / "data"), pricing_auto_update=False)
    async with open_harness_ui_app(settings_steer, configuration_path=config_steer, host_mode="webui") as app:
        thread = await app.create_thread()
        csv_att = await app.stage_thread_attachment(
            thread_id=thread.thread_id,
            upload=AttachmentUpload("notes.csv", b"x,y\n1,2\n", "text/csv"),
        )
        first = await app.submit_thread(
            thread_id=thread.thread_id, prompt="echo-prompt", attachment_ids=(csv_att.attachment_id,)
        )
        assert (await app.wait_root_operation(first.receipt_id)).status is RootOperationStatus.completed
        first_blocks = json.loads(await _latest_reply_text(app, thread.thread_id) or "[]")
        links = [block for block in first_blocks if block.get("type") == "resource_link"]
        assert len(links) == 1 and links[0]["uri"].endswith("/content.csv")

        img_att = await app.stage_thread_attachment(
            thread_id=thread.thread_id,
            upload=AttachmentUpload("steer.png", raw_png, "image/png"),
        )
        async with app.live_events(root_thread_id=thread.thread_id) as live:
            waiting = await app.submit_thread(thread_id=thread.thread_id, prompt="wait-steer")
            async with asyncio.timeout(15):
                while "waiting" not in str((await live.receive()).payload):
                    pass
            steered = await app.steer_root_operation(
                receipt_id=waiting.receipt_id,
                message="Look at this screenshot",
                attachment_ids=(img_att.attachment_id,),
            )
            assert steered.accepted
            outcome = await app.wait_root_operation(waiting.receipt_id)
            assert outcome.status is RootOperationStatus.completed, outcome.failure
        reply = await _latest_reply_text(app, thread.thread_id)
        steer_blocks = json.loads(reply.removeprefix("waiting"))
        assert any(block.get("type") == "image" and block.get("mimeType") == "image/png" for block in steer_blocks)


@pytest.mark.parametrize(
    "case",
    (
        "cancelled",
        "end_turn",
        "request_cancelled",
        "queued",
        "stop",
        "ignored",
        "prompt_required",
        "method_missing",
        "unknown",
        "injected",
        "late_injected",
        "followup_failed",
    ),
)
async def test_acp_active_steer_uses_one_resident_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    from a13n_harness_ui.app import open_harness_ui_app
    from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings
    from a13n_harness_ui.surfaces import RootOperationStatus
    from a13n_harness_ui.thread_files import ComposerInput

    journal = tmp_path / "wire.jsonl"
    gate = tmp_path / "settle"
    native = case in {"prompt_required", "method_missing", "unknown", "injected", "late_injected"}
    flags = [f"steer-journal={journal}", "steer-open-tool", "standard-steer" if native else "no-steering"]
    if case in {"queued", "unknown"}:
        flags.append(f"steer-gate={gate}")
    if case in {"end_turn", "request_cancelled"}:
        flags.append(f"steer-stop-reason={case}")
    if case in {"prompt_required", "method_missing", "unknown", "late_injected"}:
        flags.append("steer-" + case.replace("_", "-"))
    if case == "ignored":
        flags.append("ignore-steer-cancel")
        monkeypatch.setattr("a13n_harness_ui.acp_execution._STEER_CANCEL_GRACE_SECONDS", 0.1)
    config = _ui_peer_config(tmp_path, *flags)
    settings = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"), pricing_auto_update=False)
    source_id = "input_" + "1" * 32
    async with (
        asyncio.timeout(20),
        open_harness_ui_app(settings, configuration_path=config, host_mode="webui") as app,
    ):
        thread = await app.create_thread()
        async with app.live_events(root_thread_id=thread.thread_id) as live:
            receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="wait-steer")
            ready = waiting = False
            while not (ready and waiting):
                event = (await live.receive()).payload or {}
                ready |= event.get("name") == "a13n.harness_ui.root_ready"
                waiting |= event.get("delta") == "waiting"
            activity = (await app.get_thread(thread.thread_id)).thread.root_activity
            running = await app.get_root_operation(receipt.receipt_id)
            assert activity.available_actions == running.available_actions == ("wait", "steer", "cancel")
            result = await app.steer_root_operation(
                receipt_id=receipt.receipt_id,
                message=ComposerInput(
                    parts=("fail-steering-prompt" if case == "followup_failed" else "echo-prompt first",),
                    source_id=source_id,
                ),
                allow_acp_interrupt=case not in {"queued", "stop"},
            )
            assert result.accepted is (case != "unknown")
            if case == "queued":
                result = await app.steer_root_operation(
                    receipt_id=receipt.receipt_id,
                    message=(TextContent("wire-only hint", metadata={"display": False}), "second instruction"),
                    allow_acp_interrupt=False,
                )
                assert result.accepted
            if case in {"queued", "unknown"}:
                frames = [json.loads(line) for line in journal.read_text().splitlines()]
                assert all(frame["kind"] not in {"session/cancel", "response"} for frame in frames)
                gate.touch()
            if case == "stop":
                assert (await app.cancel_root_operation(receipt.receipt_id)).accepted
            outcome = await app.wait_root_operation(receipt.receipt_id)
            assert outcome.receipt.receipt_id == receipt.receipt_id
            frames = [json.loads(line) for line in journal.read_text().splitlines()]
            assert sum(frame["kind"] == "initialize" for frame in frames) == 1
            assert sum(frame["kind"] == "session/new" for frame in frames) == 1
            prompts = [frame for frame in frames if frame["kind"] == "session/prompt"]
            assert {frame["sessionId"] for frame in prompts} == {"fixture-session"}
            wire = [
                frame["kind"] for frame in frames if frame["kind"] in {"session/prompt", "session/cancel", "response"}
            ]
            if case == "ignored":
                assert outcome.status is RootOperationStatus.failed
                assert outcome.failure is not None and outcome.failure.code == "acp_steer_cancel_timeout"
                assert wire == ["session/prompt", "session/cancel"]
                return
            if case == "stop":
                assert outcome.status is RootOperationStatus.cancelled
                assert wire == ["session/prompt", "session/cancel", "response"]
                return
            if case == "followup_failed":
                assert outcome.status is RootOperationStatus.failed
                stored = await app._store.threads.get(thread.thread_id)
                assert stored is not None and stored.continuation is not None
                saved = await app._store.read_continuation(thread.thread_id, stored.continuation)
                messages = str(saved.harness_state.message_history)
                assert "wait-steer" in messages and "waiting" in messages and "fail-steering-prompt" in messages
                assert wire == ["session/prompt", "session/cancel", "response", "session/prompt"]
                return
            assert outcome.status is RootOperationStatus.completed, outcome.failure
            if case == "unknown":
                assert wire == ["session/prompt", "response"]
                return
            if case in {"injected", "late_injected"}:
                assert wire == ["session/prompt"]
            elif case == "queued":
                assert wire == ["session/prompt", "response", "session/prompt"]
                assert [block["text"].strip() for block in prompts[-1]["prompt"]][-3:] == [
                    "echo-prompt first",
                    "wire-only hint",
                    "second instruction",
                ]
            else:
                assert wire == ["session/prompt", "session/cancel", "response", "session/prompt"]
            display_events = []
            while True:
                event = (await live.receive()).payload or {}
                display_events.append(event)
                if event.get("name") == "a13n.harness_ui.root_settled":
                    break
            steering = next(item for item in display_events if item.get("name") == "a13n.input.steering")
            assert steering["metadata"]["source_id"] == source_id
            if case not in {"injected", "late_injected"}:
                names = [item.get("type") for item in display_events]
                assert names.index("TOOL_CALL_END") < display_events.index(steering) < names.index("TEXT_MESSAGE_START")
            transcript = await app.get_thread_transcript(thread_id=thread.thread_id)
            retained = [part for entry in transcript.entries for part in entry.parts]
            assert sum(part.metadata.source_id == source_id for part in retained) == 1
            if case == "queued":
                hidden = next(part for part in retained if part.text == "wire-only hint")
                assert hidden.kind == "user" and not hidden.metadata.display
            if case not in {"injected", "late_injected"}:
                text = [part.text for part in retained if part.text]
                assert text.index("waiting") < text.index("echo-prompt first")


async def test_acp_steering_during_goal_decision_supersedes_the_generated_prompt(tmp_path, monkeypatch):
    from a13n_harness_ui.app import open_harness_ui_app
    from a13n_harness_ui.goal import GoalCapability
    from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings
    from a13n_harness_ui.surfaces import RootOperationStatus

    entered, release = asyncio.Event(), asyncio.Event()
    after_reply = GoalCapability.after_reply

    async def pause_decision(self, ctx, output, continue_with):
        await after_reply(self, ctx, output, continue_with)
        if not entered.is_set():
            entered.set()
            await release.wait()

    monkeypatch.setattr(GoalCapability, "after_reply", pause_decision)
    journal = tmp_path / "wire.jsonl"
    config = _ui_peer_config(tmp_path, "no-steering", f"steer-journal={journal}")
    settings = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"), pricing_auto_update=False)
    async with asyncio.timeout(20), open_harness_ui_app(settings, configuration_path=config, host_mode="webui") as app:
        thread = await app.create_thread()
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="hello", mode="goal")
        await entered.wait()
        offer = await app.steer_root_operation(receipt_id=receipt.receipt_id, message="finish-steering-goal")
        assert offer.accepted
        release.set()
        outcome = await app.wait_root_operation(receipt.receipt_id)
        assert outcome.status is RootOperationStatus.completed, outcome.failure
        assert outcome.goal is not None and outcome.goal.status == "verified"
    frames = [json.loads(line) for line in journal.read_text().splitlines()]
    prompts = [frame for frame in frames if frame["kind"] == "session/prompt"]
    assert len(prompts) == 2 and "finish-steering-goal" in str(prompts[-1])
    assert not any(frame["kind"] == "session/cancel" for frame in frames)
