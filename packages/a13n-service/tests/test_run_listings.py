"""Bounded discovery, deep-link pagination and constant-query ancestry under real PostgreSQL."""

from contextlib import contextmanager

import pytest
from sqlalchemy import event

pytestmark = pytest.mark.anyio


@contextmanager
def statements(service):
    captured = []

    def record(connection, cursor, statement, parameters, context, executemany):
        captured.append(statement)

    engine = service.runtime.storage.engine.sync_engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        yield captured
    finally:
        event.remove(engine, "before_cursor_execute", record)


async def test_input_previews_are_unicode_bounded_pg_projections(service, scripted_model, runs_kit, monkeypatch):
    agent = await runs_kit.create_agent(service, scripted_model)
    text = "汉😀" * 100 + "beyond the preview" * 1000
    started = await runs_kit.start_thread(service, agent, text)
    run_id, thread_id = started["run"]["id"], started["thread"]["id"]

    async def no_objects(*args, **kwargs):
        raise AssertionError("Discovery must not read object storage")

    monkeypatch.setattr(type(service.runtime.objects), "get", no_objects)
    for path in ("runs", f"threads/{thread_id}/runs", f"runs/{run_id}/lineage"):
        with statements(service) as sql:
            response = await service.client.get(f"{service.api}/{path}")
        assert response.status_code == 200, response.text
        item = response.json()["items"][0]
        assert item["input_preview"] == {"text": text[:200], "truncated": True}
        assert (
            not {"view", "input", "resume", "output", "options", "pending", "environment_mounts", "memory_mounts"}
            & item.keys()
        )
        assert len(response.content) < 2500
        assert any("left(" in query and "inbox_entries.payload" in query for query in sql)
        assert not any(
            "runs.output" in query or "runs.options" in query or "threads.message_history" in query for query in sql
        )
    detail = await service.client.get(f"{service.api}/runs/{run_id}")
    assert detail.json()["input"]["content"][0]["text"] == text


async def test_thread_pages_anchor_and_bidirectional_cursors_do_not_skip_runs(service, scripted_model, runs_kit):
    agent = await runs_kit.create_agent(service, scripted_model)
    started = await runs_kit.start_thread(service, agent, "first")
    thread_id = started["thread"]["id"]
    ids = [started["run"]["id"]]
    for number in range(1, 8):
        stopped = await service.client.post(f"{service.api}/runs/{ids[-1]}/interrupt")
        assert stopped.status_code == 200, stopped.text
        added = await runs_kit.submit(service, thread_id, runs_kit.message(agent, f"input {number}"))
        assert added.status_code == 201, added.text
        ids.append(added.json()["run"]["id"])

    async def page(**params):
        response = await service.client.get(f"{service.api}/threads/{thread_id}/runs", params={"limit": 2, **params})
        assert response.status_code == 200, response.text
        return response.json()

    latest = await page()
    assert latest["total"] == latest["first_number"] == 8
    assert latest["previous_cursor"] is None
    assert [item["id"] for item in latest["items"]] == ids[:5:-1]
    anchored = await page(anchor=ids[3])
    assert anchored["first_number"] == 4 and anchored["total"] == 8
    assert [item["id"] for item in anchored["items"]] == [ids[3], ids[2]]
    older = await page(cursor=anchored["next_cursor"])
    assert older["first_number"] == 2 and older["next_cursor"] is None
    assert [item["id"] for item in older["items"]] == [ids[1], ids[0]]
    back = await page(cursor=older["previous_cursor"])
    assert back == anchored
    newer = await page(cursor=anchored["previous_cursor"])
    assert newer["first_number"] == 6
    assert [item["id"] for item in newer["items"]] == [ids[5], ids[4]]
    assert await page(cursor=newer["previous_cursor"]) == latest

    other = await runs_kit.start_thread(service, agent, "foreign")
    for params in (
        {"anchor": other["run"]["id"]},
        {"anchor": ids[3], "cursor": anchored["next_cursor"]},
        {"cursor": "invalid"},
    ):
        response = await service.client.get(f"{service.api}/threads/{thread_id}/runs", params=params)
        assert response.status_code in {400, 404}
    response = await service.client.get(
        f"{service.api}/threads/{other['thread']['id']}/runs", params={"cursor": anchored["next_cursor"]}
    )
    assert response.status_code == 400

    # A longer ancestor page takes the same SQL round trips as a single run.
    counts = []
    for limit in (1, 8):
        with statements(service) as sql:
            response = await service.client.get(f"{service.api}/runs/{ids[-1]}/lineage", params={"limit": limit})
        assert response.status_code == 200, response.text
        values = response.json()["items"]
        assert [item["id"] for item in values] == list(reversed(ids))[: len(values)]
        assert any("WITH RECURSIVE" in statement for statement in sql)
        counts.append(sum("FROM runs" in statement for statement in sql))
    assert counts == [2, 2]


