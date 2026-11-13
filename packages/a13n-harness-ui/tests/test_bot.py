from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.bot_models import BOT_THREAD_ID, BotInput, BotSubmit
from a13n_harness_ui.errors import HarnessUiError
from anyio import fail_after, sleep

from .test_app import _CompletedReconstructor, _settings
from .test_app import _write_configuration as _base_configuration

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("migrated_databases")]


def _write_configuration(tmp_path: Path, **kwargs) -> Path:
    root = _base_configuration(tmp_path, **kwargs)
    root.write_text(root.read_text() + "webui:\n  bot:\n    enabled: true\n")
    return root


async def deliver_tick(bot, *, now):
    await bot.tick(now=now)
    await bot.deliver_automatic_inputs()
    inputs = await bot._store.bot.inputs()
    if inputs and inputs[-1].state == "admitted":
        await consumed_store(bot._store, inputs[-1].input_id)


async def consumed_store(store, input_id):
    with fail_after(10):
        while True:
            item = next((item for item in await store.bot.inputs() if item.input_id == input_id), None)
            if item is not None and item.state in {"completed", "interrupted"}:
                return item
            await sleep(0.01)


async def ready(app):
    with fail_after(5):
        while not app.bot()._owned:
            await sleep(0.01)
    return app.bot()


async def consumed(app, input_id: str):
    with fail_after(10):
        while True:
            items = await app._store.bot.inputs()
            item = next((item for item in items if item.input_id == input_id), None)
            if item is not None and item.state in {"completed", "interrupted"}:
                return item
            await sleep(0.01)


async def test_bot_is_lazy_singleton_and_direct_chat_is_retained(tmp_path: Path) -> None:
    root = _write_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        app._root_runs._executor._agents = _CompletedReconstructor()
        bot = await ready(app)
        assert (await app.list_threads()).total == 0
        assert not (await bot.snapshot()).state.activated
        command = BotSubmit(input_id="input-first", text="Track this objective")
        # Native admission completes before submit returns, independently of settlement.
        async with bot._settlement_lock:
            first = await bot.submit(command)
            assert first.state == "admitted"
            assert first.receipt_id is not None
            assert await bot.submit(command) == first
        item = await consumed(app, command.input_id)
        assert item.state == "completed", item.error
        assert await bot.submit(command) == item
        snapshot = await bot.snapshot()
        assert snapshot.state.activated
        assert [message.text for message in snapshot.messages] == ["Track this objective", "root complete"]
        assert (await app.list_threads()).total == 1
        thread = (await app.get_thread(BOT_THREAD_ID)).thread
        assert thread.configuration.project_id is None
        assert (await app._threads.get(BOT_THREAD_ID)).continuation is not None
        assert await app._threads.bot_thread() == await app._threads.get(BOT_THREAD_ID)
        with pytest.raises(HarnessUiError, match="different content"):
            await bot.submit(command.model_copy(update={"text": "different"}))
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        bot = await ready(app)
        assert len((await bot.snapshot()).messages) == 2
        assert (await app.list_threads()).total == 1
        assert (await app._store.bot.inputs())[0].state == "completed"


async def test_heartbeat_is_coalesced_text_is_visible_and_pause_still_allows_chat(tmp_path: Path) -> None:
    root = _write_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        app._root_runs._executor._agents = _CompletedReconstructor()
        bot = await ready(app)
        await bot.submit(BotSubmit(input_id="input-first", text="Watch"))
        await consumed(app, "input-first")
        completion = (await app._threads.get(BOT_THREAD_ID)).completion
        assert completion is not None
        future = datetime.now(UTC) + timedelta(days=10)
        await deliver_tick(bot, now=future)
        snapshot = await bot.snapshot()
        assert len(snapshot.inputs) == 2
        assert snapshot.inputs[-1].source == "automatic"
        assert snapshot.inputs[-1].state == "completed"
        assert len(snapshot.messages) == 3
        assert snapshot.messages[-1].text == "root complete"
        assert (await app._threads.get(BOT_THREAD_ID)).completion != completion
        await deliver_tick(bot, now=future)
        assert len((await bot.snapshot()).inputs) == 2
        await bot.pause(True)
        await bot.tick(now=future + timedelta(days=10))
        assert len((await bot.snapshot()).inputs) == 2
        await bot.submit(BotSubmit(input_id="input-paused", text="Still here"))
        assert (await consumed(app, "input-paused")).state == "completed"
        assert len((await bot.snapshot()).messages) == 5


@pytest.mark.parametrize("state", ["dispatching", "admitted"])
async def test_pending_limit_includes_active_inputs_and_allows_idempotent_retry(tmp_path: Path, state) -> None:
    root = _write_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        bot = await ready(app)
        async with bot._settlement_lock:
            repository = app._store.bot
            active = await repository.retain(
                BotInput(
                    input_id="active", source="user", text="In progress", created_at=datetime.now(UTC), state=state
                )
            )
            for index in range(15):
                await repository.retain(
                    BotInput(
                        input_id=f"queued-{index}", source="user", text=f"Message {index}", created_at=datetime.now(UTC)
                    )
                )
            overflow = BotInput(input_id="overflow", source="user", text="One more", created_at=datetime.now(UTC))
            with pytest.raises(HarnessUiError) as error:
                await repository.retain(overflow)
            assert error.value.code == "bot_input_full"
            assert await repository.retain(active) == active
            assert len(await repository.inputs(pending_only=True)) == 16
            await repository.save_input(active.model_copy(update={"state": "completed"}))
            assert await repository.retain(overflow) == overflow
            assert len(await repository.inputs(pending_only=True)) == 16


