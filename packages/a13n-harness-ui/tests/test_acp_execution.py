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
from a13n_harness.acp import AcpSessionSetup
from a13n_harness.context import AgentContext
from a13n_harness_ui.acp_auth import AcpAuthRequest, acp_auth, auth_input_scope
from a13n_harness_ui.acp_execution import AcpTurn
from a13n_harness_ui.acp_sessions import AcpOptionChange, AcpSessions, local_transport
from a13n_harness_ui.configuration.models import AcpAgentResource
from a13n_harness_ui.errors import HarnessUiError
from a13n_harness_ui.mcp_runtime.inputs import Inputs, McpInputResponse
from pydantic_ai import RunContext
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.messages import BinaryContent
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
        assert len(second.all_messages()) == 4

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


async def test_acp_goal_audit_silent_reply_and_first_turn_stop(tmp_path: Path) -> None:
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

        result = await executable(turn(bot=True)).run("silence")
        assert result.status == "completed" and result.output is None

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
            # The fixture does not negotiate steering: never enter the native queue.
            assert not (await app.steer_root_operation(receipt_id=receipt.receipt_id, message="steer")).accepted
            assert (await app.cancel_root_operation(receipt.receipt_id)).accepted
        stopped = await app.wait_root_operation(receipt.receipt_id)
        assert stopped.status is RootOperationStatus.cancelled
        assert (await app.get_thread(stopped_thread.thread_id)).continuation_id is not None
        resume = await app.submit_thread(thread_id=stopped_thread.thread_id, prompt="again")
        assert (await app.wait_root_operation(resume.receipt_id)).status is RootOperationStatus.completed
        assert (await app.get_thread(stopped_thread.thread_id)).thread.excerpt.latest_reply == "done:again"


@pytest.mark.parametrize("input_tokens,output_tokens", [(8, 2), (13, 5)])
async def test_acp_usage_includes_cache_and_thinking_without_double_counting(input_tokens, output_tokens):
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
        SimpleNamespace(config_options=()),
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
    usage = ledger.finish.call_args.args[1].request_usage
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
            assert (
                await app.get_thread(thread.thread_id)
            ).thread.excerpt.latest_reply == "Files readSteering file read"
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
            .model_copy(update={"args": (str(_PEER), "ui", "unique-session")})
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
