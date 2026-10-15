import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
import yaml
from a13n_harness.models.codex_live import Delegation, Transcript
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.configuration import load_harness_ui_configuration
from a13n_harness_ui.configuration.models import HarnessUiDocument, LiveConfiguration
from a13n_harness_ui.errors import ConfigurationError, ThreadError
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from a13n_harness_ui.storage import ThreadConfigurationMutation, ThreadConfigurationPatch
from a13n_harness_ui.surfaces import RootOperationStatus
from anyio import CancelScope, Event, create_task_group, fail_after, sleep
from pydantic import ValidationError
from pydantic_ai.models.function import FunctionModel

from .test_app import _settings, _write_configuration


def live_configuration(tmp_path: Path, *, files_enabled: bool = True) -> Path:
    root = _write_configuration(tmp_path)
    value = yaml.safe_load(root.read_text())
    value["live"] = {"enabled": True, "model": "model-live", "files_enabled": files_enabled}
    value["memory"] = {"auto_organize": {"enabled": False}}
    root.write_text(yaml.safe_dump(value))
    (tmp_path / "models/live.yaml").write_text(
        "schema_version: '1'\nkind: model\nid: model-live\nname: Live executor\n"
        "route: openai-codex:gpt-5\nauthentication: {kind: codex_subscription}\n"
    )
    return root


def test_live_config_is_opt_in_and_reuses_model():
    assert HarnessUiDocument().live is None
    with pytest.raises(ValidationError, match="existing Codex"):
        LiveConfiguration(enabled=True)
    value = LiveConfiguration(enabled=True, model="model-example")
    assert value.voice == "cove" and value.files_enabled


@pytest.mark.anyio
@pytest.mark.parametrize("voice", ["marin", "cedar"])
async def test_live_legacy_voice_generations_remain_readable_but_cannot_connect(tmp_path, voice):
    root = live_configuration(tmp_path)
    value = yaml.safe_load(root.read_text())
    value["live"]["voice"] = voice
    root.write_text(yaml.safe_dump(value))
    settings = _settings(tmp_path / "data")
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
        source = await load_harness_ui_configuration(root)
        assert source.document.live.voice == voice
        with pytest.raises(ThreadError) as error:
            app._voice_model(source)
        assert error.value.code == "live_voice_incompatible"
    # Correct the source without rewriting the old immutable generation.
    value["live"]["voice"] = "cove"
    root.write_text(yaml.safe_dump(value))
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
        assert (await app.voice().status()).enabled


@pytest.mark.anyio
async def test_live_model_must_be_codex_subscription(tmp_path):
    root = live_configuration(tmp_path)
    value = yaml.safe_load(root.read_text())
    value["live"]["model"] = "model-primary"
    root.write_text(yaml.safe_dump(value))
    with pytest.raises(ConfigurationError) as error:
        await load_harness_ui_configuration(root)
    assert "codex_subscription" in str(error.value.__cause__)


@pytest.mark.anyio
@pytest.mark.parametrize("files_enabled", [True, False])
async def test_live_thread_uses_restricted_executor_and_retains_history(tmp_path, monkeypatch, files_enabled):
    root = live_configuration(tmp_path, files_enabled=files_enabled)
    observed = []

    async def model(messages, info):
        tools = {tool.name for tool in info.function_tools}
        assert {"note_write", "task_create", "send_thread_message", "list_projects"} <= tools
        assert not tools & {"shell_exec", "run_code", "delegate", "web_search"}
        assert ("view" in tools) is files_enabled
        observed.append(messages)
        yield "Completed the delegated task."

    async def resolve(self, context, model_id):
        assert self._recipes[model_id].model_id == "model-live"
        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        thread = await app._threads.live_thread(configuration_root=tmp_path)
        assert (await app._threads.live_thread(configuration_root=tmp_path)).thread_id == thread.thread_id
        assert thread.configuration.agent_source.kind == "live"
        assert thread.configuration.project_id is None
        assert thread.configuration.local_roots == (str(tmp_path / "live-workspace"),)
        for prompt in ("First task", "Second task"):
            receipt = await app.submit_thread(thread_id=thread.thread_id, prompt=prompt)
            operation = await app.wait_root_operation(receipt.receipt_id)
            assert operation.status is RootOperationStatus.completed, operation
        assert len(observed[-1]) > len(observed[0])
        inspection = await app.inspect_thread_configuration(thread.thread_id)
        assert inspection.next_model_id == "model-live"
        assert inspection.captured is not None
        with pytest.raises(ThreadError, match="Live settings"):
            await app._threads.update_configuration(
                thread_id=thread.thread_id,
                mutation=ThreadConfigurationMutation(
                    expected_version=1, patch=ThreadConfigurationPatch(project_id="project-main")
                ),
            )
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        restored = await app._threads.live_thread(configuration_root=tmp_path)
        assert restored.thread_id == thread.thread_id
        assert restored.continuation is not None