@pytest.mark.parametrize("retained_state", ["queued", "dispatching", "admitted"])
async def test_restart_never_replays_uncertain_input(tmp_path: Path, retained_state) -> None:
    root = _write_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        await ready(app)
        await app._threads.bot_thread()
        state = await app._store.bot.state()
        await app._store.bot.save_state(state.model_copy(update={"activated": True}))
        await app._store.bot.retain(
            BotInput(
                input_id="uncertain",
                source="user",
                text="Do once",
                created_at=datetime.now(UTC),
                state=retained_state,
                receipt_id="receipt-lost",
            )
        )
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        bot = await ready(app)
        item = await consumed(app, "uncertain")
        assert item.state == "interrupted"
        snapshot = await bot.snapshot()
        assert snapshot.state.paused and snapshot.state.last_error
        assert await app._root_runs.active(BOT_THREAD_ID) is None


async def test_bot_rejects_ordinary_nonhuman_wakes_and_terminal_has_no_bot(tmp_path: Path) -> None:
    root = _write_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        await ready(app)
        await app._threads.bot_thread()
        with pytest.raises(HarnessUiError) as error:
            await app._root_runs.submit_prompt(thread_id=BOT_THREAD_ID, prompt="Own completion")
        assert error.value.code == "bot_wake_rejected"
    async with open_harness_ui_app(_settings(tmp_path / "terminal"), configuration_path=root) as app:
        with pytest.raises(HarnessUiError) as error:
            app.bot()
        assert error.value.code == "bot_unavailable"


async def test_native_bot_observes_saved_sidekick_result_and_follows_up_on_heartbeat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    from a13n_harness_ui.model_runtime import HarnessUiModelResolver
    from pydantic_ai.messages import ToolReturnPart
    from pydantic_ai.models.function import DeltaToolCall, FunctionModel

    root = _write_configuration(tmp_path)
    target_id = ""
    bot_steps = 0
    working_runs = 0
    verified = False

    async def resolve(self, model_id, *, thread_id):

        async def model(messages, info):
            nonlocal target_id, bot_steps, working_runs, verified
            names = {tool.name for tool in info.function_tools}
            if thread_id != BOT_THREAD_ID:
                assert "publish_bot_message" not in names
                assert not any(tool.name == "finish_silently" for tool in info.output_tools)
                assert "kind='report'" in str(messages)
                assert "saved final response" in str(messages)
                working_runs += 1
                yield "analysis complete" if working_runs == 1 else "follow-up complete"
                return
            assert "publish_bot_message" in names and "resolve_bot_delivery" not in names
            assert "finish_silently" not in names
            output_tools = {tool.name: tool for tool in info.output_tools}
            assert output_tools["finish_silently"].parameters_json_schema == {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            }
            assert "explicit Thread messages can wake you" in str(messages) + (info.instructions or "")
            returns = [part for message in messages for part in message.parts if isinstance(part, ToolReturnPart)]
            if bot_steps == 1:
                created = next(part.content for part in returns if part.tool_name == "create_thread")
                assert created["ok"], created
                target_id = created["thread_id"]
            if bot_steps == 3:
                observed = returns[-1].content
                assert observed["ok"], observed
                assert "analysis complete" in str(observed)
                assert observed["thread"]["thread"]["thread_id"] == target_id
                verified = True
            plan = {
                0: ("create_thread", {"prompt": "Investigate the objective and save findings", "title": "Working"}),
                2: ("get_thread", {"thread_id": target_id}),
                3: ("send_thread_message", {"thread_id": target_id, "message": "Continue the verified result"}),
                4: ("publish_bot_message", {"text": "Verified the saved result and continued the working Thread."}),
            }
            call = plan.get(bot_steps)
            bot_steps += 1
            if call is None:
                if bot_steps == 2:
                    yield "I will check the working Thread."
                else:
                    yield {0: DeltaToolCall(name="finish_silently", json_args="{}", tool_call_id="quiet-heartbeat")}
            else:
                name, args = call
                yield {0: DeltaToolCall(name=name, json_args=json.dumps(args), tool_call_id=f"bot-step-{bot_steps}")}

        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "resolve", resolve)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        bot = await ready(app)
        await bot.submit(BotSubmit(input_id="objective", text="Investigate then continue the result when ready"))
        item = await consumed(app, "objective")
        assert item.state == "completed", await app._root_runs.latest(BOT_THREAD_ID)
        with fail_after(10):
            while await app._root_runs.active(target_id) is not None:
                await sleep(0.01)
        before = await bot.snapshot()
        assert len(before.inputs) == 1  # Sidekick completion did not wake Bot.
        await deliver_tick(bot, now=datetime.now(UTC) + timedelta(hours=1))
        after = await bot.snapshot()
        assert after.inputs[-1].state == "completed", after.inputs[-1].error
        assert verified
        assert [item.role for item in after.messages] == ["user", "assistant", "assistant"]
        assert after.messages[-1].text == "Verified the saved result and continued the working Thread."
        assert after.thread.completion != before.thread.completion
        assert bot_steps == 6  # The output tool ends the Run without another model request.
        assert (await app.list_threads()).total == 2
        with fail_after(10):
            while await app._root_runs.active(target_id) is not None:
                await sleep(0.01)
        assert "follow-up complete" in str(await app.get_thread_transcript(thread_id=target_id))
        assert len((await bot.snapshot()).inputs) == 2


