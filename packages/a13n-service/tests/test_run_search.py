"""Lexical matching, retention/recovery and transactional search through the public lists."""

from datetime import UTC, datetime

import pytest
from a13n_service.infra.db import short_session, transaction
from a13n_service.runs.checkpoints import DisplayWrite, record_display
from a13n_service.runs.search import Index, SearchState, excerpt, seed, vector, words
from a13n_service.runs.tables import RunRow
from a13n_service.settings import RunSearch
from a13n_stream_protocol.display import Item
from sqlalchemy import event, func, select, update
from sqlalchemy.exc import DBAPIError


def item(ordinal: int, text: str, *, state: str = "completed", **content) -> Item:
    return Item(
        id=f"item_{ordinal}",
        ordinal=ordinal,
        kind="text_message",
        state=state,
        first_stream_id="1:1",
        last_stream_id="1:2",
        started_at=datetime.now(UTC),
        content={"role": "assistant", "text": text, **content},
    )


def test_chinese_normalization_identifiers_and_safe_excerpts() -> None:
    document = words("南京市长江大桥 HTTPRequest foo_bar \uff21\uff22\uff23", document=True)
    assert set(words("南京 长江大桥 abc", document=False)) <= set(document)
    assert {"http", "request", "httprequest", "foo", "bar", "foo_bar"} <= set(document)
    assert not words("!? & | : ", document=False)
    assert excerpt("甲乙丙丁", 4, 4) == ("甲", "丁")
    assert excerpt("hello", 4, 4) == ("hello",)
    assert all(len(term.encode()) < 2048 for term in words("x" * 3000, document=True))


def test_retention_reference_counts_recovery_and_uncommitted_candidates() -> None:
    limits = RunSearch(messages=8)
    initial = seed("firstinput common", "source", limits)
    pending = item(1, "unfinished", state="in_progress")
    first = initial.prepare([pending, item(2, "oldoutput common")], [])
    assert "unfinished" not in first.counts and "oldoutput" in first.counts
    assert "oldoutput" not in initial.counts  # A failed commit discards the candidate.
    items = [item(n, f"middleword{n} common") for n in range(3, 11)]
    retained = first.prepare(items, [])
    assert "oldoutput" not in retained.counts and "common" in retained.counts
    restored = Index(SearchState.model_validate_json(retained.state.model_dump_json()), RunSearch(messages=16))
    replay = restored.prepare([*items, item(1, "interruptedfinal", state="interrupted")], [])
    assert "oldoutput" not in replay.counts and "interruptedfinal" in replay.counts
    assert "firstinput" in replay.counts
    assert not replay.prepare([*items, item(1, "interruptedfinal", state="interrupted")], []).changed


def test_vector_budget_evicts_whole_assistant_messages_and_keeps_shared_terms() -> None:
    limits = RunSearch()
    # Force the vector limit with many unique bounded messages, without hitting the ledger count limit.
    index = seed("firstuser shared", "source", limits)
    items = [item(n, "shared " + " ".join(f"token{n}z{k}" for k in range(1800))) for n in range(1, 50)]
    result = index.prepare(items, [("latest", "latestuser")])
    assert result.bytes <= limits.vector_bytes
    assert len(result.messages) < len(items) + 2
    assert {"firstuser", "latestuser", "shared", "token49z1799"} <= result.counts.keys()
    assert "token1z0" not in result.counts


def test_user_only_budget_shrinks_old_excerpts_and_protects_first_and_latest() -> None:
    inputs = [(f"source{n}", " ".join(f"user{n}word{k}" for k in range(1000))) for n in range(100)]
    result = Index(SearchState(), RunSearch()).prepare([], inputs)
    assert result.bytes <= result.limits.vector_bytes
    assert result.messages["source0"].terms != result.messages["source0"].compact
    assert result.messages["source99"].terms != result.messages["source99"].compact
    assert any(message.terms == message.compact for message in result.state.messages)
    # A separate count cap bounds identical/repeated user text, preserving chronological order after shrinking.
    result = seed("first", "source0", RunSearch(messages=8)).prepare(
        [], [(f"source{n}", f"user{n}") for n in range(1, 12)]
    )
    assert list(result.messages) == ["source0", *[f"source{n}" for n in range(5, 12)]]
    result = result.prepare([], [("source12", "latest")])
    assert list(result.messages) == ["source0", *[f"source{n}" for n in range(6, 13)]]