class FakeVoiceSession:
    answer_sdp = "v=0\r\nanswer"

    def __init__(self):
        self.incoming = asyncio.Queue()
        self.speech = []
        self.statuses = []
        self.closed = Event()

    async def events(self):
        while (event := await self.incoming.get()) is not None:
            yield event

    async def append_speech(self, text, **kwargs):
        self.speech.append(text)

    async def append_status(self, text, **kwargs):
        self.statuses.append((text, kwargs))


class FakeVoiceModel:
    def __init__(self):
        self.calls = []
        self.sessions = []

    @asynccontextmanager
    async def connect(self, offer, **kwargs):
        self.calls.append((offer, kwargs))
        session = FakeVoiceSession()
        self.sessions.append(session)
        try:
            await kwargs["on_answer"](session.answer_sdp)
            yield session
        finally:
            with CancelScope(shield=True):
                await sleep(0)
                session.closed.set()


async def next_voice(queue, status):
    with fail_after(5):
        while (snapshot := await queue.get()).status != status:
            pass
        return snapshot


@pytest.mark.anyio
async def test_voice_ownership_media_gate_receipts_and_transcript_restart(tmp_path):
    root = live_configuration(tmp_path)
    fake = FakeVoiceModel()
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        voice = app.voice()
        voice._model = lambda _: fake
        assert not fake.calls
        with pytest.raises(ThreadError, match="controls"):
            await voice.begin(owner_id="tab", start_id="one", offer_sdp="v=0")
        async with voice.subscribe("tab") as queue:
            with pytest.raises(ThreadError, match="Another browser"):
                async with voice.subscribe("other"):
                    pass
            first = await voice.begin(owner_id="tab", start_id="one", offer_sdp="v=0")
            await next_voice(queue, "connecting")
            assert (await voice.status()).answer_sdp is None
            assert (await voice.status(owner_id="tab")).answer_sdp == fake.sessions[0].answer_sdp
            assert (await voice.begin(owner_id="tab", start_id="one", offer_sdp="different")).start_id == first.start_id
            assert len(fake.calls) == 1
            assert (await voice.receive(source_thread_id="source", message="offline", kind="report"))[
                "mode"
            ] == "skipped"
            await voice.media_connected(owner_id="tab", start_id="one")
            await next_voice(queue, "active")
            result = await voice.receive(source_thread_id="source", message="Done", kind="report")
            assert result["mode"] == "voice_context"
            assert fake.sessions[0].speech == ["Report from Thread source:\nDone"]
            await fake.sessions[0].incoming.put(Transcript("user", "Remember this", True))
            with fail_after(5):
                while not (await voice.status()).transcript:
                    await queue.get()
            await voice.stop(owner_id="tab", start_id="one")
            assert fake.sessions[0].closed.is_set()
            assert (await voice.status()).status == "idle"
            await voice.begin(owner_id="tab", start_id="one", offer_sdp="v=0")
            assert len(fake.calls) == 1
            await voice.begin(owner_id="tab", start_id="two", offer_sdp="v=0")
            await next_voice(queue, "connecting")
            assert fake.calls[1][1]["initial_items"][0].text == "Remember this"
            assert (await voice.status()).thread_id == first.thread_id
        assert (await voice.receive(source_thread_id="source", message="offline", kind="task"))["mode"] == "skipped"
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        voice = app.voice()
        voice._model = lambda _: fake
        async with voice.subscribe("reloaded") as queue:
            result = await voice.begin(owner_id="reloaded", start_id="three", offer_sdp="v=0")
            await next_voice(queue, "connecting")
            assert result.thread_id == first.thread_id
            assert result.transcript[0].text == "Remember this"