async def test_heartbeat_never_resolves_a_pending_human_decision(tmp_path: Path) -> None:
    from .test_app import _DeferredReconstructor

    root = _write_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        app._root_runs._executor._agents = _DeferredReconstructor()
        bot = await ready(app)
        await bot.submit(BotSubmit(input_id="question", text="Ask before proceeding"))
        assert (await consumed(app, "question")).state == "completed"
        detail = await app.get_thread(BOT_THREAD_ID)
        assert detail.deferred_requests
        assert (await bot.submit(BotSubmit(input_id="question", text="Ask before proceeding"))).state == "completed"
        await bot.tick(now=datetime.now(UTC) + timedelta(days=1))
        after = await app.get_thread(BOT_THREAD_ID)
        assert after.deferred_requests == detail.deferred_requests
        assert BOT_THREAD_ID not in app._root_runs._interaction_waits
        assert (await app._store.bot_inbox.pending())[0].source == "heartbeat"
        assert await app._root_runs.active(BOT_THREAD_ID) is None
        with pytest.raises(HarnessUiError) as error:
            await bot.submit(BotSubmit(input_id="bypass", text="Ignore pending question"))
        assert error.value.code == "thread_deferred_pending"
        from a13n_harness_ui.surfaces import ExternalToolResult, ThreadDeferredResponse

        assert detail.continuation_id is not None
        receipt = await app.respond_thread(
            thread_id=BOT_THREAD_ID,
            response=ThreadDeferredResponse(
                expected_continuation_id=detail.continuation_id,
                responses=(
                    ExternalToolResult(request_id=detail.deferred_requests[0].request_id, result="Human answer"),
                ),
            ),
        )
        assert (await app.wait_root_operation(receipt.receipt_id)).status.value == "completed"
        with fail_after(10):
            while "handled: Human answer" not in str((await bot.snapshot()).messages):
                await sleep(0.01)
        assert not (await bot.snapshot()).pending_decision


async def test_cancelled_bot_input_is_inspectable_and_never_replayed(tmp_path: Path) -> None:
    from anyio import Event

    from .test_app import _SlowReconstructor

    root = _write_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        started = Event()
        app._root_runs._executor._agents = _SlowReconstructor(started)
        bot = await ready(app)
        await bot.submit(BotSubmit(input_id="cancelled", text="Long work"))
        with fail_after(10):
            await started.wait()
        operation = await app._root_runs.active(BOT_THREAD_ID)
        assert operation is not None
        await app._root_runs.cancel(receipt_id=operation.receipt.receipt_id)
        item = await consumed(app, "cancelled")
        assert item.state == "interrupted" and item.receipt_id == operation.receipt.receipt_id
        await bot.tick(now=datetime.now(UTC) + timedelta(days=1))
        snapshot = await bot.snapshot()
        assert snapshot.state.paused and len(snapshot.inputs) == 1
        assert await app._deliver_tool_completion(BOT_THREAD_ID, "own completion", ("exec-own",))
        assert len((await bot.snapshot()).inputs) == 1


