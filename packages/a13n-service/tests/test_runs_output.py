"""Terminal output selection and receipts through the Service Run lifecycle."""

import json

import pytest
from a13n_service.runs.stream import stream_key

pytestmark = pytest.mark.anyio


async def _events(service, thread_id):  # type: ignore[no-untyped-def]
    return [
        json.loads(fields["event"])
        for _, fields in await service.runtime.redis.xrange(stream_key(thread_id))
        if "event" in fields
    ]


async def test_quiet_output_is_scoped_to_one_run(service, scripted_model, runs_kit):  # type: ignore[no-untyped-def]
    agent = await runs_kit.create_agent(service, scripted_model)
    scripted_model.call("finish_silently", {}, call_id="quiet")
    first = await runs_kit.start_thread(
        service,
        agent,
        "Check for work",
        options={
            "overrides": {
                "output_spec": {
                    "alternatives": [{"kind": "text"}, {"kind": "server_tool", "ref": "builtin.finish_silently"}]
                }
            }
        },
    )
    await (await runs_kit.attempt(service))
    run = await runs_kit.get_run(service, first["run"]["id"])
    assert run["status"] == "completed" and run["output"] is None
    initial = await scripted_model.request()
    assert "finish_silently" in {item["function"]["name"] for item in initial["tools"]}
    assert scripted_model.requests.empty()

    assert not any(role == "assistant" for role, _ in runs_kit.texts(await runs_kit.items(service, run["id"])))

    response = await runs_kit.submit(service, run["thread_id"], runs_kit.message(agent, "Answer in text"))
    assert response.status_code == 201, response.text
    scripted_model.say("Ordinary reply")
    await (await runs_kit.attempt(service))
    thread = await runs_kit.get_thread(service, run["thread_id"])
    successor = await runs_kit.get_run(service, thread["last_run_id"])
    assert successor["parent_run_id"] == run["id"]
    assert successor["output"] == "Ordinary reply"
    request = await scripted_model.request()
    assert "finish_silently" not in {item["function"]["name"] for item in request.get("tools", [])}


async def _client_wait(service, scripted_model, runs_kit):  # type: ignore[no-untyped-def]
    agent = await runs_kit.create_agent(service, scripted_model)
    scripted_model.call("publish_card", {"title": "Ready"}, call_id="card")
    first = await runs_kit.start_thread(
        service,
        agent,
        "Publish a card",
        options={
            "overrides": {
                "output_spec": {
                    "alternatives": [
                        {"kind": "text"},
                        {
                            "kind": "client_tool",
                            "name": "publish_card",
                            "schema": {
                                "type": "object",
                                "properties": {"title": {"type": "string"}},
                                "required": ["title"],
                            },
                        },
                    ]
                }
            }
        },
    )
    await (await runs_kit.attempt(service))
    waiting = await runs_kit.get_run(service, first["run"]["id"])
    assert waiting["status"] == "waiting", waiting
    (call,) = waiting["pending"]["calls"]
    assert call["tool_call_id"] == "card"
    assert call["tool_name"] == "publish_card"
    assert call["arguments"] == {"title": "Ready"}
    assert call["terminal"] is True
    request = await scripted_model.request()
    tool = next(item["function"] for item in request["tools"] if item["function"]["name"] == "publish_card")
    assert tool["parameters"]["required"] == ["title"]
    events = await _events(service, waiting["thread_id"])
    calls = [event for event in events if event.get("toolCallId") == "card"]
    assert {event["type"] for event in calls} == {"TOOL_CALL_START", "TOOL_CALL_ARGS", "TOOL_CALL_END"}
    finished = next(event for event in events if event["type"] == "RUN_FINISHED")
    assert finished["outcome"]["type"] == "interrupt"
    (interrupt,) = finished["outcome"]["interrupts"]
    assert interrupt["toolCallId"] == "card" and interrupt["metadata"]["terminal"] is True
    saved = await runs_kit.items(service, waiting["id"])
    assert any(
        item["content"].get("outcome", {}).get("type") == "interrupt"
        for item in saved["items"]
        if item["content"].get("type") == "RUN_FINISHED"
    )
    return agent, waiting


@pytest.mark.parametrize("failed", [False, True])
async def test_client_output_receipt_finishes_the_successor_without_model_io(service, scripted_model, runs_kit, failed):  # type: ignore[no-untyped-def]
    agent, waiting = await _client_wait(service, scripted_model, runs_kit)
    receipt = (
        {"status": "failed", "message": "Could not publish"}
        if failed
        else {
            "status": "returned",
            "value": {"card_id": "one"},
        }
    )
    url = f"{service.api}/runs/{waiting['id']}/resume"
    body = {"approvals": {}, "calls": {"card": receipt}}
    refused = await service.client.post(
        url,
        json={**body, "input": {"content": [{"type": "text", "text": "Another task"}]}},
        headers=runs_kit.fresh_key(),
    )
    assert refused.status_code == 400, refused.text
    response = await service.client.post(url, json=body, headers=runs_kit.fresh_key())
    assert response.status_code == 201, response.text
    await (await runs_kit.attempt(service))
    successor = await runs_kit.get_run(service, response.json()["id"])
    assert successor["parent_run_id"] == waiting["id"]
    if failed:
        assert (successor["status"], successor["failure"]["code"]) == ("failed", "client_output_failed"), successor
    else:
        assert successor["status"] == "completed" and successor["output"] == receipt["value"], successor
        events = await _events(service, successor["thread_id"])
        tool_result = next(event for event in events if event["type"] == "TOOL_CALL_RESULT")
        assert tool_result["toolCallId"] == "card"
        finished = [event for event in events if event["type"] == "RUN_FINISHED"][-1]
        assert finished["outcome"]["type"] == "success" and finished["result"] == receipt["value"]
    assert scripted_model.requests.empty()
    assert (await runs_kit.get_run(service, waiting["id"]))["status"] == "waiting"

    message = await runs_kit.submit(service, waiting["thread_id"], runs_kit.message(agent, "Continue"))
    assert message.status_code == 201, message.text
    scripted_model.say("Continued")
    await (await runs_kit.attempt(service))
    request = await scripted_model.request()
    (result,) = [part["content"] for part in request["messages"] if part.get("tool_call_id") == "card"]
    assert ("Could not publish" if failed else "card_id") in result


async def test_fork_of_terminal_wait_runs_its_own_prompt(service, scripted_model, runs_kit):  # type: ignore[no-untyped-def]
    agent, waiting = await _client_wait(service, scripted_model, runs_kit)
    response = await service.client.post(
        f"{service.api}/runs/{waiting['id']}/fork",
        json=runs_kit.message(agent, "Go another way"),
        headers=runs_kit.fresh_key(),
    )
    assert response.status_code == 201, response.text
    scripted_model.say("New branch")
    await (await runs_kit.attempt(service))
    fork = await runs_kit.get_run(service, response.json()["run"]["id"])
    assert fork["output"] == "New branch", fork
    request = await scripted_model.request()
    assert any(message.get("tool_call_id") == "card" for message in request["messages"])
    assert "Go another way" in str(runs_kit.user_texts(request))
    assert (await runs_kit.get_run(service, waiting["id"]))["pending"] == waiting["pending"]
