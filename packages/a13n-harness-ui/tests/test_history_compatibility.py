"""Read old immutable histories through the real HTTP API without a write migration."""

from contextlib import asynccontextmanager

import httpx
import pytest
from a13n_harness.state import AgentContextStateSnapshot, CapabilityState, HarnessState
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.display_history import _message_digest, import_display_history, with_display_history
from a13n_harness_ui.storage import ObjectKind, StoredContinuation
from a13n_harness_ui.storage.read_models import project_continuation
from a13n_harness_ui.thread_projection import _transcript_turns
from a13n_harness_ui.webui import create_webui
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from .test_app import _CompletedReconstructor, _settings, _write_configuration

pytestmark = pytest.mark.anyio


def history_fixture():
    history = [ModelRequest(parts=[UserPromptPart("Repeated question")])]
    for index in range(55):
        history.extend(
            [
                ModelResponse(
                    parts=[
                        TextPart(f"Step {index}"),
                        ToolCallPart("view", {"index": index}, tool_call_id=f"call-{index}"),
                    ]
                ),
                ModelRequest(parts=[ToolReturnPart("view", f"Result {index}", tool_call_id=f"call-{index}")]),
            ]
        )
    history.append(
        ModelResponse(
            parts=[
                ThinkingPart("Reasoning"),
                TextPart("Full original 文本\n" * 6000),
                TextPart("Same"),
                TextPart("Same"),
            ]
        )
    )
    for index in range(104):
        history.extend(
            [
                ModelRequest(parts=[UserPromptPart("Repeated question")]),
                ModelResponse(parts=[TextPart(f"Answer {index}")]),
            ]
        )
    return history


async def seed_history(app, format):
    """Install a historical checkpoint; no execution or write is needed to inspect it."""
    app._root_runs._executor._agents = _CompletedReconstructor()
    created = await app.create_thread()
    receipt = await app.submit_thread(thread_id=created.thread_id, prompt="Seed composition")
    await app.wait_root_operation(receipt.receipt_id)
    thread = await app._store.threads.get(created.thread_id)
    stored = await app._store.objects.read_model(thread.continuation, StoredContinuation)
    history = history_fixture()
    native = HarnessState.new(thread_id=thread.thread_id, message_history=history[-2:] if format == "v1" else history)
    state = native
    if format in {"v1", "stale-v1"}:
        full = HarnessState.new(message_history=history)
        state = native.model_copy(
            update={
                "agent_context_state": AgentContextStateSnapshot(
                    entries={
                        "a13n.harness-ui.display-history": CapabilityState(
                            version="1",
                            data={
                                "messages": full.model_dump(mode="json")["message_history"],
                                "model_positions": list(
                                    range(len(history) - len(native.message_history), len(history))
                                ),
                                "pending_response_position": None,
                                "model_history_digest": _message_digest(native.message_history_json)
                                if format == "v1"
                                else "0" * 64,
                            },
                        ),
                    }
                )
            }
        )
    elif format == "v2":
        state = with_display_history(native, import_display_history(history))
    value = stored.model_copy(update={"harness_state": state})
    replacement = (await app._store.objects.publish_model(object_kind=ObjectKind.continuation, value=value)).ref
    selected = await app._store.threads.select_continuation(
        thread_id=thread.thread_id,
        expected=thread.continuation,
        replacement=replacement,
        read_model=project_continuation(value),
    )
    assert await app._store.publish_work(thread.thread_id, replacement, state)
    return selected, history


