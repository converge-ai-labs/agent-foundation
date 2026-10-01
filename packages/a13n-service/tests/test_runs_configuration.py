"""Accepted Run configuration is durable, independent of Agent revisions and immutable under steering."""

import asyncio
import re
from urllib.parse import urlsplit

import pytest
from a13n_service.runs.inbox import Request
from a13n_service.runs.schemas import Message

pytestmark = pytest.mark.anyio


def test_configuration_has_canonical_idempotency_digest():
    base = {"agent_id": "agt_000000000000000000000000", "payload": {"content": [{"type": "text", "text": "hi"}]}}
    first = Message.model_validate({**base, "options": {"configuration": {"allowed_hosts": ["B.test", "a.test."]}}})
    second = Message.model_validate(
        {**base, "options": {"configuration": {"allowed_hosts": ["a.test", "b.test", "a.test"]}}}
    )
    assert Request.of("key", "message", "thread", first).digest == Request.of("key", "message", "thread", second).digest
    denied = Message.model_validate({**base, "options": {"configuration": {"allowed_hosts": []}}})
    assert Request.of("key", "message", "thread", first).digest != Request.of("key", "message", "thread", denied).digest


@pytest.mark.parametrize("use_regex", [False, True])
async def test_steering_retains_or_matches_the_accepted_configuration(service, scripted_model, runs_kit, use_regex):
    agent = await runs_kit.create_agent(service, scripted_model, toolsets={"configuration": {"enabled": True}})
    host = urlsplit(scripted_model.url).hostname
    rule = f"regex:{re.escape(host)}" if use_regex else host
    configuration = {"allowed_hosts": [rule], "extensions": {"example.filter": {"images": ["keep"]}}}
    gate = asyncio.Event()
    scripted_model.call("find_resources", {"kind": "model"}, call_id="call_find", gate=gate)
    scripted_model.say("Done")
    started = await runs_kit.start_thread(service, agent, "start", options={"configuration": configuration})
    thread_id = started["thread"]["id"]
    run_id = started["run"]["id"]
    assert started["run"]["options"]["configuration"] == configuration
    running = await runs_kit.attempt(service)
    await scripted_model.request()
    for value in ({"allowed_hosts": []}, {"allowed_hosts": None}, {"allowed_hosts": ["different.test"]}):
        body = runs_kit.message(agent, "change", options={"configuration": value})
        response = await runs_kit.submit(service, thread_id, body)
        assert response.status_code == 409, response.text
        assert "run_configuration_immutable" in response.text
    omitted = await runs_kit.submit(service, thread_id, runs_kit.message(agent, "omitted config"))
    equal = await runs_kit.submit(
        service, thread_id, runs_kit.message(agent, "same config", options={"configuration": configuration})
    )
    assert omitted.status_code == equal.status_code == 201
    thread = await runs_kit.get_thread(service, thread_id)
    edited = await service.client.patch(
        f"{service.api}/threads/{thread_id}/inbox/{omitted.json()['entry']['id']}",
        json={"options": {"configuration": {"allowed_hosts": []}}},
        headers=runs_kit.if_match(thread),
    )
    assert edited.status_code == 409, edited.text
    assert "run_configuration_immutable" in edited.text
    gate.set()
    await running
    entries = {entry["id"]: entry for entry in await runs_kit.inbox(service, thread_id)}
    assert entries[omitted.json()["entry"]["id"]]["assigned_run_id"] == run_id
    assert entries[equal.json()["entry"]["id"]]["assigned_run_id"] == run_id
    assert (await runs_kit.get_run(service, run_id))["options"]["configuration"] == configuration


async def test_async_child_inherits_the_parent_configuration(service, scripted_model, runs_kit):
    await runs_kit.pause_sweeps(service)
    coordinator = await runs_kit.delegating(
        service, scripted_model, "async", edge={"usage_limits": {"request_limit": 5}}
    )
    configuration = {
        "allowed_hosts": [urlsplit(scripted_model.url).hostname],
        "extensions": {"example.filter": {"image": "keep"}},
    }
    scripted_model.call(
        "delegate", {"subagent_name": "helper", "prompt": "compute"}, call_id="call_d", to="Role: coordinator"
    )
    scripted_model.say("Delegated", to="Role: coordinator")
    started = await runs_kit.start_thread(service, coordinator, "delegate", options={"configuration": configuration})
    await (await runs_kit.attempt(service))
    # The parent reports the durable child Run receipt.
    from .test_runs_children import _child_run

    child = await _child_run(service, started["run"]["id"])
    assert child.options["configuration"] == configuration
    assert child.options["max_usage"] == {"requests": 5}
    scripted_model.say("42", to="Role: worker")
    await (await runs_kit.attempt(service))
    assert (await runs_kit.get_run(service, child.id))["options"]["configuration"] == configuration


async def test_initial_url_input_is_denied_before_any_request(service, scripted_model, runs_kit, listen):
    from fastapi import FastAPI

    called = []
    app = FastAPI()

    @app.get("/input")
    async def read_input():
        called.append(True)
        return {"text": "must not be read"}

    agent = await runs_kit.create_agent(service, scripted_model)
    # Input preparation must authorize its URL before making any HTTP or Model request.
    configuration = {"allowed_hosts": [urlsplit(scripted_model.url).hostname]}
    async with listen(app) as url:
        message = runs_kit.message(agent, "start", options={"configuration": configuration})
        denied_url = url.replace("127.0.0.1", "localhost")
        message["payload"] = {"content": [{"type": "url", "url": f"{denied_url}/input"}]}
        response = await service.client.post(f"{service.api}/threads", json=message, headers=runs_kit.fresh_key())
        assert response.status_code == 201, response.text
        await (await runs_kit.attempt(service))
        run = await runs_kit.get_run(service, response.json()["run"]["id"])
    assert run["status"] == "failed", run
    assert run["failure"]["code"] == "invalid_argument"
    assert called == []