def test_only_root_assistant_text_is_taken_from_display() -> None:
    index = Index(SearchState(), RunSearch()).prepare(
        [
            item(1, "uservisual", role="user"),
            item(2, "childsecret", subagentRunId="child"),
            item(3, "reasoningsecret").model_copy(update={"kind": "reasoning"}),
            item(4, "rootanswer"),
            item(5, "hiddenanswer", metadata={"display": False}),
        ],
        [("source", "realuser")],
    )
    assert set(index.counts) == {"realuser", "rootanswer"}


@pytest.mark.anyio
async def test_creation_completion_and_same_run_and_matching(service, scripted_model, runs_kit) -> None:
    agent = await runs_kit.create_agent(service, scripted_model)
    submitted = await runs_kit.start_thread(service, agent, "南京市长江大桥 HTTPRequest")
    run = submitted["run"]

    async def ids(resource: str, query: str, **params) -> list[str]:
        response = await service.client.get(f"{service.api}/{resource}", params={"text_query": query, **params})
        assert response.status_code == 200, response.text
        return [row["id"] for row in response.json()["items"]]

    for resource, key in (("runs", "id"), ("threads", "thread_id"), ("sessions", "session_id")):
        assert await ids(resource, "南京 request") == [run[key]]
        assert await ids(resource, "南京 missing") == []
        assert await ids(resource, "!!!") == []
    assert await ids("sessions", "南京", q=run["thread_id"]) == [run["session_id"]]
    assert await ids("runs", "南京", status="completed") == []
    vector_updates = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("UPDATE runs SET") and "search_vector=" in statement:
            vector_updates.append(statement)

    engine = service.runtime.storage.engine.sync_engine
    event.listen(engine, "before_cursor_execute", capture)
    try:
        scripted_model.say("检索结果已经完成 finalanswer")
        await (await runs_kit.attempt(service))
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert len(vector_updates) == 1  # The unchanged model boundary and seal add no extra vector write.
    assert await ids("runs", "南京 finalanswer", status="completed") == [run["id"]]
    assert await ids("runs", "南京 finalanswer", status="completed", view="compact") == [run["id"]]
    tail = await runs_kit.tail(service, run["id"])
    assert tail.search is not None
    async with short_session(service.runtime.storage) as session:
        actual = await session.scalar(select(func.pg_column_size(RunRow.search_vector)).where(RunRow.id == run["id"]))
    assert actual == Index(tail.search, service.runtime.settings.run_search).bytes
    with pytest.raises(DBAPIError, match="sealed run facts are immutable"):
        async with transaction(service.runtime.storage) as session:
            await session.execute(
                update(RunRow).where(RunRow.id == run["id"]).values(search_vector=vector(["tampered"]))
            )

    # A successor inherits model history, but only indexes its own new input/output.
    response = await service.client.post(
        f"{service.api}/threads/{run['thread_id']}/inbox",
        json=runs_kit.message(agent, "successorinput"),
        headers=runs_kit.fresh_key(),
    )
    assert response.status_code == 201, response.text
    assert await ids("runs", "南京") == [run["id"]]
    assert await ids("sessions", "南京 successorinput") == []


