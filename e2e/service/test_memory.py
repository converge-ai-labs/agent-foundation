"""Memory across processes: conversations on two workers editing one file, the changes their next run receives,
recovery from committed memory cursors after a worker crash, and a memory deleted during a run; record memories in
a fake mem0 server, recalled into a run's first input and written through the record tools, a recall that fails
without failing its run, and a deleted record memory's namespace purged through the outbox."""

import json
import re
import signal
from collections.abc import AsyncIterator, Iterator

import httpx2
import pytest

from dev.fixtures.process import fixture_process

from .api import eventually, expect
from .scripted import tool_results
from .stack import LEASE_SECONDS, read_rows

pytestmark = pytest.mark.anyio

CONTEXT = re.compile(
    r'<memory-context memory="([^"]+)" trust="untrusted" kind="(\w+)">\n[^\n]*\n(.*?)\n</memory-context>', re.S
)
RECALL = re.compile(r'<memory-recall memory="([^"]+)" trust="untrusted">\n[^\n]*\n(.*?)\n</memory-recall>', re.S)


@pytest.fixture
async def mem0() -> AsyncIterator[httpx2.AsyncClient]:
    """A fake self-hosted mem0 server of the journey's own, with a client of its fixture controls."""
    with fixture_process("dev.fixtures.mem0") as url:
        async with httpx2.AsyncClient(base_url=url, trust_env=False, timeout=10) as client:
            yield client


async def create_memory(api, name: str, files: dict[str, str], **fields: object) -> dict:  # type: ignore[no-untyped-def]
    memory = expect(await api.client.post("/api/v1/memories", json={"name": name, **fields}), 201)
    for path, content in files.items():
        body = {"path": path, "content": content}
        expect(await api.client.post(f"/api/v1/memories/{memory['id']}/files", json=body), 201)
    return memory


async def history(api, memory: dict, path: str) -> list[tuple[str, str | None, str | None]]:  # type: ignore[no-untyped-def]
    """A file's revisions, newest first, as (op, run, tool call)."""
    listing = await api.client.get(f"/api/v1/memories/{memory['id']}/revisions", params={"path": path})
    return [(item["op"], item["run_id"], item["tool_call_id"]) for item in expect(listing, 200)["items"]]


def user_parts(request: dict) -> Iterator[str]:
    for entry in request["body"]["messages"]:
        if entry["role"] == "user":
            content = entry["content"]
            yield from [content] if isinstance(content, str) else [part.get("text", "") for part in content]


def contexts(request: dict) -> list[tuple[str, str, dict]]:
    """The memory context blocks a model request carries, as (memory, kind, body)."""
    return [
        (match[1], match[2], json.loads(match[3])) for part in user_parts(request) for match in CONTEXT.finditer(part)
    ]


def recalls(request: dict) -> list[tuple[str, list[str]]]:
    """The recall blocks a model request carries, as (memory, recalled texts)."""
    return [
        (match[1], [record["text"] for record in json.loads(match[2])["records"]])
        for part in user_parts(request)
        for match in RECALL.finditer(part)
    ]


async def record_memory(api, mem0: httpx2.AsyncClient, name: str, *texts: str) -> dict:  # type: ignore[no-untyped-def]
    """A record memory in the journey's fake mem0 server, holding `texts`."""
    body = {"type": "mem0_oss", "name": name, "config": {"base_url": str(mem0.base_url)}}
    provider = expect(await api.client.post("/api/v1/memory-providers", json=body), 201)
    body = {"name": name, "type": "mem0_oss", "provider_id": provider["id"]}
    memory = expect(await api.client.post("/api/v1/memories", json=body), 201)
    for text in texts:
        expect(await api.client.post(f"/api/v1/memories/{memory['id']}/records", json={"text": text}), 201)
    return memory


async def namespace(mem0: httpx2.AsyncClient, memory: dict) -> list[str]:
    """What the fake mem0 server holds under the memory's namespace, even after the memory is gone."""
    return expect(await mem0.get(f"/fixture/namespaces/{memory['namespace']}"), 200)["texts"]


def result_of(request: dict, call_id: str) -> str:
    [content] = [str(entry["content"]) for entry in request["body"]["messages"] if entry.get("tool_call_id") == call_id]
    return content


def cursors(stack, run_id: str) -> dict[str, str | None]:  # type: ignore[no-untyped-def]
    """The memory cursors the run's last checkpoint committed, which the public run view omits."""
    [row] = read_rows(stack.database, "SELECT memory_cursors FROM runs WHERE id = :run_id", run_id=run_id)
    return row["memory_cursors"]


def workers_of(stack, run_id: str) -> list[str]:  # type: ignore[no-untyped-def]
    rows = read_rows(
        stack.database, "SELECT worker_id FROM run_attempts WHERE run_id = :run_id ORDER BY number", run_id=run_id
    )
    return [row["worker_id"] for row in rows]