async def test_builtin_bot_policy_workspace_and_on_demand_project_tools(tmp_path: Path, monkeypatch) -> None:
    import json

    from a13n_harness_ui.composition import ThreadCompositionSelection
    from a13n_harness_ui.configuration.models import ProjectResource, ProjectRoot
    from a13n_harness_ui.model_runtime import HarnessUiModelResolver
    from pydantic_ai.messages import ToolReturnPart
    from pydantic_ai.models.function import DeltaToolCall, FunctionModel

    root = _write_configuration(tmp_path, instructions="Agent-only policy sentinel")
    root.write_text(root.read_text() + "    model: model-primary\n    instructions: Custom Bot policy sentinel\n")
    (tmp_path / "AGENTS.md").write_text("Global Bot policy sentinel")
    for project_id, text in (("project-main", "Main memory sentinel"), ("project-other", "Other memory sentinel")):
        directory = tmp_path / "memory" / "projects" / project_id
        directory.mkdir(parents=True)
        (directory / "MEMORY.md").write_text(text)
    steps = 0
    created_thread = ""

    async def resolve(self, model_id, *, thread_id):
        async def model(messages, info):
            nonlocal steps, created_thread
            names = {tool.name for tool in info.function_tools}
            if thread_id != BOT_THREAD_ID:
                assert not {"create_project", "read_project_memory", "publish_bot_message"} & names
                assert "ask_user_question" in names
                yield "Working Thread result"
                return
            assert {
                "create_project",
                "create_thread",
                "read_project_memory",
                "offer_connector_setup",
            } <= names
            assert "ask_user_question" not in names
            if steps == 0:
                projected = str(messages) + str(info.instructions)
                assert "Main memory sentinel" not in projected and "Other memory sentinel" not in projected
                assert "Custom Bot policy sentinel" in projected
                assert "Global Bot policy sentinel" in projected
                assert "Agent-only policy sentinel" not in projected
                assert "Bot does not use ask_user_question" in projected
                assert "use offer_connector_setup" in projected
            if steps == 1:
                assert "Main memory sentinel" in str(messages)
            if steps == 2:
                assert "Other memory sentinel" in str(messages)
            plan = (
                ("read_project_memory", {"project_id": "project-main", "path": "MEMORY.md"}),
                ("read_project_memory", {"project_id": "project-other", "path": "MEMORY.md"}),
                (
                    "create_project",
                    {
                        "project": {
                            "schema_version": "1",
                            "kind": "project",
                            "id": "project-created",
                            "name": "Created",
                            "roots": [{"path": str(tmp_path / "created-work")}],
                        }
                    },
                ),
                ("create_thread", {"project_id": "project-created", "prompt": "Do this bounded work"}),
            )
            if steps == len(plan):
                returns = [part for message in messages for part in message.parts if isinstance(part, ToolReturnPart)]
                created_thread = returns[-1].content["thread_id"]
                yield "Project and working Thread created; work admission is not completion."
                return
            name, args = plan[steps]
            steps += 1
            yield {0: DeltaToolCall(name=name, json_args=json.dumps(args), tool_call_id=f"project-tool-{steps}")}

        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "resolve", resolve)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        project = ProjectResource(
            schema_version="1",
            kind="project",
            id="project-other",
            name="Other",
            roots=(ProjectRoot(path=str(tmp_path / "other")),),
        )
        assert await app.create_project(project=project) == project
        original = (tmp_path / "projects/project-other.yaml").read_text()
        with pytest.raises(HarnessUiError, match="already exists"):
            await app.create_project(project=project.model_copy(update={"name": "Replacement"}))
        assert (tmp_path / "projects/project-other.yaml").read_text() == original
        with pytest.raises(HarnessUiError):
            await app.read_project_memory(project_id="project-main", path="../a13n-harness-ui.yaml")
        with pytest.raises(HarnessUiError):
            await app.read_project_memory(project_id="project-missing")
        bot = await ready(app)
        assert (await bot.snapshot()).interval_seconds == 3600
        await bot.submit(
            BotSubmit(input_id="project-objective", text="Read project context and drive a working Thread")
        )
        item = await consumed(app, "project-objective")
        operation = await app._root_runs.latest(BOT_THREAD_ID)
        assert item.state == "completed", operation.failure if operation else item.error
        assert created_thread
        created = (await app.get_thread(created_thread)).thread
        assert created.configuration.project_id == "project-created"
        assert created.configuration.agent_source.kind == "agent"
        thread = await app._threads.get(BOT_THREAD_ID)
        assert thread.configuration.agent_source.kind == "bot"
        assert thread.configuration.local_roots == (str(tmp_path / "bot"),)
        source = await app.current_configuration()
        composition = app._configurations._resolver.resolve_run(source, ThreadCompositionSelection.from_thread(thread))
        assert composition.root.source_kind == "bot"
        assert composition.root.model.model_id == "model-primary"
        assert composition.root.instructions == ("Custom Bot policy sentinel",)
        assert len(composition.root.global_guidance) == 1
        assert composition.root.global_guidance[0].endswith("Global Bot policy sentinel")
        assert "Drive working Threads" in "\n".join(composition.root.system_prompt)
        assert "user_interaction" not in {item.capability for item in composition.root.capabilities}
        assert not composition.root.children


async def test_bot_reads_and_updates_heartbeat_checklist_with_guidance_and_custom_instructions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    from a13n_harness_ui.model_runtime import HarnessUiModelResolver
    from pydantic_ai.messages import ToolReturnPart
    from pydantic_ai.models.function import DeltaToolCall, FunctionModel

    root = _write_configuration(tmp_path, instructions="Agent-only policy sentinel")
    root.write_text(root.read_text() + "    instructions: Custom Bot policy sentinel\n")
    (tmp_path / "AGENTS.md").write_text("Global Bot policy sentinel")
    workspace = tmp_path / "bot"
    workspace.mkdir()
    guidance = workspace / "AGENTS.md"
    guidance.write_text("Workspace policy revision one")
    checklist = workspace / "HEARTBEAT.md"
    checklist.write_text("Original heartbeat check sentinel")
    steps = 0
    reads: list[str] = []

    async def resolve(self, model_id, *, thread_id):
        async def model(messages, info):
            nonlocal steps
            step = steps
            steps += 1
            projected = str(messages) + (info.instructions or "")
            if step == 0:
                tools = {tool.name for tool in info.function_tools}
                assert {"view", "write", "edit"} <= tools
                assert {"shell_exec", "shell_input", "list_environment_targets", "open_environment_target"} <= tools
                # Computer tools appear after opening a target with that ceiling.
                assert not {"computer_observe", "computer_input"} & tools
                assert "Manage recurring checks in HEARTBEAT.md" in projected
                assert "Custom Bot policy sentinel" in (info.instructions or "")
                assert "Global Bot policy sentinel" in projected
                assert "Workspace policy revision one" in projected
                assert "Agent-only policy sentinel" not in projected
                assert "Original heartbeat check sentinel" not in projected
                yield "Ready to check on heartbeat."
                return
            if step in {1, 4}:
                assert "Host heartbeat: read HEARTBEAT.md in your working directory" in str(messages)
                if step == 1:
                    assert "Original heartbeat check sentinel" not in projected
                else:
                    assert "Workspace policy revision two" in projected
                name, args = "view", {"file_path": "HEARTBEAT.md"}
            elif step in {2, 5}:
                returned = [part for message in messages for part in message.parts if isinstance(part, ToolReturnPart)]
                assert returned[-1].tool_name == "view"
                reads.append(str(returned[-1].content))
                expected = "Original heartbeat check sentinel" if step == 2 else "Bot-managed heartbeat check sentinel"
                assert expected in str(returned[-1].content)
                if step == 5:
                    yield {0: DeltaToolCall(name="finish_silently", json_args="{}", tool_call_id="quiet-next")}
                    return
                name, args = (
                    "write",
                    {
                        "file_path": "HEARTBEAT.md",
                        "content": "Bot-managed heartbeat check sentinel",
                    },
                )
            else:
                assert step == 3
                yield {0: DeltaToolCall(name="finish_silently", json_args="{}", tool_call_id="quiet-updated")}
                return
            yield {0: DeltaToolCall(name=name, json_args=json.dumps(args), tool_call_id=f"checklist-{step}")}

        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "resolve", resolve)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        bot = await ready(app)
        await bot.submit(BotSubmit(input_id="checklist", text="Manage recurring checks in your workspace."))
        assert (await consumed(app, "checklist")).state == "completed"
        assert checklist.read_text() == "Original heartbeat check sentinel"
        due = (await bot.snapshot()).state.next_heartbeat_at
        await deliver_tick(bot, now=due)
        assert (await bot.snapshot()).inputs[-1].state == "completed", reads
        assert checklist.read_text() == "Bot-managed heartbeat check sentinel"
        guidance.write_text("Workspace policy revision two")
        await deliver_tick(bot, now=due + timedelta(hours=1))
        assert (await bot.snapshot()).inputs[-1].state == "completed"
        assert steps == 6
        assert len((await bot.snapshot()).messages) == 2


