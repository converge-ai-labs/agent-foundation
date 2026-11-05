"""Run-only primary selection preserves Thread mounts and accepted instance identities."""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

from .environments_support import act, environment, interrupt, reserve, start, with_fake_providers

pytestmark = pytest.mark.anyio


@pytest.fixture
async def env(service) -> SimpleNamespace:  # type: ignore[no-untyped-def]
    return await with_fake_providers(service)


def primary(run: dict) -> str:
    return next(mount["environment_id"] for mount in run["environment_mounts"] if mount["name"] == "workspace")


async def test_run_allocation_preserves_desired_mounts_and_allocates_for_each_new_input(env) -> None:  # type: ignore[no-untyped-def]
    original = await start(env)
    thread_id = original["thread"]["id"]
    desired = await env.client.get(f"{env.api}/threads/{thread_id}/environments")
    await interrupt(env, original["run"]["id"])
    options = {"environment": {"mode": "fresh", "template_id": env.template["id"]}}
    body = {"agent_id": env.agent["id"], "payload": {"content": [{"type": "text", "text": "next"}]}, "options": options}
    ids = {primary(original["run"])}
    for _ in range(2):
        response = await env.client.post(
            f"{env.api}/threads/{thread_id}/inbox", json=body, headers={"idempotency-key": uuid4().hex}
        )
        assert response.status_code == 201, response.text
        run = response.json()["run"]
        assert primary(run) not in ids
        ids.add(primary(run))
        assert (await environment(env, primary(run)))["status"] == "reserved"
        assert (await env.client.get(f"{env.api}/threads/{thread_id}/environments")).json() == desired.json()
        await interrupt(env, run["id"])
    # Omitting a Run-only selection returns to the persistent Thread mount.
    del body["options"]
    response = await env.client.post(
        f"{env.api}/threads/{thread_id}/inbox", json=body, headers={"idempotency-key": uuid4().hex}
    )
    assert primary(response.json()["run"]) == primary(original["run"])


async def test_fresh_allocation_is_idempotent_and_does_not_install_a_thread_mount(env) -> None:  # type: ignore[no-untyped-def]
    body = {
        "agent_id": env.agent["id"],
        "payload": {"content": [{"type": "text", "text": "fresh"}]},
        "options": {"environment": {"mode": "fresh", "template_id": env.template["id"]}},
    }
    headers = {"idempotency-key": uuid4().hex}
    responses = await asyncio.gather(
        *(env.client.post(f"{env.api}/threads", json=body, headers=headers) for _ in range(2))
    )
    assert all(response.status_code in {200, 201} for response in responses), [response.text for response in responses]
    first, replay = [response.json() for response in responses]
    assert first["run"]["id"] == replay["run"]["id"]
    assert primary(first["run"]) == primary(replay["run"])
    assert len((await env.client.get(f"{env.api}/environments")).json()["items"]) == 1
    assert (await env.client.get(f"{env.api}/threads/{first['thread']['id']}/environments")).json()["items"] == []


async def test_explicit_instance_selection_never_allocates_a_replacement(env) -> None:  # type: ignore[no-untyped-def]
    selected = await reserve(env, env.template["id"])
    options = {"environment": {"mode": "existing", "environment_id": selected["id"]}}
    first = await start(env, options=options)
    assert primary(first["run"]) == selected["id"]
    await interrupt(env, first["run"]["id"])
    assert (await act(env, "DELETE", selected["id"]))[0] == 202
    refused = await start(env, options=options)
    assert refused["run"] is None
    assert refused["entry"]["failure"]["code"] == "environment_deleted"
    options["environment"]["environment_id"] = f"env_{uuid4().hex}"
    missing = await start(env, options=options)
    assert missing["run"] is None and missing["entry"]["failure"]["code"] == "not_found"
    assert (await env.client.get(f"{env.api}/environments")).json()["items"] == []
    assert len((await env.client.get(f"{env.api}/environments?status=deleted")).json()["items"]) == 1


async def test_resume_reuses_the_run_only_primary(env, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    review = {"name": "review", "description": "Review", "parameters_json_schema": {"type": "object"}}
    agent = await runs_kit.create_agent(env, scripted_model, client_tools=[review])
    started = await runs_kit.start_thread(
        env, agent, "review", options={"environment": {"mode": "fresh", "template_id": env.template["id"]}}
    )
    scripted_model.call("review", {}, call_id="review")
    await (await runs_kit.attempt(env))
    await scripted_model.request()
    assert (await runs_kit.get_run(env, started["run"]["id"]))["status"] == "waiting"
    response = await env.client.post(
        f"{env.api}/runs/{started['run']['id']}/resume",
        json={"approvals": {}, "calls": {"review": {"status": "returned", "value": "ok"}}},
        headers=runs_kit.fresh_key(),
    )
    assert response.status_code == 201, response.text
    assert primary(response.json()) == primary(started["run"])
    assert len((await env.client.get(f"{env.api}/environments")).json()["items"]) == 1