async def test_input_preview_preserves_part_order_and_resume_input(service, scripted_model, runs_kit):
    agent = await runs_kit.create_agent(
        service,
        scripted_model,
        client_tools=[
            {
                "name": "lookup",
                "description": "Look up",
                "parameters_json_schema": {"type": "object"},
            }
        ],
    )
    body = runs_kit.message(agent, "ignored")
    body["payload"]["content"] = [
        {"type": "text", "text": "first"},
        {"type": "json", "value": {"a": 1}},
        {"type": "text", "text": "last"},
    ]
    started = await service.client.post(f"{service.api}/threads", json=body, headers=runs_kit.fresh_key())
    assert started.status_code == 201, started.text
    run = started.json()["run"]
    listing = await service.client.get(f"{service.api}/threads/{run['thread_id']}/runs")
    assert listing.json()["items"][0]["input_preview"] == {"text": "first\nlast", "truncated": False}
    scripted_model.call("lookup", {}, call_id="call_lookup")
    await (await runs_kit.attempt(service))
    resumed = await service.client.post(
        f"{service.api}/runs/{run['id']}/resume",
        json={
            "approvals": {},
            "calls": {"call_lookup": {"status": "returned", "value": "hidden result"}},
            "input": {"content": [{"type": "text", "text": "resume input"}]},
        },
        headers=runs_kit.fresh_key(),
    )
    assert resumed.status_code == 201, resumed.text
    listing = await service.client.get(f"{service.api}/threads/{run['thread_id']}/runs")
    assert listing.json()["items"][0]["input_preview"] == {"text": "resume input", "truncated": False}


async def test_lineage_budget_continues_without_duplicates(service, scripted_model, runs_kit):
    from a13n_service.infra import reads

    agent = await runs_kit.create_agent(service, scripted_model)
    started = await runs_kit.start_thread(service, agent, "汉😀" * 100)
    ids = [started["run"]["id"]]
    for _ in range(14):
        await service.client.post(f"{service.api}/runs/{ids[-1]}/interrupt")
        added = await runs_kit.submit(service, started["thread"]["id"], runs_kit.message(agent, "汉😀" * 100))
        assert added.status_code == 201, added.text
        ids.append(added.json()["run"]["id"])
    seen = []
    cursor = None
    pages = 0
    while True:
        with statements(service) as sql:
            response = await service.client.get(
                f"{service.api}/runs/{ids[-1]}/lineage", params={"cursor": cursor} if cursor else {}
            )
        assert response.status_code == 200, response.text
        page = response.json()
        assert reads.size(page) < reads.BODY_CHARS
        assert sum("inbox_entries.payload" in query for query in sql) == 1
        seen.extend(item["id"] for item in page["items"])
        pages += 1
        cursor = page["next_cursor"]
        if cursor is None:
            break
        assert pages < len(ids)
    assert pages > 1
    assert seen == list(reversed(ids))


async def test_thread_counts_share_the_page_snapshot(service, scripted_model, runs_kit, monkeypatch):
    from a13n_service.runs import listings

    agent = await runs_kit.create_agent(service, scripted_model)
    started = await runs_kit.start_thread(service, agent, "first")
    run_id, thread_id = started["run"]["id"], started["thread"]["id"]
    await service.client.post(f"{service.api}/runs/{run_id}/interrupt")
    original = listings.views

    async def concurrent_insert(session, rows):
        values = await original(session, rows)
        added = await runs_kit.submit(service, thread_id, runs_kit.message(agent, "concurrent"))
        assert added.status_code == 201, added.text
        return values

    with monkeypatch.context() as patch:
        patch.setattr(listings, "views", concurrent_insert)
        response = await service.client.get(f"{service.api}/threads/{thread_id}/runs")
    assert response.status_code == 200, response.text
    page = response.json()
    assert [item["id"] for item in page["items"]] == [run_id]
    assert page["total"] == page["first_number"] == 1
    assert page["previous_cursor"] is None
    fresh = await service.client.get(f"{service.api}/threads/{thread_id}/runs")
    assert fresh.json()["total"] == 2