@pytest.mark.isolated_service(worker_slots=1)
async def test_two_workers_edit_one_file_and_the_next_run_gets_the_changes(stack) -> None:  # type: ignore[no-untyped-def]
    api, model = stack.api, stack.model
    agent = await api.create_agent("Helper", await api.create_model(model.base_url))
    memory = await create_memory(api, "prefs", {"prefs.md": "- likes tea\n- reply in English\n"})
    mounts = [{"name": "prefs", "memory_id": memory["id"], "access": "write"}]
    view = {"memory": "prefs", "path": "prefs.md"}

    def edit(old: str, new: str) -> dict[str, str]:
        return {**view, "old_string": old, "new_string": new}

    # Both conversations view the file before either edits it; each edit then waits at its own gate.
    await model.call("memory_file_view", view, call_id="call_a_view", to="[mem-a]")
    await model.call("memory_file_edit", edit("tea", "coffee"), call_id="call_a_edit", to="[mem-a]", hold="a")
    await model.call("memory_file_edit", edit("English", "French"), call_id="call_a_stale", to="[mem-a]")
    await model.say("Coffee noted.", to="[mem-a]")
    await model.call("memory_file_view", view, call_id="call_b_view", to="[mem-b]")
    await model.call("memory_file_edit", edit("English", "Chinese"), call_id="call_b_edit", to="[mem-b]", hold="b")
    await model.say("Chinese noted.", to="[mem-b]")
    # A fills one worker's only slot; B must use the other without suspending A's heartbeats.
    a = await api.start(agent, "[mem-a] I like coffee now", memories=mounts)
    await model.arrived("[mem-a]", status="held")
    b = await api.start(agent, "[mem-b] Reply in Chinese from now on", memories=mounts)
    await model.arrived("[mem-b]", status="held")
    a_run, b_run = a["run"]["id"], b["run"]["id"]

    await model.open("b")
    assert (await api.sealed(b_run))["status"] == "completed"
    await model.open("a")
    assert (await api.sealed(a_run))["status"] == "completed"
    [a_worker] = workers_of(stack, a_run)
    [b_worker] = workers_of(stack, b_run)
    assert a_worker != b_worker

    # The disjoint edits both apply; the edit based on the stale view fails and shows the current content.
    current = expect(await api.client.get(f"/api/v1/memories/{memory['id']}/files/prefs.md"), 200)
    assert current["content"] == "- likes coffee\n- reply in Chinese\n"
    stale = result_of((await model.requests("[mem-a]"))[-1], "call_a_stale")
    assert "no_match" in stale and "reply in Chinese" in stale
    assert await history(api, memory, "prefs.md") == [
        ("update", a_run, "call_a_edit"),
        ("update", b_run, "call_b_edit"),
        ("create", None, None),
    ]
    a_first = (await model.requests("[mem-a]"))[0]
    assert [(name, kind) for name, kind, _ in contexts(a_first)] == [("prefs", "full")]
    # Each run committed the cursor of the context it received, before any write.
    assert cursors(stack, a_run) == cursors(stack, b_run) == {memory["id"]: "1"}

    # B's next run receives what changed since its context: both edits, listed once, not the whole index.
    await model.say("Nothing else.", to="[mem-b]")
    receipt = await api.send(b["thread"]["id"], agent, "[mem-b] Anything new?")
    assert (await api.sealed(receipt["run"]["id"]))["status"] == "completed"
    delivered = contexts((await model.requests("[mem-b]"))[-1])
    assert [(name, kind) for name, kind, _ in delivered] == [("prefs", "full"), ("prefs", "changes")]
    assert delivered[-1][2]["changed"] == ["prefs.md: - likes coffee"]
    assert cursors(stack, receipt["run"]["id"]) == {memory["id"]: "3"}


@pytest.mark.isolated_service
async def test_a_recovered_attempt_continues_from_the_committed_memory_cursors(stack) -> None:  # type: ignore[no-untyped-def]
    api, model = stack.api, stack.model
    agent = await api.create_agent("Helper", await api.create_model(model.base_url))
    memory = await create_memory(api, "notes", {"README.md": "# Notes\n"}, always_load=["README.md"])
    mounts = [{"name": "notes", "memory_id": memory["id"], "access": "write"}]
    entry = {"memory": "notes", "path": "log.md", "content": "- first entry\n"}
    await model.call("memory_file_create", entry, call_id="call_log", to="[crash]")
    # The worker dies while this request is outstanding; its answer never comes.
    await model.say("Lost with the worker.", to="[crash]", hold="never")
    doomed = stack.workers[0]
    with stack.only(doomed):
        receipt = await api.start(agent, "[crash] Log the first entry", memories=mounts)
        run_id = receipt["run"]["id"]
        await model.arrived("[crash]", status="held")

    async def committed() -> bool:
        items = (await api.items(run_id))["items"]
        return any(item["kind"] == "tool_call" and item["state"] == "completed" for item in items)

    await eventually(committed)
    assert cursors(stack, run_id) == {memory["id"]: "1"}

    doomed.signal(signal.SIGKILL)
    doomed.wait(5)
    await model.say("Recovered after the crash.", to="[crash]")
    run = await api.sealed(run_id, timeout=LEASE_SECONDS * 5)
    assert (run["status"], run["output"], run["attempts"]) == ("completed", "Recovered after the crash.", 2)
    requests = await model.requests("[crash]")
    assert [request["status"] for request in requests] == ["answered", "abandoned", "answered"]
    lost, retried = requests[1], requests[2]
    # The replacement restored the history holding the context with its cursor: nothing is delivered or written twice.
    assert contexts(retried) == contexts(lost) and [kind for _, kind, _ in contexts(retried)] == ["full"]
    assert len(tool_results(retried)) == 1
    assert await history(api, memory, "log.md") == [("create", run_id, "call_log")]
    assert cursors(stack, run_id) == {memory["id"]: "1"}


