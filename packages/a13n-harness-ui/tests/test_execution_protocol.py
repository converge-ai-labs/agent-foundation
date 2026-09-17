"""Public-boundary execution, child and decision lifecycle without provider I/O."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import yaml
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from anyio import Event, fail_after
from pydantic_ai.messages import ModelRequest, ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from websockets.asyncio.client import connect

from .test_app import _write_configuration
from .test_comment_protocol import publication
from .test_configuration_protocol import HEADERS, settled
from .test_interactive_protocol import frame_until, listener

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    "child_output",
    [
        "Child completed.",
        "Detailed finding.\n\n" * 600 + "End of review.",
        "Long finding.\n\n" * 6000 + "Final retained paragraph.",
    ],
)
async def test_child_question_competing_response_history_and_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, child_output: str
) -> None:
    root = _write_configuration(tmp_path)
    agent_path = tmp_path / "agents/assistant.yaml"
    parent = yaml.safe_load(agent_path.read_text())
    parent["subagents"] = [{"agent": "agent-worker"}]
    agent_path.write_text(yaml.safe_dump(parent))
    (tmp_path / "agents/worker.yaml").write_text(
        yaml.safe_dump(
            {"schema_version": "1", "kind": "agent", "id": "agent-worker", "name": "Worker", "model": "model-primary"}
        )
    )
    resumed_started, release = Event(), Event()

    async def stream(messages, info):
        if "delegate" not in {tool.name for tool in info.function_tools}:
            yield child_output
            return
        returned = {
            part.tool_name
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        }
        if "delegate" not in returned:
            name, args = "delegate", {"subagent_name": "agent-worker", "prompt": "Bounded task"}
        elif "wait_subagent" not in returned:
            name, args = "wait_subagent", {"timeout_seconds": 10}
        elif "ask_user_question" not in returned:
            name, args = (
                "ask_user_question",
                {
                    "questions": [
                        {
                            "header": "Option",
                            "question": "Which option?",
                            "options": [
                                {"label": "One", "description": "First"},
                                {"label": "Two", "description": "Second"},
                            ],
                        }
                    ]
                },
            )
        else:
            resumed_started.set()
            await release.wait()
            yield "Root completed after one answer."
            return
        yield {0: DeltaToolCall(name=name, json_args=json.dumps(args), tool_call_id=f"call-{name}")}

    async def resolve(self, context, model_id):
        return FunctionModel(stream_function=stream)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    async with listener(tmp_path, configuration_path=root) as (http, ws):
        async with httpx.AsyncClient(base_url=http, headers=HEADERS, trust_env=False) as api:
            thread_id = (await api.post("/api/threads", json={})).json()["thread_id"]
            prefix = f"/api/threads/{thread_id}"
            first = (await api.post(prefix + "/submit", json={"prompt": "Delegate then ask"})).json()["receipt_id"]
            assert (await settled(api, first))["status"] == "suspended"
            children = (await api.get(prefix + "/children")).json()
            assert children["total"] == 1
            child = children["executions"][0]
            assert child["persisted_status"] == "succeeded"
            assert child["parent_thread_id"] == thread_id and child["root_thread_id"] == thread_id
            # A child is inspectable, but cannot create an independent shared root draft.
            async with connect(
                ws + f"/api/threads/{child['child_thread_id']}/draft/connect", proxy=None, origin=http
            ) as connection:
                await connection.send('{"api_key":"test-only-key"}')
                failure = await frame_until(connection, lambda f: "error" in f)
                assert failure["error"]["code"] == "child_thread_scoped"
            decisions = (await api.get(prefix + "/decisions")).json()
            assert len(decisions["requests"]) == 1 and decisions["requests"][0]["kind"] == "question"
            body = {
                "expected_continuation_id": decisions["continuation_id"],
                "responses": [
                    {
                        "kind": "question",
                        "request_id": decisions["requests"][0]["request_id"],
                        "answers": {"Which option?": "One"},
                    }
                ],
            }
            incomplete = await api.post(prefix + "/decisions", json={**body, "responses": []})
            assert incomplete.status_code == 400 and incomplete.json()["error"]["code"] == "request_invalid"
            resumed = await api.post(prefix + "/decisions", json=body)
            assert resumed.status_code == 200, resumed.text
            second = resumed.json()["receipt_id"]
            with fail_after(10):
                await resumed_started.wait()
            # Closing an observation must not cancel or restart the root operation.
            async with connect(ws + "/api/realtime/connect", proxy=None, origin=http) as observation:
                await observation.send('{"api_key":"test-only-key"}')
                await observation.send(
                    json.dumps(
                        {
                            "version": 1,
                            "kind": "subscribe",
                            "channel": "focus",
                            "stream": "focus",
                            "root_thread_id": thread_id,
                        }
                    )
                )
                frames = []
                with fail_after(5):
                    async for message in observation:
                        envelope = json.loads(message)
                        if "frame" not in envelope:
                            continue
                        frame = envelope["frame"]
                        frames.append(frame)
                        if frame["kind"] == "ready" or (
                            frame["kind"] == "snapshot" and frame["resume_cursor"] is not None
                        ):
                            break
                assert frames[0]["kind"] == "snapshot"
            assert (await api.get(f"/api/operations/{second}")).json()["status"] == "running"
            # Another participant targets exactly the same pending batch.
            async with httpx.AsyncClient(base_url=http, headers=HEADERS, trust_env=False) as other:
                conflict = await other.post(prefix + "/decisions", json=body)
                # The resumed request checkpoint already superseded this batch.
                assert (
                    conflict.status_code == 409 and conflict.json()["error"]["code"] == "thread_continuation_conflict"
                )
            release.set()
            assert (await settled(api, second))["status"] == "completed"
            stale = await api.post(prefix + "/decisions", json=body)
            assert stale.status_code == 409 and stale.json()["error"]["code"] == "thread_continuation_conflict"
            history = (await api.get(prefix + "/transcript")).json()
            assert "Root completed after one answer." in json.dumps(history)
            assert (await api.get(prefix + "/decisions")).json() is None
            unavailable = await api.get(prefix + "/children/execution-missing/review")
            assert (
                unavailable.status_code == 400
                and unavailable.json()["error"]["code"] == "subagent_execution_unavailable"
            )
            child_review = await api.get(prefix + f"/children/{child['execution_id']}/review")
            assert child_review.status_code == 200, child_review.text
            if len(child_output) <= 32 * 1024:
                assert child_review.json()["summary"] == child_output
                assert child_review.json()["truncated"] is False
            output_page = await api.get(prefix + f"/children/{child['execution_id']}/saved-output", params={"limit": 1})
            assert output_page.status_code == 200, output_page.text
            output = output_page.json()["outputs"][0]
            assert output["text"] == child_output[: 64 * 1024]
            assert output["total_characters"] == len(child_output)
            assert output["target"]["location"]["activity"] is None
            child_comment = publication(output["target"])
            posted = await api.post(prefix + "/comments", json=child_comment)
            assert posted.status_code == 200, posted.text
            child_comment_record = posted.json()
            wrong_parent = await api.get(
                f"/api/threads/{child['child_thread_id']}/children/{child['execution_id']}/saved-output"
            )
            assert wrong_parent.status_code == 400
            usage = (await api.get(prefix + "/usage")).json()
            assert usage["descendants"]["model_requests"] == 1
            assert usage["root"]["model_requests"] == 4
    async with listener(tmp_path, configuration_path=root) as (http, _):
        async with httpx.AsyncClient(base_url=http, headers=HEADERS, trust_env=False) as api:
            restored = (await api.get(prefix + "/transcript")).json()
            assert restored == history
            assert (await api.post(prefix + "/comments", json=child_comment)).json() == child_comment_record
            original = await api.post(prefix + "/saved-output", json=child_comment["target"])
            assert original.status_code == 200
            page = original.json()
            text = page["text"]
            while page["next_offset"] is not None:
                response = await api.post(
                    prefix + "/saved-output", json=child_comment["target"], params={"offset": page["next_offset"]}
                )
                assert response.status_code == 200
                page = response.json()
                text += page["text"]
            assert text == child_output
            assert (await api.get(prefix + "/children")).json()["executions"][0]["execution_id"] == child[
                "execution_id"
            ]
            restored_review = await api.get(prefix + f"/children/{child['execution_id']}/review")
            assert restored_review.status_code == 200, restored_review.text
            if len(child_output) <= 32 * 1024:
                assert restored_review.json()["summary"] == child_output
                assert restored_review.json()["truncated"] is False
            assert (await api.get(prefix + "/decisions")).json() is None


async def test_child_streams_provisional_text_before_message_close_and_saved_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from anyio import create_task_group

    root = _write_configuration(tmp_path)
    agent_path = tmp_path / "agents/assistant.yaml"
    parent = yaml.safe_load(agent_path.read_text())
    parent["subagents"] = [{"agent": "agent-worker"}]
    agent_path.write_text(yaml.safe_dump(parent))
    (tmp_path / "agents/worker.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "1",
                "kind": "agent",
                "id": "agent-worker",
                "name": "Worker",
                "model": "model-primary",
                "subagents": [],
            }
        )
    )
    release, observed, subscribed, completed = Event(), Event(), Event(), Event()
    child_events: list[dict] = []

    async def stream(messages, info):
        returned = {
            part.tool_name for message in messages for part in message.parts if isinstance(part, ToolReturnPart)
        }
        if "delegate" not in {tool.name for tool in info.function_tools}:
            yield "Provisional child text. "
            await release.wait()
            yield "Retained final text."
            return
        if "delegate" not in returned:
            name, args = "delegate", {"subagent_name": "agent-worker", "prompt": "Inspect output"}
        elif "wait_subagent" not in returned:
            name, args = "wait_subagent", {"timeout_seconds": 40}
        else:
            yield "Root complete."
            return
        yield {0: DeltaToolCall(name=name, tool_call_id=name, json_args=json.dumps(args))}

    async def resolve(self, context, model_id):
        return FunctionModel(stream_function=stream)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    async with listener(tmp_path, configuration_path=root) as (http, ws):
        async with httpx.AsyncClient(base_url=http, headers=HEADERS, trust_env=False, timeout=30) as api:
            thread_id = (await api.post("/api/threads", json={})).json()["thread_id"]
            prefix = f"/api/threads/{thread_id}"

            async def watch():
                async with connect(ws + "/api/realtime/connect", proxy=None, origin=http) as response:
                    await response.send('{"api_key":"test-only-key"}')
                    await response.send(
                        json.dumps(
                            {
                                "version": 1,
                                "kind": "subscribe",
                                "channel": "focus",
                                "stream": "focus",
                                "root_thread_id": thread_id,
                            }
                        )
                    )
                    async for message in response:
                        envelope = json.loads(message)
                        if "frame" not in envelope:
                            continue
                        frame = envelope["frame"]
                        if frame["kind"] == "snapshot":
                            subscribed.set()
                        if frame["kind"] != "event" or frame["event"]["run_kind"] != "child":
                            continue
                        event = frame["event"]
                        child_events.append(event)
                        if event["event_type"] == "TEXT_MESSAGE_CONTENT" and "Provisional" in event["payload"]["delta"]:
                            observed.set()
                        if event["event_type"] == "RUN_FINISHED":
                            completed.set()
                            return

            async with create_task_group() as group:
                group.start_soon(watch)
                try:
                    with fail_after(10):
                        await subscribed.wait()
                        receipt = (await api.post(prefix + "/submit", json={"prompt": "Delegate"})).json()["receipt_id"]
                        await observed.wait()
                    assert not completed.is_set()
                    child = (await api.get(prefix + "/children")).json()["executions"][0]
                    assert child["persisted_status"] == "running"
                    assert not any(
                        event["event_type"] == "TEXT_MESSAGE_END"
                        and event["payload"].get("message_id") == child_events[-1]["payload"].get("message_id")
                        for event in child_events
                    )
                    saved = await api.get(prefix + f"/children/{child['execution_id']}/saved-output")
                    assert saved.status_code == 400 and saved.json()["error"]["code"] == "comment_source_unavailable"
                    assert all(
                        event["root_thread_id"] == thread_id and event["execution_id"] == child["execution_id"]
                        for event in child_events
                    )
                    run_ids = {event["run_id"] for event in child_events}
                    assert len(run_ids) == 1
                finally:
                    release.set()
                with fail_after(10):
                    await completed.wait()
                assert (await settled(api, receipt))["status"] == "completed"
                child = (await api.get(prefix + "/children")).json()["executions"][0]
                assert child["persisted_status"] == "succeeded"
                assert child["child_run_id"] == child_events[-1]["run_id"]
                saved = await api.get(prefix + f"/children/{child['execution_id']}/saved-output")
                assert "Retained final text." in saved.text
                assert sum(event["event_type"] == "RUN_FINISHED" for event in child_events) == 1
