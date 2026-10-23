"""Caller-owned session identity, atomic first input, and finding sessions by recognizable context."""

import hashlib

import pytest
from a13n_service.runs.inbox import Request
from a13n_service.runs.schemas import NewThread, canonical_json

pytestmark = pytest.mark.anyio

ORIGIN = {
    "application": "review-bot",
    "name": "GitHub · acme/widgets #42",
    "external_id": "main_discussion_42",
}


async def test_identity_create_update_clear_and_stale_editor(service) -> None:
    created = await service.client.post(
        f"{service.api}/sessions",
        json={"title": "  Review #42  ", "origin": ORIGIN, "labels": {"project": "Widgets"}},
    )
    assert created.status_code == 201, created.text
    value = created.json()
    assert value["title"] == "Review #42" and value["origin"] == ORIGIN
    path = f"{service.api}/sessions/{value['id']}"
    changed = await service.client.patch(
        path, json={"title": "Review and follow up"}, headers={"if-match": created.headers["etag"]}
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["origin"] == ORIGIN and changed.json()["labels"] == {"project": "Widgets"}
    stale = await service.client.patch(path, json={"origin": None}, headers={"if-match": created.headers["etag"]})
    assert stale.status_code == 412
    cleared = await service.client.patch(
        path, json={"title": None, "origin": None}, headers={"if-match": changed.headers["etag"]}
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["title"] is None and cleared.json()["origin"] is None
    assert cleared.json()["labels"] == {"project": "Widgets"}
    listed = (await service.client.get(f"{service.api}/sessions")).json()["items"]
    assert listed[0]["title"] is None and listed[0]["origin"] is None


@pytest.mark.parametrize(
    "identity",
    [
        {"title": " "},
        {"title": "x" * 257},
        {"origin": {**ORIGIN, "url": "https://github.com/acme/widgets/pull/42"}},
        {"origin": {**ORIGIN, "application": " "}},
    ],
)
async def test_invalid_identity_is_rejected_without_creating_a_session(service, identity) -> None:
    response = await service.client.post(f"{service.api}/sessions", json=identity)
    assert response.status_code == 400, response.text
    assert (await service.client.get(f"{service.api}/sessions")).json()["items"] == []


async def test_first_thread_creates_identity_atomically_and_replays_it(service, scripted_model, runs_kit) -> None:
    agent = await runs_kit.create_agent(service, scripted_model)
    body = runs_kit.message(
        agent,
        "Actual input",
        session={"title": "Review #42", "origin": ORIGIN, "labels": {"role": "main"}, "memory": {"enabled": False}},
    )
    headers = runs_kit.fresh_key()
    created = await service.client.post(f"{service.api}/threads", json=body, headers=headers)
    assert created.status_code == 201, created.text
    owner = created.json()["thread"]["session_id"]
    view = (await service.client.get(f"{service.api}/sessions/{owner}")).json()
    assert view["title"] == "Review #42" and view["origin"] == ORIGIN and view["labels"] == {"role": "main"}
    assert view["memory"]["enabled"] is False
    replay = await service.client.post(f"{service.api}/threads", json=body, headers=headers)
    assert replay.status_code == 200 and replay.json()["thread"]["session_id"] == owner
    changed = await service.client.post(
        f"{service.api}/threads", json={**body, "session": {"title": "Different"}}, headers=headers
    )
    assert changed.status_code == 409, changed.text
    mixed = await service.client.post(
        f"{service.api}/threads", json={**body, "session_id": owner}, headers=runs_kit.fresh_key()
    )
    assert mixed.status_code == 400
    refused = await service.client.post(
        f"{service.api}/threads", json={**body, "agent_id": "ap_00000000000000000000"}, headers=runs_kit.fresh_key()
    )
    assert refused.status_code == 404
    assert len((await service.client.get(f"{service.api}/sessions")).json()["items"]) == 1
    # Display context is not silently supplied to the model as instructions.
    scripted_model.say("Done")
    await (await runs_kit.attempt(service))
    request = await scripted_model.request()
    model_input = canonical_json(runs_kit.user_texts(request)).decode()
    assert "Actual input" in model_input
    assert "Review #42" not in model_input and ORIGIN["external_id"] not in model_input


async def test_first_input_names_an_empty_session_once(service, scripted_model, runs_kit) -> None:
    agent = await runs_kit.create_agent(service, scripted_model)
    empty = (await service.client.post(f"{service.api}/sessions", json={})).json()
    created = await runs_kit.start_thread(service, agent, "First\n  question", session_id=empty["id"])
    path = f"{service.api}/sessions/{empty['id']}"
    assert (await service.client.get(path)).json()["title"] == "First question"
    scripted_model.say("Done")
    await (await runs_kit.attempt(service))
    await runs_kit.submit(service, created["thread"]["id"], runs_kit.message(agent, "Another question"))
    assert (await service.client.get(path)).json()["title"] == "First question"


async def test_search_combines_identity_text_scope_and_filters(service, scripted_model, runs_kit) -> None:
    agent = await runs_kit.create_agent(service, scripted_model)
    created = await runs_kit.start_thread(
        service, agent, "南京部署", session={"title": "Review #42", "origin": ORIGIN, "labels": {"project": "Widgets"}}
    )
    owner = created["thread"]["session_id"]
    for query in (
        "review #42",
        "widgets",
        "#42",
        ORIGIN["external_id"],
        owner,
        created["thread"]["id"],
        "南京",
    ):
        found = await service.client.get(f"{service.api}/sessions", params={"q": query})
        assert found.status_code == 200, found.text
        assert [item["id"] for item in found.json()["items"]] == [owner], query
    for params in (
        {"q": "%"},
        {"q": "Review_#42"},
        {"q": "#42", "application": "another-app"},
        {"q": "#42", "label": "project:Other"},
    ):
        assert (await service.client.get(f"{service.api}/sessions", params=params)).json()["items"] == []
    # An exact Thread ID must not open that Thread under a different Session
    # whose descriptive metadata happens to mention it.
    await service.client.post(f"{service.api}/sessions", json={"title": created["thread"]["id"]})
    exact = await service.client.get(f"{service.api}/sessions", params={"q": created["thread"]["id"]})
    assert [item["id"] for item in exact.json()["items"]] == [owner]
    matched = await service.client.get(
        f"{service.api}/sessions", params={"application": "review-bot", "label": "project:Widgets"}
    )
    assert [item["id"] for item in matched.json()["items"]] == [owner]
    other = await service.client.post(f"{service.organization}/workspaces", json={"name": "Other"})
    hidden = await service.client.get(
        f"{service.api}/sessions", params={"q": "Widgets"}, headers={"x-workspace-id": other.json()["id"]}
    )
    assert hidden.status_code == 200 and hidden.json()["items"] == []


def test_old_thread_requests_retain_their_idempotency_digest() -> None:
    body = NewThread(agent_id="ap_11111111111111111111", payload={"content": [{"type": "text", "text": "hello"}]})
    previous = body.model_dump(mode="json", exclude={"session"})
    expected = hashlib.sha256(canonical_json(["thread", "ws_example", previous])).hexdigest()
    assert Request.of("retry", "thread", "ws_example", body).digest == expected