async def test_native_finish_silently_completes_without_publication_and_resumes_next_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from a13n_harness_ui.model_runtime import HarnessUiModelResolver
    from pydantic_ai.messages import ToolCallPart, ToolReturnPart
    from pydantic_ai.models.function import DeltaToolCall, FunctionModel

    root = _write_configuration(tmp_path)
    requests = 0

    async def resolve(self, model_id, *, thread_id):
        async def model(messages, info):
            nonlocal requests
            requests += 1
            if requests == 1:
                assert "call finish_silently" in str(messages) + (info.instructions or "")
                yield {0: DeltaToolCall(name="finish_silently", json_args="{}", tool_call_id="quiet-direct")}
            elif requests == 2:
                assert "call finish_silently with no arguments" in str(messages)
                assert "Host heartbeat: read HEARTBEAT.md in your working directory if it exists" in str(messages)
                assert not (tmp_path / "bot/HEARTBEAT.md").exists()
                assert any(
                    isinstance(part, ToolReturnPart) and part.tool_call_id == "quiet-direct"
                    for message in messages
                    for part in message.parts
                )
                yield {0: DeltaToolCall(name="finish_silently", json_args="{}", tool_call_id="quiet-heartbeat")}
            else:
                calls = [part for message in messages for part in message.parts if isinstance(part, ToolCallPart)]
                assert [part.tool_name for part in calls].count("finish_silently") == 2
                yield "I am still following the objective."

        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "resolve", resolve)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        bot = await ready(app)
        completion = (await app._threads.bot_thread()).completion
        await bot.submit(BotSubmit(input_id="quiet", text="Keep watching; no reply is needed."))
        assert (await consumed(app, "quiet")).state == "completed"
        before = await bot.snapshot()
        assert [message.role for message in before.messages] == ["user"]
        assert (await app._root_runs.latest(BOT_THREAD_ID)).outcome.execution.output is None
        assert before.thread.completion == completion
        await deliver_tick(bot, now=datetime.now(UTC) + timedelta(hours=1))
        after = await bot.snapshot()
        assert after.inputs[-1].state == "completed"
        assert after.messages == before.messages
        assert after.thread.completion == completion
        assert requests == 2
        await bot.submit(BotSubmit(input_id="follow-up", text="What is the status?"))
        assert (await consumed(app, "follow-up")).state == "completed"
        assert requests == 3
        replied = await bot.snapshot()
        assert replied.messages[-1].text == "I am still following the objective."
        assert replied.thread.completion != completion


async def test_generic_human_steer_cannot_bypass_bot_input_policy(tmp_path: Path) -> None:
    from anyio import Event

    from .test_app import _SlowReconstructor
    from .test_bot_inbox import policy

    root = _write_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        bot = await ready(app)
        started = Event()
        app._root_runs._executor._agents = _SlowReconstructor(started)
        await bot.submit(BotSubmit(input_id="busy-steer", text="Work"))
        with fail_after(5):
            await started.wait()
        operation = await app._root_runs.active(BOT_THREAD_ID)
        assert operation is not None
        for enabled in (True, False):
            if not enabled:
                await policy(app, root, enabled=False)
            with pytest.raises(HarnessUiError) as error:
                await app.steer_root_operation(receipt_id=operation.receipt.receipt_id, message="Bypass")
            assert error.value.code == "bot_chat_required"
        assert len(await app._store.bot.inputs()) == 1
        await app.cancel_root_operation(operation.receipt.receipt_id)


