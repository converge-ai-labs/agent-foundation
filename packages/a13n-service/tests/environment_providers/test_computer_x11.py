"""Opt-in Service integration against an owned disposable X11 fixture, never a personal desktop.

See dev/fixtures/linux-desktop/README.md. The endpoint must serve that fixture on loopback.
"""

import json
import os
from io import BytesIO

import pytest
from a13n_harness.environment.models import COMPUTER_ACTIONS, EnvironmentPermissionSet
from a13n_service.runs.environments.adapters import execution_connector
from PIL import Image

from ..environments_support import register, target

ENDPOINT = os.environ.get("A13N_SERVICE_X11_ENDPOINT")
TOKEN = "service-x11-fixture-token-not-a-secret"
pytestmark = [pytest.mark.anyio, pytest.mark.skipif(not ENDPOINT, reason="Requires disposable Service X11 desktop")]


def returned(request, call_id):
    value = json.loads(next(m["content"] for m in request["messages"] if m.get("tool_call_id") == call_id))
    if isinstance(value, list):
        value = value[0]
    if isinstance(value, str):
        value = json.loads(value)
    assert isinstance(value, dict), repr(value)[:1000]
    return value


async def test_native_desktop_permissions_freeze_and_retained_screenshots(service, scripted_model, runs_kit):
    assert ENDPOINT and ENDPOINT.startswith("http://127.0.0.1:")
    await runs_kit.pause_sweeps(service)
    response = await register(service, ENDPOINT, TOKEN)
    assert response.status_code == 201, response.text
    environment = response.json()
    checked = await service.client.post(f"{service.api}/environments/{environment['id']}/computer/check")
    assert checked.status_code == 200, checked.text
    assert checked.json()["observe_ready"] and checked.json()["input_ready"]
    assert "environment.computer.type_text" not in checked.json()["operations"]
    agent = await runs_kit.create_agent(service, scripted_model, toolsets={"computer": {"enabled": True}})
    observe = {"operations": ["environment.computer.describe", "environment.computer.observe"]}
    interact = EnvironmentPermissionSet(operations=COMPUTER_ACTIONS).model_dump(mode="json")
    submitted = await runs_kit.start_thread(
        service,
        agent,
        "Observe only",
        environments=[
            {
                "name": "workspace",
                "environment_id": environment["id"],
                "permission_ceiling": observe,
            }
        ],
    )
    thread_id = submitted["thread"]["id"]
    thread = await service.client.get(f"{service.api}/threads/{thread_id}")
    changed = await service.client.patch(
        f"{service.api}/threads/{thread_id}/environments/workspace",
        json={"permission_ceiling": interact},
        headers={"if-match": thread.headers["etag"]},
    )
    assert changed.status_code == 200, changed.text
    scripted_model.call("computer_observe", {"max_dimension": 1024}, call_id="before")
    scripted_model.say("Observed without input.")
    await (await runs_kit.attempt(service))
    first = await scripted_model.request()
    names = {t["function"]["name"] for t in first["tools"]}
    assert "computer_observe" in names and "computer_click" not in names and "computer_press_keys" not in names
    await scripted_model.request()
    frozen = await runs_kit.get_run(service, submitted["run"]["id"])
    assert frozen["environment_mounts"][0]["permission_ceiling"] == observe
    assert frozen["status"] == "completed"

    # A later Run inherits the changed ceiling, and performs real native input.
    reply = await runs_kit.submit(service, thread_id, runs_kit.message(agent, "Click and type a key"))
    assert reply.status_code == 201, reply.text
    second_id = reply.json()["run"]["id"]
    scripted_model.call("computer_observe", {"max_dimension": 1024}, call_id="observe")
    task = await runs_kit.attempt(service)
    initial = await scripted_model.request()
    names = {t["function"]["name"] for t in initial["tools"]}
    assert "computer_click" in names and "computer_press_keys" in names
    after_observe = await scripted_model.request()
    assert "computer_type_text" not in {t["function"]["name"] for t in after_observe["tools"]}
    observation = returned(after_observe, "observe")
    assert observation["ok"], observation
    # Activate the new desktop window before clicking its control. Native input receipts
    # confirm dispatch, not that the window manager or application consumed a gesture.
    scripted_model.call(
        "computer_click",
        {"observation_id": observation["observation_id"], "point": {"x": 400, "y": 300}},
        call_id="focus",
    )
    after_focus = await scripted_model.request()
    assert returned(after_focus, "focus")["effect"] == "executed"
    scripted_model.call(
        "computer_click",
        {"observation_id": observation["observation_id"], "point": {"x": 100, "y": 120}},
        call_id="click",
    )
    after_click = await scripted_model.request()
    assert returned(after_click, "click")["effect"] == "executed"
    scripted_model.call("computer_press_keys", {"keys": ["a"]}, call_id="key")
    after_key = await scripted_model.request()
    assert returned(after_key, "key")["effect"] == "executed"
    scripted_model.call("computer_observe", {"max_dimension": 1024}, call_id="after")
    final = await scripted_model.request()
    assert returned(final, "after")["ok"]
    assert any("data:image/jpeg;base64," in json.dumps(m) for m in final["messages"])
    scripted_model.say("Clicked and entered a key on the real desktop.")
    await task
    run = await runs_kit.get_run(service, second_id)
    assert run["status"] == "completed", run["failure"]
    items = await runs_kit.items(service, second_id)
    images = {
        item["content"]["toolCallId"]: item["content"]["tool_images"][0]
        for item in items["items"]
        if item["content"].get("tool_images")
    }
    for call_id in ("observe", "after"):
        response = await service.client.get(f"{service.api}/runs/{second_id}/images/{images[call_id]['id']}")
        assert response.status_code == 200
        image = Image.open(BytesIO(response.content))
        assert image.size == (1024, 768)
        red, green, _ = image.getpixel((70, 100))
        assert red > green if call_id == "observe" else green > red, (call_id, image.getpixel((70, 100)))
    # Independent GUI event-loop evidence; no input injected outside Computer tools.
    connector = await execution_connector(service.runtime, await target(service, environment["id"]))
    async with await connector.open() as execution:
        state = json.loads((await execution.operations.files.read_text("/tmp/work/desktop.json")).text)
    assert state["clicks"] == 1 and state["keys"] == ["a"]