@pytest.mark.parametrize("format", ["native", "v1", "stale-v1", "v2"])
async def test_history_http_pages_and_originals_never_rewrite_selected_storage(tmp_path, monkeypatch, format):
    settings = _settings(tmp_path / "state")
    configuration = _write_configuration(tmp_path)
    async with open_harness_ui_app(settings, configuration_path=configuration) as app:
        selected, history = await seed_history(app, format)
        original = (await app._store.objects.read(selected.continuation)).model_dump_json()
    expected_turns = [turn.model_dump(mode="json") for turn in _transcript_turns(tuple(history))]

    base = f"/api/threads/{selected.thread_id}"
    # Cold index construction and a subsequent App restart use the same old source.
    for _ in range(2):

        @asynccontextmanager
        async def opened():
            async with open_harness_ui_app(settings, configuration_path=configuration) as app:
                with monkeypatch.context() as guard:

                    async def forbidden(*args, **kwargs):
                        pytest.fail("Reading history must not publish or select a new checkpoint")

                    guard.setattr(app._store.objects, "publish_model", forbidden)
                    guard.setattr(app._store.threads, "select_continuation", forbidden)
                    yield app
                current = await app._store.threads.get(selected.thread_id)
                assert current.continuation == selected.continuation
                assert current.initial_state == selected.initial_state
                assert current.completion == selected.completion
                assert (await app._store.objects.read(current.continuation)).model_dump_json() == original

        server = create_webui(opened, api_key=None)
        async with (
            server.router.lifespan_context(server),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=server), base_url="http://127.0.0.1") as client,
        ):
            source = selected.continuation.logical_digest

            async def get(path, **params):
                response = await client.get(base + path, params=params)
                assert response.status_code == 200, response.text
                return response.json()

            await get("")
            directory = await get("/inputs", limit=100)
            assert directory["turns"] == expected_turns[:100]
            rest = await get("/inputs", cursor=directory["next_cursor"], limit=100)
            assert rest["turns"] == expected_turns[100:] and rest["next_cursor"] is None
            assert len({turn["turn_id"] for turn in expected_turns}) == 105

            page = await get("/transcript", limit=37)
            assert page["total"] == len(history)
            positions = [entry["position"] for entry in page["entries"]]
            while page["next_cursor"]:
                page = await get("/transcript", cursor=page["next_cursor"], limit=37)
                positions = [entry["position"] for entry in page["entries"]] + positions
            assert positions == list(range(len(history)))
            forward = [entry["position"] for entry in page["entries"]]
            while page["newer_cursor"]:
                page = await get("/transcript", cursor=page["newer_cursor"], limit=37)
                forward += [entry["position"] for entry in page["entries"]]
            assert forward == positions

            middle = await get("/transcript", turn_id=expected_turns[1]["turn_id"], limit=1)
            assert middle["turns"] == expected_turns[1:2]
            previous = await get("/transcript", cursor=middle["earlier_turns_cursor"], limit=1)
            later = await get("/transcript", cursor=middle["later_turns_cursor"], limit=1)
            assert previous["turns"] == expected_turns[:1]
            assert later["turns"] == expected_turns[2:3]
            entry = previous["entries"][0]
            assert entry["position"] == 111 and entry["parts"][1]["text_truncated"]
            assert previous["boundary_entries"][0]["position"] == 0
            text, offset = "", 0
            target = {
                "producing_thread_id": selected.thread_id,
                "source_id": source,
                "location": {"kind": "root_text", "message": entry["position"], "part": 1},
            }
            assert target["source_id"] == source and target["location"] == {
                "kind": "root_text",
                "message": 111,
                "part": 1,
            }
            while offset is not None:
                response = await client.post(
                    base + "/saved-output", params={"offset": offset, "limit": 32768}, json=target
                )
                assert response.status_code == 200, response.text
                body = response.json()
                assert body["offset"] == offset
                text += body["text"]
                offset = body["next_offset"]
            assert text == history[111].parts[1].content
            for part in (2, 3):
                response = await client.post(
                    base + "/saved-output", json={**target, "location": {**target["location"], "part": part}}
                )
                assert response.json()["text"] == "Same"

            for path, params, status, code in (
                ("/transcript", {"cursor": "broken"}, 400, "thread_history_cursor_invalid"),
                ("/inputs", {"cursor": "broken"}, 400, "thread_history_cursor_invalid"),
                ("/transcript", {"expected_continuation_id": "wrong"}, 400, "thread_history_continuation_changed"),
                ("/inputs", {"expected_continuation_id": "wrong"}, 400, "thread_history_continuation_changed"),
                ("/transcript", {"turn_id": "missing"}, 400, "thread_history_input_missing"),
            ):
                response = await client.get(base + path, params=params)
                assert response.status_code == status and response.json()["error"]["code"] == code, response.text