async def test_unknown_decision_admission_is_interrupted_without_stranding_dispatch(
    tmp_path: Path, monkeypatch
) -> None:
    from a13n_harness_ui.surfaces import ExternalToolResult, ThreadDeferredResponse

    from .test_app import _DeferredReconstructor

    root = _write_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        bot = await ready(app)
        app._root_runs._executor._agents = _DeferredReconstructor()
        await bot.submit(BotSubmit(input_id="decision-fault", text="Ask before proceeding"))
        assert (await consumed(app, "decision-fault")).state == "completed"
        detail = await app.get_thread(BOT_THREAD_ID)
        response = ThreadDeferredResponse(
            expected_continuation_id=detail.continuation_id,
            responses=(ExternalToolResult(request_id=detail.deferred_requests[0].request_id, result="Human answer"),),
        )
        original = app._root_runs.submit_response

        async def fail(**kwargs):
            raise RuntimeError("Unexpected decision admission failure")

        monkeypatch.setattr(app._root_runs, "submit_response", fail)
        with pytest.raises(RuntimeError, match="Unexpected decision"):
            await bot.respond(response)
        failed = (await app._store.bot.inputs())[-1]
        assert failed.source_id == "decision" and failed.state == "interrupted"
        assert (await bot.snapshot()).state.paused
        assert all(item.state != "dispatching" for item in await app._store.bot.inputs())
        monkeypatch.setattr(app._root_runs, "submit_response", original)
        receipt = await bot.respond(response)
        assert (await app.wait_root_operation(receipt.receipt_id)).status.value == "completed"
        with fail_after(5):
            while any(item.state == "admitted" for item in await app._store.bot.inputs()):
                await sleep(0.01)


@pytest.mark.parametrize("automatic", [False, True])
@pytest.mark.parametrize("cancel", [False, True])
async def test_human_input_immediately_steers_and_settles_the_exact_receipt(
    tmp_path: Path, monkeypatch, automatic: bool, cancel: bool
) -> None:
    from a13n_harness_ui.model_runtime import HarnessUiModelResolver
    from anyio import Event, sleep_forever
    from pydantic_ai.models.function import FunctionModel

    started, release = Event(), Event()
    calls = 0

    async def resolve(self, model_id, *, thread_id):
        async def model(messages, info):
            nonlocal calls
            calls += 1
            if "answer-now" not in str(messages):
                started.set()
                await sleep_forever()
            await release.wait()
            yield "Human answer"

        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "resolve", resolve)
    root = _write_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        bot = await ready(app)
        if automatic:
            await bot.tick(now=datetime.now(UTC) + timedelta(days=1))
            await bot.deliver_automatic_inputs()
        else:
            await bot.submit(BotSubmit(input_id="initial", text="Keep working"))
        with fail_after(5):
            await started.wait()
        original = (await bot.snapshot()).inputs[0]
        command = BotSubmit(input_id="steering", text="answer-now")
        with fail_after(5):
            steered = await bot.submit(command)
            assert steered.state == "admitted"
            assert steered.receipt_id == original.receipt_id
            assert await bot.submit(command) == steered
            second = await bot.submit(BotSubmit(input_id="second-steering", text="Include details"))
            assert second.receipt_id == original.receipt_id
        if cancel:
            await app.cancel_root_operation(original.receipt_id)
        else:
            release.set()
        expected = "interrupted" if cancel else "completed"
        assert (await consumed(app, "steering")).state == expected
        assert (await consumed(app, "second-steering")).state == expected
        assert (await consumed(app, original.input_id)).state == expected
        snapshot = await bot.snapshot()
        replies = [message.text for message in snapshot.messages if message.role == "assistant"]
        assert replies == ([] if cancel else ["Human answer"])
        if not cancel:
            assert calls >= 2  # The first request never completes without immediate steering.
        assert not any(item.state in {"queued", "dispatching", "admitted"} for item in snapshot.inputs)


async def test_rejected_human_steering_never_queues_or_admits_a_replacement(tmp_path: Path, monkeypatch) -> None:
    from a13n_harness_ui.surfaces import RootControlResult
    from anyio import Event

    from .test_app import _SlowReconstructor

    root = _write_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        bot = await ready(app)
        started = Event()
        app._root_runs._executor._agents = _SlowReconstructor(started)
        original = await bot.submit(BotSubmit(input_id="initial", text="Keep working"))
        with fail_after(5):
            await started.wait()
        attempts = []

        async def reject(**kwargs):
            attempts.append(kwargs)
            return RootControlResult(receipt_id=original.receipt_id, accepted=False)

        monkeypatch.setattr(app._root_runs, "steer", reject)
        command = BotSubmit(input_id="rejected", text="Change direction")
        for _ in range(2):
            with pytest.raises(HarnessUiError) as error:
                await bot.submit(command)
            assert error.value.code == "bot_input_rejected"
        assert len(attempts) == 1
        assert attempts[0]["immediate"] is True
        assert attempts[0]["receipt_id"] == original.receipt_id
        await bot.tick(now=datetime.now(UTC) + timedelta(days=1))
        assert (await app._root_runs.active(BOT_THREAD_ID)).receipt.receipt_id == original.receipt_id
        assert (await bot.snapshot()).inputs[-1].state == "discarded"
        assert [message.text for message in (await bot.snapshot()).messages] == ["Keep working"]
        await app.cancel_root_operation(original.receipt_id)


async def test_obsolete_bot_timeout_is_uninterpreted_configuration_extra() -> None:
    from a13n_harness_ui.configuration.models import BotConfiguration

    assert "run_timeout_seconds" not in BotConfiguration.model_fields
    policy = BotConfiguration.model_validate({"run_timeout_seconds": 180})
    assert policy.__pydantic_extra__ == {"run_timeout_seconds": 180}
    assert policy.interval_seconds == 3600


