"""Opt-in bounded reads through the authenticated HTTP and MCP surfaces."""

import json

import pytest

from .mcp_support import call as mcp_call
from .mcp_support import client as mcp_client
from .mcp_support import key as mcp_key

pytestmark = pytest.mark.anyio


async def test_run_summaries_budget_paging_sections_and_original_etag(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    agent = await runs_kit.create_agent(service, scripted_model)
    accepted = [await runs_kit.start_thread(service, agent, "汉字" * 1000) for _ in range(12)]
    expected = [value["run"]["id"] for value in reversed(accepted)]
    cursor, found = None, []
    while True:
        params = {"agent_id": agent["id"], "limit": 12}
        if cursor:
            params["cursor"] = cursor
        response = await service.client.get(f"{service.api}/runs", params=params)
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(response.text) < 12000 and len(body["items"]) < 12
        assert all(value["input_preview"]["truncated"] and "options" not in value for value in body["items"])
        found.extend(value["id"] for value in body["items"])
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert found == expected
    run_id = expected[0]
    full = await service.client.get(f"{service.api}/runs/{run_id}")
    short = await service.client.get(f"{service.api}/runs/{run_id}", params={"view": "compact"})
    assert full.headers["etag"] == short.headers["etag"] and "options" in full.json()
    fragment = await service.client.get(f"{service.api}/runs/{run_id}", params={"section": "input", "limit": 100})
    assert fragment.status_code == 200, fragment.text
    part = fragment.json()
    assert part["representation"] == "fragment" and part["has_more"]
    missing_version = await service.client.get(
        f"{service.api}/runs/{run_id}", params={"section": "input", "offset": 100}
    )
    assert missing_version.status_code == 400
    thread_id = accepted[-1]["thread"]["id"]
    inbox = await service.client.get(
        f"{service.api}/threads/{thread_id}/inbox", params={"view": "compact", "kind": "message"}
    )
    assert inbox.status_code == 200 and inbox.json()["items"][0]["payload"]["truncated"], inbox.text
    entry = inbox.json()["items"][0]
    detail = await service.client.get(
        f"{service.api}/threads/{thread_id}/inbox/{entry['id']}", params={"section": "payload"}
    )
    assert detail.status_code == 200 and json.loads(detail.json()["text"]) == full.json()["input"], detail.text


async def test_revision_summaries_sections_compare_and_configuration_roundtrip(
    service, scripted_model, runs_kit
) -> None:  # type: ignore[no-untyped-def]
    agent = await runs_kit.create_agent(service, scripted_model, instructions="original\n" * 2000)
    base = f"{service.api}/agents/{agent['id']}"
    original = await service.client.get(f"{base}/revisions/{agent['default_revision_id']}")
    assert original.status_code == 200
    config = original.json()["config"]
    edited = {**config, "instructions": "changed instructions"}
    head = await service.client.get(base)
    created = await service.client.post(
        f"{base}/revisions", json={"config": edited, "note": "note" * 300}, headers={"if-match": head.headers["etag"]}
    )
    assert created.status_code == 201, created.text
    revision_id = created.json()["id"]
    listed = await service.client.get(f"{base}/revisions", params={"view": "compact"})
    assert listed.status_code == 200, listed.text
    assert [value["is_default"] for value in listed.json()["items"]] == [True, False]
    assert listed.json()["items"][0]["note"]["truncated"]
    parts, offset, version = [], 0, None
    while True:
        params = {"section": "all", "offset": offset}
        if version:
            params["version"] = version
        response = await service.client.get(f"{base}/revisions/{agent['default_revision_id']}", params=params)
        assert response.status_code == 200, response.text
        part = response.json()
        parts.append(part["text"])
        if not part["has_more"]:
            break
        offset, version = part["next_offset"], part["version"]
    restored = json.loads("".join(parts))
    assert restored == config
    diff = await service.client.get(
        f"{base}/revisions/{revision_id}/compare",
        params={
            "base_revision_id": agent["default_revision_id"],
            "section": "instructions",
            "query": "changed instructions",
        },
    )
    assert diff.status_code == 200 and '+"changed instructions"' in diff.json()["text"], diff.text
    started = await runs_kit.start_thread(service, {**agent, "default_revision_id": revision_id}, "hello")
    summary = await service.client.get(f"{service.api}/runs/{started['run']['id']}/agent", params={"view": "compact"})
    assert summary.status_code == 200 and "revision" not in summary.json(), summary.text
    restored["instructions"] = "Edited after reconstructing the complete configuration"
    head = await service.client.get(base)
    saved = await service.client.post(
        f"{base}/revisions", json={"config": restored}, headers={"if-match": head.headers["etag"]}
    )
    assert saved.status_code == 201, saved.text
    assert saved.json()["config"] == restored


async def test_toolset_index_definitions_and_mcp_share_exact_http_representation(service) -> None:  # type: ignore[no-untyped-def]
    index = await service.client.get(f"{service.api}/toolsets/index", params={"query": "files", "limit": 10})
    assert index.status_code == 200, index.text
    assert "files" in [item["name"] for item in index.json()["items"]] and "config_schema" not in index.text
    definitions = await service.client.get(
        f"{service.api}/toolsets/definitions", params=[("name", "files"), ("name", "missing")]
    )
    assert definitions.status_code == 200 and definitions.json()["missing"] == ["missing"], definitions.text
    async with mcp_client(service, await mcp_key(service)) as http:
        result = await mcp_call(http, service, "get", "/toolsets/index", query="files", limit=10)
    assert result["status"] == 200 and result["body"] == index.json()
    unauthorized = await service.client.get("/api/v1/toolsets/index", headers={"X-Workspace-ID": "ws_" + "f" * 20})
    assert unauthorized.status_code in {403, 404}


async def test_compact_items_strict_limit_filters_and_versioned_field_reads(service, scripted_model, runs_kit) -> None:  # type: ignore[no-untyped-def]
    agent = await runs_kit.create_agent(service, scripted_model)
    started = await runs_kit.start_thread(service, agent, "hello")
    scripted_model.say("answer " * 1000)
    await (await runs_kit.attempt(service))
    path = f"{service.api}/runs/{started['run']['id']}/items"
    full = await service.client.get(path)
    assert full.status_code == 200
    expected = [item["id"] for item in full.json()["items"]]
    found, after = [], 0
    while True:
        page = await service.client.get(path, params={"view": "compact", "limit": 1, "after": after})
        assert page.status_code == 200, page.text
        body = page.json()
        assert len(body["items"]) <= 1 and body["run"]["view"] == "compact"
        found.extend(item["id"] for item in body["items"])
        if body["next_after"] is None:
            break
        assert body["next_after"] > after
        after = body["next_after"]
    assert found == expected
    filtered = await service.client.get(
        path, params={"view": "compact", "kind": "text_message", "state": "completed", "limit": 10}
    )
    assert filtered.status_code == 200 and filtered.json()["items"], filtered.text
    item = next(item for item in filtered.json()["items"] if item["text"]["truncated"])
    selected = await service.client.get(
        f"{path}/{item['id']}/content", params={"ordinal": item["ordinal"], "field": "text", "limit": 100}
    )
    assert selected.status_code == 200 and selected.json()["has_more"], selected.text
    wrong = await service.client.get(f"{path}/wrong/content", params={"ordinal": item["ordinal"], "field": "text"})
    assert wrong.status_code == 404


async def test_filtered_item_windows_advance_through_empty_pages_and_backwards(
    service, scripted_model, runs_kit, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    from a13n_service.infra.db import transaction
    from a13n_service.runs import checkpoints
    from a13n_service.runs.checkpoints import TailPointer
    from a13n_service.runs.display import DisplayFold, Tail
    from a13n_service.runs.tables import RunRow

    agent = await runs_kit.create_agent(service, scripted_model)
    run_id = (await runs_kit.start_thread(service, agent, "start"))["run"]["id"]
    fold = DisplayFold(run_id, Tail(), attempt=1, page_items=500, page_bytes=1048576)
    for index in range(105):
        fold.fold(
            [
                {"type": "TEXT_MESSAGE_START", "messageId": f"m{index}", "role": "assistant"},
                {"type": "TEXT_MESSAGE_CONTENT", "messageId": f"m{index}", "delta": "active"},
            ]
        )
    frozen = fold.snapshot().tail
    pointer = TailPointer(
        key="test-tail", digest="0" * 64, size=0, format=1, position=frozen.position, first=1, count=105
    )
    async with transaction(service.runtime.storage) as session:
        row = await session.get(RunRow, run_id)
        assert row is not None
        row.tail = pointer.model_dump(mode="json")

    async def load_tail(objects, selected):  # type: ignore[no-untyped-def]
        assert selected == pointer
        return frozen.model_copy(deep=True)

    monkeypatch.setattr(checkpoints, "load_tail", load_tail)
    path = f"{service.api}/runs/{run_id}/items"
    original = await service.client.get(path, params={"limit": 2})
    assert len(original.json()["items"]) == 105
    empty = await service.client.get(path, params={"view": "compact", "kind": "tool_call", "limit": 2})
    assert empty.json()["items"] == [] and empty.json()["next_after"] == 100, empty.text
    end = await service.client.get(path, params={"view": "compact", "kind": "tool_call", "limit": 2, "after": 100})
    assert end.json()["items"] == [] and end.json()["next_after"] is None, end.text
    before, found = 106, []
    while before is not None:
        response = await service.client.get(
            path, params={"view": "compact", "kind": "text_message", "limit": 20, "before": before}
        )
        assert response.status_code == 200 and len(response.text) < 12000, response.text
        page = response.json()
        ordinals = [item["ordinal"] for item in page["items"]]
        assert ordinals == sorted(ordinals)
        found = ordinals + found
        before = page["next_before"]
    assert found == list(range(1, 106))