@pytest.mark.anyio
async def test_search_cursor_is_bound_to_text(service, scripted_model, runs_kit) -> None:
    agent = await runs_kit.create_agent(service, scripted_model)
    for _ in range(2):
        await runs_kit.start_thread(service, agent, "searchable")
    for resource, view in (("runs", "detailed"), ("runs", "compact"), ("threads", None), ("sessions", None)):
        projection = {"view": view} if view is not None else {}
        response = await service.client.get(
            f"{service.api}/{resource}", params={"text_query": "searchable", "limit": 1, **projection}
        )
        assert response.status_code == 200, response.text
        cursor = response.json()["next_cursor"]
        assert cursor
        next_page = await service.client.get(
            f"{service.api}/{resource}", params={"text_query": "searchable", "cursor": cursor}
        )
        assert next_page.status_code == 200 and len(next_page.json()["items"]) == 1
        changed = await service.client.get(
            f"{service.api}/{resource}", params={"text_query": "different", "cursor": cursor}
        )
        assert changed.status_code == 400


@pytest.mark.anyio
async def test_record_display_search_rollback(service, scripted_model, runs_kit) -> None:
    agent = await runs_kit.create_agent(service, scripted_model)
    submitted = await runs_kit.start_thread(service, agent, "originalinput")
    run_id = submitted["run"]["id"]
    # A model-boundary checkpoint provides a real published tail and an active fenced run.
    import asyncio

    scripted_model.say("finaltext", gate=asyncio.Event())
    task = await runs_kit.attempt(service)
    await scripted_model.request()
    await runs_kit.checkpointed(service, run_id)
    from a13n_service.runs.checkpoints import TailPointer

    try:
        with pytest.raises(RuntimeError, match="abort"):
            async with transaction(service.runtime.storage) as session:
                run = await session.get(RunRow, run_id)
                version = run.version
                record_display(
                    session,
                    run,
                    DisplayWrite(tail=TailPointer.model_validate(run.tail), pages=(), search_terms=("rollbackword",)),
                )
                await session.flush()
                await session.refresh(run)
                assert run.version == version
                raise RuntimeError("abort")
        result = await service.client.get(f"{service.api}/runs", params={"text_query": "rollbackword"})
        assert result.json()["items"] == []
        result = await service.client.get(f"{service.api}/runs", params={"text_query": "originalinput"})
        assert [row["id"] for row in result.json()["items"]] == [run_id]
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.anyio
async def test_search_is_scoped_to_workspace(service, scripted_model, runs_kit) -> None:
    agent = await runs_kit.create_agent(service, scripted_model)
    await runs_kit.start_thread(service, agent, "privateword")
    other = await service.client.post(f"{service.organization}/workspaces", json={"name": "Other"})
    assert other.status_code == 201, other.text
    for resource in ("runs", "threads", "sessions"):
        response = await service.client.get(
            f"{service.api}/{resource}",
            params={"text_query": "privateword"},
            headers={"x-workspace-id": other.json()["id"]},
        )
        assert response.status_code == 200 and response.json()["items"] == []


@pytest.mark.anyio
async def test_interrupted_stream_is_searchable_only_after_terminal_display(
    executing, scripted_model, runs_kit
) -> None:
    import asyncio

    agent = await runs_kit.create_agent(executing, scripted_model)
    scripted_model.say("partialkeyword", " neverfinished", interval=30)
    submitted = await runs_kit.start_thread(executing, agent, "inputkeyword")
    run_id, thread_id = submitted["run"]["id"], submitted["run"]["thread_id"]
    async with asyncio.timeout(10):
        while "partialkeyword" not in str(await executing.runtime.redis.xrange(f"a13n:thread:{thread_id}")):
            await asyncio.sleep(0.01)
    response = await executing.client.get(f"{executing.api}/runs", params={"text_query": "partialkeyword"})
    assert response.json()["items"] == []
    stopped = await executing.client.post(f"{executing.api}/runs/{run_id}/interrupt")
    assert stopped.status_code == 200, stopped.text
    assert (await runs_kit.sealed(executing, run_id))["status"] == "cancelled"
    response = await executing.client.get(f"{executing.api}/runs", params={"text_query": "partialkeyword"})
    assert [row["id"] for row in response.json()["items"]] == [run_id]