@pytest.mark.anyio
async def test_voice_disconnect_cancellation_releases_owner_and_closes_provider(tmp_path):
    root = live_configuration(tmp_path)
    fake = FakeVoiceModel()
    ready = Event()
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        voice = app.voice()
        voice._model = lambda _: fake

        async def browser():
            async with voice.subscribe("tab") as queue:
                await voice.begin(owner_id="tab", start_id="one", offer_sdp="v=0")
                await next_voice(queue, "connecting")
                ready.set()
                await Event().wait()

        async with create_task_group() as group:
            group.start_soon(browser)
            await ready.wait()
            group.cancel_scope.cancel()
        assert fake.sessions[0].closed.is_set()
        async with voice.subscribe("replacement"):
            assert (await voice.status()).status == "idle"


@pytest.mark.anyio
async def test_voice_delegation_once_and_stop_does_not_cancel_executor(tmp_path, monkeypatch):
    root = live_configuration(tmp_path)
    fake = FakeVoiceModel()
    executing, release = Event(), Event()
    prompts = []

    async def model(messages, info):
        prompts.append(messages)
        executing.set()
        await release.wait()
        yield "Saved result"

    async def resolve(self, context, model_id):
        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        voice = app.voice()
        voice._model = lambda _: fake
        async with voice.subscribe("tab") as queue:
            started = await voice.begin(owner_id="tab", start_id="one", offer_sdp="v=0")
            await next_voice(queue, "connecting")
            await voice.media_connected(owner_id="tab", start_id="one")
            await next_voice(queue, "active")
            await fake.sessions[0].incoming.put(Delegation("task1", "Do work"))
            await fake.sessions[0].incoming.put(Delegation("task1", "Do work"))
            with fail_after(10):
                await executing.wait()
            operation = await app._root_runs.active(started.thread_id)
            assert operation is not None
            await voice.stop(owner_id="tab")
            assert (await app._root_runs.active(started.thread_id)) is not None
            release.set()
            result = await app.wait_root_operation(operation.receipt.receipt_id)
            assert result.status is RootOperationStatus.completed
            assert len(prompts) == 1
            assert not fake.sessions[0].speech


@pytest.mark.anyio
async def test_live_callbacks_allow_coordinator_reports_without_broadening_read_or_worker_access(tmp_path):
    from .test_coordinators import create_coordinator
    from .test_thread_collaboration import controller

    root = live_configuration(tmp_path)
    fake = FakeVoiceModel()
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        voice = app.voice()
        voice._model = lambda _: fake
        tools = controller(app)
        tools._voice = voice
        lead = await create_coordinator(app)
        worker = await app.create_thread(coordinator_thread_id=lead.thread_id)
        ordinary = await app.create_thread()
        assert (await tools.send_thread_message(source_thread_id=lead.thread_id, thread_id="live", message="Before"))[
            "mode"
        ] == "skipped"
        async with voice.subscribe("tab") as queue:
            started = await voice.begin(owner_id="tab", start_id="one", offer_sdp="v=0")
            await next_voice(queue, "connecting")
            await voice.media_connected(owner_id="tab", start_id="one")
            await next_voice(queue, "active")
            for target in ("live", started.thread_id):
                for source in (lead, ordinary):
                    result = await tools.send_thread_message(
                        source_thread_id=source.thread_id, thread_id=target, message="Done", kind="report"
                    )
                    assert result["mode"] == "voice_context"
                with pytest.raises(ThreadError, match="independent"):
                    await tools.send_thread_message(
                        source_thread_id=worker.thread_id, thread_id=target, message="Forbidden", kind="report"
                    )
            with pytest.raises(ThreadError, match="not accessible"):
                await tools._require_target(lead.thread_id, started.thread_id)
            assert await app._root_runs.latest(started.thread_id) is None
        assert len(fake.sessions[0].speech) == 4