async def test_a_memory_deleted_during_a_run_refuses_its_next_call(stack) -> None:  # type: ignore[no-untyped-def]
    api, model = stack.api, stack.model
    agent = await api.create_agent("Helper", await api.create_model(model.base_url))
    memory = await create_memory(api, "todo", {"todo.md": "- buy milk\n"})
    mounts = [{"name": "todo", "memory_id": memory["id"], "access": "write"}]
    append = {"memory": "todo", "path": "todo.md", "content": "- call Sam\n"}
    await model.call("memory_file_append", append, call_id="call_append", to="[gone]", hold="gone")
    await model.say("The list is gone.", to="[gone]")
    receipt = await api.start(agent, "[gone] Add a todo", memories=mounts)
    await model.arrived("[gone]", status="held")

    path = f"/api/v1/memories/{memory['id']}"
    current = await api.client.get(path)
    expect(await api.client.delete(path, headers={"if-match": current.headers["etag"]}), 204)
    await model.open("gone")
    run = await api.sealed(receipt["run"]["id"])
    assert (run["status"], run["output"]) == ("completed", "The list is gone.")
    assert "memory_deleted" in result_of((await model.requests("[gone]"))[-1], "call_append")
    mounted = await api.client.get(f"/api/v1/threads/{receipt['thread']['id']}/memories")
    assert expect(mounted, 200)["items"] == []
    assert (await api.client.get(path)).status_code == 404


async def test_a_run_recalls_records_and_writes_through_the_record_tools(stack, mem0) -> None:  # type: ignore[no-untyped-def]
    api, model = stack.api, stack.model
    agent = await api.create_agent("Helper", await api.create_model(model.base_url))
    memory = await record_memory(api, mem0, "facts", "likes green tea", "lives in Lisbon")
    mounts = [{"name": "facts", "memory_id": memory["id"], "access": "write"}]
    add = {"memory": "facts", "text": "prefers window seats"}
    await model.call("memory_record_add", add, call_id="call_add", to="[rec]")
    await model.say("Noted.", to="[rec]")
    receipt = await api.start(agent, "[rec] Which tea do I like? I prefer window seats.", memories=mounts)
    run = await api.sealed(receipt["run"]["id"])
    assert (run["status"], run["output"]) == ("completed", "Noted.")

    first, second = await model.requests("[rec]")
    # Only the run's first input carries the records closest to it, as untrusted data.
    assert recalls(first) == [("facts", ["likes green tea"])]
    assert recalls(second) == recalls(first)
    assert '"id"' in result_of(second, "call_add")
    assert await namespace(mem0, memory) == ["likes green tea", "lives in Lisbon", "prefers window seats"]


async def test_a_failing_recall_does_not_fail_the_run(stack, mem0) -> None:  # type: ignore[no-untyped-def]
    api, model = stack.api, stack.model
    agent = await api.create_agent("Helper", await api.create_model(model.base_url))
    memory = await record_memory(api, mem0, "facts", "likes green tea")
    expect(await mem0.put("/fixture/failing", json={"operations": ["search"]}), 200)
    await model.say("No memories today.", to="[norecall]")
    mounts = [{"name": "facts", "memory_id": memory["id"], "access": "read"}]
    receipt = await api.start(agent, "[norecall] Which tea do I like?", memories=mounts)
    run = await api.sealed(receipt["run"]["id"])
    assert (run["status"], run["output"]) == ("completed", "No memories today.")
    [request] = await model.requests("[norecall]")
    assert recalls(request) == []


async def test_deleting_a_record_memory_purges_its_namespace_through_the_outbox(stack, mem0) -> None:  # type: ignore[no-untyped-def]
    api = stack.api
    memory = await record_memory(api, mem0, "facts", "likes green tea", "lives in Lisbon")
    kept = await record_memory(api, mem0, "other", "never purged")
    path = f"/api/v1/memories/{memory['id']}"
    expect(await api.client.delete(path, headers={"if-match": (await api.client.get(path)).headers["etag"]}), 204)

    async def purged() -> bool:
        return not await namespace(mem0, memory)

    await eventually(purged)
    assert await namespace(mem0, kept) == ["never purged"]
    rows = read_rows(
        stack.database,
        "SELECT dedupe_key, status FROM outbox WHERE kind = 'memory_purge' AND workspace_id = :workspace_id",
        workspace_id=api.tenant["workspace_id"],
    )
    assert rows == [{"dedupe_key": memory["id"], "status": "delivered"}]
