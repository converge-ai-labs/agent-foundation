"""Accepted Run configuration is durable, independent of Agent revisions and immutable under steering."""

import asyncio
import re
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from a13n_harness import RunError
from a13n_harness.providers.endpoint_policy import EndpointPolicyError
from a13n_service.runs.execute import _deterministic, _own
from a13n_service.runs.inbox import Request
from a13n_service.runs.schemas import Message

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("grouped", [False, True])
def test_wrapped_endpoint_refusals_keep_the_safe_reason(grouped):
    refusal = EndpointPolicyError("endpoint must use HTTPS")
    wrapped = RunError("Agent execution failed.", code="agent_run_failed")
    wrapped.__cause__ = refusal
    error = ExceptionGroup("monitors", [ExceptionGroup("stream", [wrapped])]) if grouped else wrapped
    outcome = _deterministic(_own(error))
    assert outcome is not None and outcome.failure is not None
    assert outcome.failure.model_dump() == {
        "code": "provider_endpoint_denied",
        "message": "endpoint must use HTTPS",
    }


@pytest.mark.parametrize(
    ("base_url", "reason"),
    [
        ("http://model.example/v1", "endpoint must use HTTPS"),
        ("https://model bad.example/v1", "allowed hosts must be exact DNS hostnames or IP literals"),
        ("https://user:private-secret@model.example/v1", "endpoint must not contain user information"),
        ("https://model.example/v1?api_key=private-secret", "endpoint query contains a sensitive parameter"),
    ],
)
async def test_model_endpoint_refusal_is_visible_without_retrying(serve, settings, runs_kit, base_url, reason):
    providers = settings.providers.model_copy(update={"require_https": True, "http_origins": ()})
    async with serve(settings=settings.model_copy(update={"providers": providers})) as service:
        agent = await runs_kit.create_agent(service, SimpleNamespace(url=base_url))
        run_id = (await runs_kit.start_thread(service, agent, "hi"))["run"]["id"]
        await (await runs_kit.attempt(service))
        run = await runs_kit.get_run(service, run_id)
    assert run["status"] == "failed" and run["attempts"] == 1, run
    assert run["failure"] == {"code": "provider_endpoint_denied", "message": reason}
    assert "private-secret" not in str(run["failure"])


async def test_model_http_failure_preserves_status_without_exposing_the_response(service, runs_kit, listen):
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    app = FastAPI()
    requests = []

    @app.post("/v1/chat/completions")
    async def rejected():
        requests.append(True)
        return JSONResponse(
            {"error": {"message": "private-secret upstream authentication details", "type": "authentication_error"}},
            status_code=401,
        )

    async with listen(app) as url:
        agent = await runs_kit.create_agent(service, SimpleNamespace(url=f"{url}/v1"))
        run_id = (await runs_kit.start_thread(service, agent, "hi"))["run"]["id"]
        await (await runs_kit.attempt(service))
        run = await runs_kit.get_run(service, run_id)
    assert run["status"] == "failed" and run["attempts"] == 1, run
    assert run["failure"]["code"] == "agent_run_failed"
    assert "HTTP 401" in run["failure"]["message"]
    assert "private-secret" not in str(run["failure"])
    assert requests == [True]


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
    agent = await runs_kit.create_agent(service, scripted_model, **runs_kit.LOOKUP)
    host = urlsplit(scripted_model.url).hostname
    rule = f"regex:{re.escape(host)}" if use_regex else host
    configuration = {"allowed_hosts": [rule], "extensions": {"example.filter": {"images": ["keep"]}}}
    gate = asyncio.Event()
    # Every native request stays open while configuration is checked; immediate steering may
    # cancel either of the first two requests before the equal-config entry is incorporated.
    for _ in range(3):
        scripted_model.say("Done", gate=gate)
    started = await runs_kit.start_thread(service, agent, "start", options={"configuration": configuration})
    thread_id = started["thread"]["id"]
    run_id = started["run"]["id"]
    assert started["run"]["options"]["configuration"] == configuration
    # Edit while the worker has not started: a gated model response does not stop its initial checkpoint
    # from assigning steers, and assigned entries correctly refuse editing before configuration validation.
    omitted = await runs_kit.submit(service, thread_id, runs_kit.message(agent, "omitted config"))
    assert omitted.status_code == 201, omitted.text
    assert omitted.json()["entry"]["status"] == "pending"
    thread = await runs_kit.get_thread(service, thread_id)
    edited = await service.client.patch(
        f"{service.api}/threads/{thread_id}/inbox/{omitted.json()['entry']['id']}",
        json={"options": {"configuration": {"allowed_hosts": []}}},
        headers=runs_kit.if_match(thread),
    )
    assert edited.status_code == 409, edited.text
    assert "run_configuration_immutable" in edited.text

    running = await runs_kit.attempt(service)
    await scripted_model.request()
    for value in ({"allowed_hosts": []}, {"allowed_hosts": None}, {"allowed_hosts": ["different.test"]}):
        body = runs_kit.message(agent, "change", options={"configuration": value})
        response = await runs_kit.submit(service, thread_id, body)
        assert response.status_code == 409, response.text
        assert "run_configuration_immutable" in response.text
    equal = await runs_kit.submit(
        service, thread_id, runs_kit.message(agent, "same config", options={"configuration": configuration})
    )
    assert equal.status_code == 201, equal.text
    while "same config" not in str(runs_kit.user_texts(await scripted_model.request())):
        pass
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