@pytest.mark.parametrize("steering", [False, True])
async def test_uncertain_human_acceptance_is_retained_and_never_replayed(tmp_path: Path, monkeypatch, steering) -> None:
    from anyio import Event

    from .test_app import _SlowReconstructor

    root = _write_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        bot = await ready(app)
        started = Event()
        app._root_runs._executor._agents = _SlowReconstructor(started)
        if steering:
            await bot.submit(BotSubmit(input_id="original", text="Keep working"))
            with fail_after(5):
                await started.wait()
        method = "steer" if steering else "submit_prompt"
        original = getattr(app._root_runs, method)
        attempts = 0

        async def accept_then_lose_ack(**kwargs):
            nonlocal attempts
            attempts += 1
            await original(**kwargs)
            raise RuntimeError("Lost native acknowledgement")

        monkeypatch.setattr(app._root_runs, method, accept_then_lose_ack)
        command = BotSubmit(input_id="uncertain", text="Do this once")
        with pytest.raises(RuntimeError, match="Lost native acknowledgement"):
            await bot.submit(command)
        assert (await bot.submit(command)).state == "interrupted"
        assert attempts == 1
        assert (await bot.snapshot()).state.paused
        active = await app._root_runs.active(BOT_THREAD_ID)
        if active is not None:
            await app.cancel_root_operation(active.receipt.receipt_id)


async def test_early_automatic_publication_waits_for_receipt_correlation(tmp_path: Path, monkeypatch) -> None:
    from a13n_harness_ui.model_runtime import HarnessUiModelResolver
    from anyio import Event
    from pydantic_ai.messages import ToolReturnPart
    from pydantic_ai.models.function import DeltaToolCall, FunctionModel

    publishing = Event()

    async def resolve(self, model_id, *, thread_id):
        async def model(messages, info):
            published = any(
                isinstance(part, ToolReturnPart) and part.tool_name == "publish_bot_message"
                for message in messages
                for part in message.parts
            )
            if published:
                yield {0: DeltaToolCall(name="finish_silently", json_args="{}", tool_call_id="quiet")}
            else:
                publishing.set()
                yield {
                    0: DeltaToolCall(
                        name="publish_bot_message",
                        tool_call_id="publish",
                        json_args='{"text":"Verified progress"}',
                    )
                }

        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "resolve", resolve)
    root = _write_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        bot = await ready(app)
        original = app._root_runs.submit_prompt

        async def delay_native_return(**kwargs):
            receipt = await original(**kwargs)
            with fail_after(5):
                await publishing.wait()
                # The real publication tool has started before admission returns.
                while bot._lock.statistics().tasks_waiting == 0:
                    await sleep(0.01)
            assert not await app._store.bot.messages()
            return receipt

        monkeypatch.setattr(app._root_runs, "submit_prompt", delay_native_return)
        await bot.tick(now=datetime.now(UTC) + timedelta(days=1))
        await bot.deliver_automatic_inputs()
        item = (await bot.snapshot()).inputs[0]
        assert (await consumed(app, item.input_id)).state == "completed"
        snapshot = await bot.snapshot()
        assert len(snapshot.messages) == 1
        assert snapshot.messages[0].input_id == item.input_id
        assert snapshot.messages[0].text == "Verified progress"
        assert snapshot.thread.completion is not None


async def test_bot_publications_notify_once_per_message_not_per_run(tmp_path: Path) -> None:
    from a13n_harness_ui.connectors.models import SetupOffer

    root = _write_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        bot = await ready(app)
        await app._threads.bot_thread()
        notices = []
        bot._notify = lambda thread_id, notice: notices.append((thread_id, notice))
        offers = (
            SetupOffer(
                offer_id="setup-offer", thread_id=BOT_THREAD_ID, connector_key="github", created_at=datetime.now(UTC)
            ),
        )
        for _ in range(2):
            await bot.publish(
                message_id="one",
                text="First message",
                receipt_id="same-run",
                connector_setups=offers,
                display_ids=("display-first",),
            )
        await bot.publish(message_id="two", text="Second message", receipt_id="same-run")
        assert len(notices) == 2
        assert [notice.message_id for _, notice in notices] == ["one", "two"]
        assert [notice.receipt_id for _, notice in notices] == ["same-run", "same-run"]
        assert [notice.brief for _, notice in notices] == ["First message", "Second message"]
        assert all(thread_id == BOT_THREAD_ID for thread_id, _ in notices)
        first = (await bot.snapshot()).messages[0]
        assert first.connector_setups == offers
        assert first.display_ids == ("display-first",)


