"""Exercise outbound ACP through the normal Harness middleware and state boundary."""

from __future__ import annotations

import asyncio
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


def test_native_launch_does_not_copy_host_credentials() -> None:
    transport = local_transport(
        _resource(), "/workspace", environ={"PATH": "/bin", "HOME": "/home/test", "CONNECTOR_TOKEN": "private"}
    )
    assert not transport.inherit_environment
    assert transport.environment == {"PATH": "/bin", "HOME": "/home/test"}


async def test_acp_auth_probe_and_browser_login(tmp_path: Path) -> None:
    resource = _resource().model_copy(update={"args": (str(_PEER), "auth")})
    changed = asyncio.Event()

    async def notify(_: str) -> None:
        changed.set()

    inputs = Inputs(notify)
    missing = resource.model_copy(update={"command": str(tmp_path / "missing")})
    assert (await acp_auth(missing, tmp_path, AcpAuthRequest(action="probe"), inputs)).state == "executable_missing"
    status = await acp_auth(resource, tmp_path, AcpAuthRequest(action="probe"), inputs)
    assert status.state == "available" and status.logout_supported
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


@pytest.mark.parametrize("flow", ["files", "inbox"])
async def test_acp_host_inputs_and_inbox_links(tmp_path: Path, flow: str) -> None:
    from a13n_harness_ui.app import open_harness_ui_app
    from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings
    from a13n_harness_ui.surfaces import RootOperationStatus
    from a13n_harness_ui.thread_files import AttachmentUpload

    root = tmp_path / "a13n-harness-ui.yaml"
    root.write_text('schema_version: "1"\ndefaults:\n  agent: acp-fixture\ninput:\n  long_text_threshold_chars: 512\n')
    directory = tmp_path / "acp_agents"
    directory.mkdir()
    resource = _resource().model_copy(update={"args": (str(_PEER), "ui", flow)})
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
            for prompt in ("inbox exact", "inbox fallback"):
                receipt = await app.submit_thread(thread_id=thread.thread_id, prompt=prompt)
                outcome = await app.wait_root_operation(receipt.receipt_id)
                assert outcome.status is RootOperationStatus.completed, outcome.failure
            options = await app.acp_options(thread.thread_id)
            assert [(item.id, item.value) for item in options] == [("mode", "chat")]
            changed = await app.acp_options(thread.thread_id, AcpOptionChange(id="mode", value="plan"))
            assert changed[0].value == "plan"
            history = await app.get_thread_transcript(thread_id=thread.thread_id)
            items = (await app.inbox().page(state="open", before=None, limit=10)).items
            assert len(items) == len(history.turns) == 2
            assert items[1].tool_call_id == "inbox-exact"
            assert items[0].tool_call_id == history.turns[1].turn_id
            for item, turn in zip(reversed(items), history.turns, strict=True):
                location = await app.inbox().location(item.item_id)
                assert (location.continuation_id, location.turn_id) == (history.continuation_id, turn.turn_id)


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