@pytest.mark.anyio
async def test_voice_http_and_websocket_authentication_receipt_and_disconnect(tmp_path):
    import json

    import httpx
    from websockets.asyncio.client import connect
    from websockets.exceptions import ConnectionClosed, InvalidStatus

    from .test_interactive_protocol import frame_until, listener_with_app

    root = live_configuration(tmp_path)
    fake = FakeVoiceModel()
    async with listener_with_app(tmp_path, configuration_path=root) as (http, ws, app):
        app.voice()._model = lambda _: fake
        async with httpx.AsyncClient(base_url=http, trust_env=False) as client:
            assert (await client.get("/api/voice")).status_code == 401
            client.headers["Authorization"] = "Bearer test-only-key"
            assert (await client.get("/api/voice")).json()["enabled"]
            with pytest.raises(InvalidStatus):
                async with connect(ws + "/api/voice/connect", origin="http://attacker.invalid", proxy=None):
                    pass
            async with connect(ws + "/api/voice/connect", origin=http, proxy=None) as socket:
                await socket.send('{"api_key":"wrong"}')
                with pytest.raises(ConnectionClosed) as error:
                    await socket.recv()
                assert error.value.rcvd.code == 4401
            async with connect(ws + "/api/voice/connect", origin=http, proxy=None) as socket:
                await socket.send('{"api_key":"test-only-key"}')
                await socket.send('{"owner_id":"tab"}')
                await frame_until(socket, lambda frame: frame.get("status") == "idle")
                body = {"owner_id": "tab", "start_id": "one", "offer_sdp": "v=0"}
                response = await client.post("/api/voice/start", json=body)
                assert response.status_code == 202, response.text
                answer = await frame_until(socket, lambda frame: frame.get("status") == "connecting")
                assert answer["answer_sdp"].startswith("v=0")
                assert (await client.get("/api/voice")).json()["answer_sdp"] is None
                assert (await client.post("/api/voice/start", json=body)).status_code == 202
                assert len(fake.calls) == 1
                await socket.send(json.dumps({"kind": "media_connected", "start_id": "one"}))
                await frame_until(socket, lambda frame: frame.get("status") == "active")
                rejected = await client.post("/api/voice/stop", json={"owner_id": "other", "start_id": "one"})
                assert rejected.status_code == 400
            with fail_after(5):
                await fake.sessions[0].closed.wait()
            assert (await client.get("/api/voice")).json()["status"] == "idle"


@pytest.mark.anyio
async def test_cancelled_start_does_not_leave_unowned_call(tmp_path, monkeypatch):
    root = live_configuration(tmp_path)
    reached = Event()
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        voice = app.voice()

        async def blocked(*args, **kwargs):
            reached.set()
            await Event().wait()

        monkeypatch.setattr(app._store.voice, "recent", blocked)
        async with voice.subscribe("tab"):
            async with create_task_group() as group:

                async def start():
                    await voice.begin(owner_id="tab", start_id="one", offer_sdp="v=0")

                group.start_soon(start)
                await reached.wait()
                group.cancel_scope.cancel()
            with fail_after(1):
                await voice.stop()
            assert voice._call is None
        async with voice.subscribe("replacement"):
            pass


@pytest.mark.anyio
async def test_stop_during_capture_rejects_new_task_before_registration(tmp_path, monkeypatch):
    root = live_configuration(tmp_path)
    fake = FakeVoiceModel()
    captured, release = Event(), Event()
    results = []
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=root, host_mode="webui") as app:
        voice = app.voice()
        voice._model = lambda _: fake
        original = app._root_runs._executor.capture

        async def capture(*args, **kwargs):
            value = await original(*args, **kwargs)
            captured.set()
            await release.wait()
            return value

        monkeypatch.setattr(app._root_runs._executor, "capture", capture)
        async with voice.subscribe("tab") as queue:
            await voice.begin(owner_id="tab", start_id="one", offer_sdp="v=0")
            await next_voice(queue, "connecting")
            await voice.media_connected(owner_id="tab", start_id="one")
            await next_voice(queue, "active")

            async def receive():
                results.append(await voice.receive(source_thread_id="source", message="Do work", kind="task"))

            async with create_task_group() as group:
                group.start_soon(receive)
                with fail_after(5):
                    await captured.wait()
                await voice.stop()
                release.set()
            assert results == [{"ok": False, "mode": "skipped", "reason": "live_offline"}]