@pytest.mark.parametrize("busy", [False, True])
@pytest.mark.parametrize("attachment_only", [False, True])
@pytest.mark.parametrize("image", [False, True])
async def test_human_attachments_use_native_input_and_survive_exact_retry(
    tmp_path: Path, monkeypatch, busy: bool, attachment_only: bool, image: bool
) -> None:
    from io import BytesIO

    from a13n_harness.content import ContentItem
    from a13n_harness_ui.model_runtime import HarnessUiModelResolver
    from a13n_harness_ui.thread_files import AttachmentUpload, InputAttachmentReference
    from anyio import Event, sleep_forever
    from PIL import Image
    from pydantic_ai import BinaryContent
    from pydantic_ai.messages import TextContent
    from pydantic_ai.models.function import FunctionModel

    started, release = Event(), Event()
    image_file = BytesIO()
    Image.new("RGB", (2, 2), "white").save(image_file, format="PNG")
    data = image_file.getvalue() if image else b"hello"

    async def resolve(self, model_id, *, thread_id):
        async def model(messages, info):
            started.set()
            if busy:
                await sleep_forever()
            await release.wait()
            yield "Received"

        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "resolve", resolve)
    root = _write_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        bot = await ready(app)
        # Explicit attachment selection can prepare an unused Bot without a Run.
        attachment = await app.stage_thread_attachment(
            thread_id=BOT_THREAD_ID,
            upload=AttachmentUpload(
                "diagram.png" if image else "notes.txt", data, "image/png" if image else "text/plain"
            ),
        )
        assert await app._root_runs.active(BOT_THREAD_ID) is None
        if busy:
            await bot.submit(BotSubmit(input_id="initial", text="Keep working"))
            with fail_after(5):
                await started.wait()
        captured = []
        method = "steer" if busy else "submit_prompt"
        original = getattr(app._root_runs, method)

        async def observe(**kwargs):
            captured.append(kwargs["message" if busy else "prompt"])
            return await original(**kwargs)

        monkeypatch.setattr(app._root_runs, method, observe)
        reference = InputAttachmentReference(attachment_id=attachment.attachment_id)
        parts = (reference,) if attachment_only else ("Before ", reference, " after")
        command = BotSubmit(input_id="with-file", text="" if attachment_only else "Before  after", parts=parts)
        admitted = await bot.submit(command)
        assert admitted.state == "admitted"
        assert await bot.submit(command) == admitted
        assert len(captured) == 1
        visible = [part for part in captured[0] if isinstance(part, TextContent)]
        assert any("attachments/" + attachment.attachment_id in part.content for part in visible)
        media = [part for part in captured[0] if isinstance(part, ContentItem)]
        assert len(media) == int(image)
        if image:
            assert isinstance(media[0].value, BinaryContent)
            assert media[0].value.data == data
            assert media[0].value.media_type == "image/png"
        if not attachment_only:
            assert [part.content for part in visible][1::2] == ["Before ", " after"]
        message = next(message for message in (await bot.snapshot()).messages if message.input_id == "with-file")
        assert message.parts == ((attachment,) if attachment_only else ("Before ", attachment, " after"))
        assert (await app.read_thread_attachment(thread_id=BOT_THREAD_ID, attachment_id=attachment.attachment_id))[
            1
        ] == data
        other = await app.stage_thread_attachment(
            thread_id=BOT_THREAD_ID, upload=AttachmentUpload("other.txt", b"world", "text/plain")
        )
        with pytest.raises(HarnessUiError, match="different content"):
            await bot.submit(
                BotSubmit(input_id="with-file", parts=(InputAttachmentReference(attachment_id=other.attachment_id),))
            )
        if busy:
            await app.cancel_root_operation(admitted.receipt_id)
        else:
            release.set()
        await consumed(app, "with-file")
        assert await bot.submit(command)
        assert len(captured) == 1
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        message = next(
            message for message in (await (await ready(app)).snapshot()).messages if message.input_id == "with-file"
        )
        assert any(
            not isinstance(part, str) and part.attachment_id == attachment.attachment_id for part in message.parts
        )


async def test_user_publication_invalidates_after_commit_once(tmp_path: Path, monkeypatch) -> None:
    root = _write_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        bot = await ready(app)
        await app._threads.bot_thread()
        hints = []
        original = bot._summaries.publish

        async def observe(**kwargs):
            hints.append(kwargs)
            assert any(message.input_id == "published" for message in await app._store.bot.messages())
            await original(**kwargs)

        monkeypatch.setattr(bot._summaries, "publish", observe)
        item = BotInput(input_id="published", source="user", text="Hello", created_at=datetime.now(UTC))
        await app._store.bot.retain(item)
        await bot._publish_user(item)
        await bot._publish_user(item)
        assert hints == [{"kind": "thread", "root_thread_id": BOT_THREAD_ID, "thread_id": BOT_THREAD_ID}]


@pytest.mark.parametrize("silent", [False, True])
async def test_settlement_invalidates_after_all_correlated_inputs_complete(
    tmp_path: Path, monkeypatch, silent: bool
) -> None:
    from a13n_harness_ui.model_runtime import HarnessUiModelResolver
    from pydantic_ai.models.function import DeltaToolCall, FunctionModel

    async def resolve(self, model_id, *, thread_id):
        async def model(messages, info):
            if silent:
                yield {0: DeltaToolCall(name="finish_silently", tool_call_id="quiet", json_args="{}")}
            else:
                yield "Done"

        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "resolve", resolve)
    root = _write_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        bot = await ready(app)
        async with bot._settlement_lock:
            item = await bot.submit(BotSubmit(input_id="settle", text="Work"))
            await app.wait_root_operation(item.receipt_id)
            await app._store.bot.retain(item.model_copy(update={"input_id": "correlated"}))
            hints = []
            original = bot._summaries.publish

            async def observe(**kwargs):
                hints.append(kwargs)
                if kwargs["kind"] == "thread":
                    assert (await app._store.bot.get_input("settle")).state == "completed"
                    assert (await app._store.bot.get_input("correlated")).state == "completed"
                await original(**kwargs)

            monkeypatch.setattr(bot._summaries, "publish", observe)
            await bot._finish(item)
            assert hints[-1] == {"kind": "thread", "root_thread_id": BOT_THREAD_ID, "thread_id": BOT_THREAD_ID}
            assert sum(hint["kind"] == "thread" for hint in hints) == 1
