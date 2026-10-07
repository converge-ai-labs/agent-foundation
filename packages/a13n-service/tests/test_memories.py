"""Memories: the resource, its files and history through the API, and the PostgreSQL store runs write through."""

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from a13n_harness.capabilities import DEFAULT_FILE_GUIDE
from a13n_harness.providers.memory import Changes, FullResync, MemoryStoreError, Origin
from a13n_service.infra.audit import AuditEventRow
from a13n_service.infra.db import short_session
from a13n_service.infra.errors import ServiceError
from a13n_service.resources.memories import files
from a13n_service.resources.memories import service as memories
from a13n_service.resources.memories.schemas import MemoryCreate, MemoryFileCreate, MemoryFileReplace, MemoryUpdate
from a13n_service.resources.memories.store import PostgresFileStore
from a13n_service.resources.memories.tables import MemoryFileRevisionRow, MemoryFileRow, MemoryFileStoreRow
from a13n_service.settings import MemorySettings
from a13n_service.tenancy.authorize import BUILT_IN_ROLES, Grant, Principal
from sqlalchemy import event, select

pytestmark = pytest.mark.anyio

RUN = Origin(run_id="run_0123456789abcdef0123456789", principal_id=None, tool_call_id="call-1")


def etag(resource: dict[str, Any]) -> str:
    return f'"{resource["id"]}:{resource["version"]}"'


async def create_memory(service: SimpleNamespace, name: str = "team", **fields: Any) -> dict[str, Any]:
    created = await service.client.post(f"{service.api}/memories", json={"name": name, **fields})
    assert created.status_code == 201, created.text
    return created.json()


async def create_file(service: SimpleNamespace, memory: dict[str, Any], path: str, content: str) -> dict[str, Any]:
    created = await service.client.post(
        f"{service.api}/memories/{memory['id']}/files", json={"path": path, "content": content}
    )
    assert created.status_code == 201, created.text
    return created.json()


def store(service: SimpleNamespace, memory: dict[str, Any], **limits: Any) -> PostgresFileStore:
    settings = service.runtime.settings.memory.model_copy(update=limits)
    return PostgresFileStore(service.runtime.storage, memory["id"], settings)


async def revisions(service: SimpleNamespace, memory: dict[str, Any]) -> list[MemoryFileRevisionRow]:
    async with short_session(service.runtime.storage) as session:
        return list(
            (
                await session.scalars(
                    select(MemoryFileRevisionRow)
                    .where(MemoryFileRevisionRow.memory_id == memory["id"])
                    .order_by(MemoryFileRevisionRow.seq)
                )
            ).all()
        )


async def counters(service: SimpleNamespace, memory: dict[str, Any]) -> MemoryFileStoreRow:
    async with short_session(service.runtime.storage) as session:
        row = await session.get(MemoryFileStoreRow, memory["id"])
        assert row is not None
        return row


async def test_memories_are_configured_with_preconditions_and_guides(service) -> None:  # type: ignore[no-untyped-def]
    base = f"{service.api}/memories"
    memory = await create_memory(service, always_load=["README.md"], labels={"team": "a"})
    assert (memory["kind"], memory["type"], memory["guide"]) == ("file", "postgres", None)
    assert memory["inherited_guide"] == DEFAULT_FILE_GUIDE
    assert (memory["file_count"], memory["content_bytes"], memory["history_bytes"]) == (0, 0, 0)
    assert memory["always_load"] == ["README.md"]
    for always_load, field in ((["../x"], "always_load.0"), (["a.md", "a.md"], "always_load")):
        refused = await service.client.post(base, json={"name": "Bad", "always_load": always_load})
        assert refused.status_code == 400 and refused.json()["error"]["details"]["field"] == field
    oversized = await service.client.post(base, json={"name": "Big", "guide": "x" * 4097})
    assert oversized.status_code == 400 and oversized.json()["error"]["details"]["field"] == "guide"

    item = f"{base}/{memory['id']}"
    assert (await service.client.patch(item, json={"guide": "Keep it short."})).status_code == 428
    guided = await service.client.patch(item, json={"guide": "Keep it short."}, headers={"if-match": etag(memory)})
    assert guided.status_code == 200, guided.text
    assert (guided.json()["guide"], guided.json()["inherited_guide"]) == ("Keep it short.", DEFAULT_FILE_GUIDE)
    assert guided.headers["etag"] == etag(guided.json())
    silenced = await service.client.patch(item, json={"guide": ""}, headers={"if-match": etag(guided.json())})
    assert (silenced.json()["guide"], silenced.json()["inherited_guide"]) == ("", DEFAULT_FILE_GUIDE)
    inherited = await service.client.patch(item, json={"guide": None}, headers={"if-match": etag(silenced.json())})
    assert inherited.json()["guide"] is None
    unchanged = await service.client.patch(item, json={"name": "team"}, headers={"if-match": etag(inherited.json())})
    assert unchanged.json()["version"] == inherited.json()["version"]

    listed = await service.client.get(base, params={"label": "team:a"})
    assert [item["id"] for item in listed.json()["items"]] == [memory["id"]]
    assert (await service.client.delete(item, headers={"if-match": etag(memory)})).status_code == 412
    assert (await service.client.delete(item, headers={"if-match": etag(unchanged.json())})).status_code == 204
    assert (await service.client.get(item)).status_code == 404
    async with short_session(service.runtime.storage) as session:
        audit = (await session.scalars(select(AuditEventRow).where(AuditEventRow.target_id == memory["id"]))).all()
    assert [event.action for event in audit] == [
        "memory.create",
        "memory.update",
        "memory.update",
        "memory.update",
        "memory.delete",
    ]


async def test_the_default_guide_comes_from_startup_configuration(serve, settings) -> None:  # type: ignore[no-untyped-def]
    configured = MemorySettings.model_validate({"default_guide": {"file": "One fact per file."}})
    async with serve(settings=settings.model_copy(update={"memory": configured})) as service:
        memory = await create_memory(service)
        assert (memory["guide"], memory["inherited_guide"]) == (None, "One fact per file.")


async def test_files_change_under_their_etags_and_each_change_is_one_revision(service) -> None:  # type: ignore[no-untyped-def]
    memory = await create_memory(service)
    base = f"{service.api}/memories/{memory['id']}/files"
    created = await create_file(service, memory, "prefs.md", "---\ndescription: Preferences\n---\nlikes tea\n")
    assert created["description"] == "Preferences" and created["size"] == 43
    taken = await service.client.post(base, json={"path": "prefs.md", "content": "x"})
    assert taken.status_code == 409 and taken.json()["error"]["code"] == "already_exists"
    for body, status, field in (
        ({"path": "../prefs.md", "content": "x"}, 400, "path"),
        ({"path": "bad.md", "content": "---\ndescription: [\n---\n"}, 400, "content"),
        ({"path": "big.md", "content": "x" * 65537}, 413, None),
    ):
        refused = await service.client.post(base, json=body)
        assert refused.status_code == status, refused.text
        assert field is None or refused.json()["error"]["details"]["field"] == field

    item = f"{base}/prefs.md"
    read = await service.client.get(item)
    assert read.json()["content"].endswith("likes tea\n") and read.headers["etag"] == etag(created)
    assert (await service.client.put(item, json={"content": "likes coffee\n"})).status_code == 428
    replaced = await service.client.put(item, json={"content": "likes coffee\n"}, headers={"if-match": etag(created)})
    assert replaced.status_code == 200 and replaced.json()["id"] == created["id"]
    assert replaced.json()["description"] == "likes coffee"
    stale = await service.client.put(item, json={"content": "x\n"}, headers={"if-match": etag(created)})
    assert stale.status_code == 412
    same = await service.client.put(
        item, json={"content": "likes coffee\n"}, headers={"if-match": etag(replaced.json())}
    )
    assert same.json()["version"] == replaced.json()["version"]

    moved = await service.client.post(
        f"{base}/move",
        json={"source": "prefs.md", "destination": "user/prefs.md"},
        headers={"if-match": etag(same.json())},
    )
    assert moved.status_code == 200 and moved.json()["id"] == created["id"] and moved.json()["path"] == "user/prefs.md"
    await create_file(service, memory, "other.md", "other\n")
    blocked = await service.client.post(
        f"{base}/move", json={"source": "other.md", "destination": "user/prefs.md"}, headers={"if-match": '"x:1"'}
    )
    assert blocked.status_code == 412
    listing = await service.client.get(base, params={"prefix": "user/"})
    assert [entry["path"] for entry in listing.json()["items"]] == ["user/prefs.md"]
    assert "content" not in listing.json()["items"][0]
    first = await service.client.get(base, params={"limit": 1})
    rest = await service.client.get(base, params={"limit": 1, "cursor": first.json()["next_cursor"]})
    assert [entry["path"] for entry in first.json()["items"] + rest.json()["items"]] == ["other.md", "user/prefs.md"]

    deleted = await service.client.delete(f"{base}/user/prefs.md", headers={"if-match": etag(moved.json())})
    assert deleted.status_code == 204
    assert (await service.client.get(f"{base}/user/prefs.md")).status_code == 404

    history = [(row.seq, row.path, row.op, row.moved_path) for row in await revisions(service, memory)]
    assert history == [
        (1, "prefs.md", "create", None),
        (2, "prefs.md", "update", None),
        (3, "prefs.md", "move_out", "user/prefs.md"),
        (4, "user/prefs.md", "move_in", "prefs.md"),
        (5, "other.md", "create", None),
        (6, "user/prefs.md", "delete", None),
    ]
    totals = await counters(service, memory)
    assert (totals.seq, totals.file_count, totals.content_bytes) == (6, 1, len("other\n"))
    assert totals.history_bytes == 43 + 2 * len("likes coffee\n")
    async with short_session(service.runtime.storage) as session:
        audit = (await session.scalars(select(AuditEventRow).where(AuditEventRow.target_id == memory["id"]))).all()
    assert [event.action for event in audit][1:] == [
        "memory.file.create",
        "memory.file.update",
        "memory.file.move",
        "memory.file.create",
        "memory.file.delete",
    ]
    assert all("likes" not in str(event.details) for event in audit)


async def test_revisions_show_diffs_filter_by_run_and_restore_forward(service) -> None:  # type: ignore[no-untyped-def]
    memory = await create_memory(service)
    files_store = store(service, memory)
    version = await files_store.write("prefs.md", "likes tea\nreply in English\n", expected=None, origin=RUN)
    base = f"{service.api}/memories/{memory['id']}"
    edited = await create_file(service, memory, "notes.md", "a\n")
    replaced = await service.client.put(
        f"{base}/files/prefs.md",
        json={"content": "likes tea\nreply in Chinese\n"},
        headers={"if-match": f'"{edited["id"]}:{edited["version"]}"'},
    )
    assert replaced.status_code == 412
    current = (await service.client.get(f"{base}/files/prefs.md")).json()
    assert current["version"] == int(version) and current["updated_by_run_id"] == RUN.run_id
    replaced = await service.client.put(
        f"{base}/files/prefs.md", json={"content": "likes tea\nreply in Chinese\n"}, headers={"if-match": etag(current)}
    )

    page = (await service.client.get(f"{base}/revisions")).json()
    assert [(item["seq"], item["op"]) for item in page["items"]] == [(3, "update"), (2, "create"), (1, "create")]
    by_run = (await service.client.get(f"{base}/revisions", params={"run_id": RUN.run_id})).json()["items"]
    assert [(item["seq"], item["tool_call_id"]) for item in by_run] == [(1, "call-1")]
    older = await service.client.get(f"{base}/revisions", params={"limit": 2})
    tail = await service.client.get(f"{base}/revisions", params={"limit": 2, "cursor": older.json()["next_cursor"]})
    assert [item["seq"] for item in tail.json()["items"]] == [1]

    detail = (await service.client.get(f"{base}/revisions/3")).json()
    assert (detail["previous_content"], detail["content"]) == (
        "likes tea\nreply in English\n",
        "likes tea\nreply in Chinese\n",
    )
    assert detail["hunks"] == ["@@ -1,2 +1,2 @@\n likes tea\n-reply in English\n+reply in Chinese"]
    created = (await service.client.get(f"{base}/revisions/1")).json()
    assert created["previous_content"] is None and created["hunks"][0].startswith("@@ -0,0 +1,2 @@")

    restore = f"{base}/revisions/3/restore"
    assert (await service.client.post(restore)).status_code == 428
    restored = await service.client.post(restore, headers={"if-match": etag(replaced.json())})
    assert restored.status_code == 200, restored.text
    assert restored.json()["file"]["content"] == "likes tea\nreply in English\n"
    assert restored.headers["etag"] == etag(restored.json()["file"])
    removed = await service.client.post(f"{base}/revisions/1/restore", headers={"if-match": restored.headers["etag"]})
    assert removed.json() == {"path": "prefs.md", "file": None}
    recreated = await service.client.post(f"{base}/revisions/4/restore")
    assert recreated.json()["file"]["content"] == "likes tea\nreply in Chinese\n"
    assert (await service.client.post(f"{base}/revisions/1/restore", headers={"if-match": '"x:1"'})).status_code == 412
    assert [row.op for row in await revisions(service, memory)][3:] == ["update", "delete", "create"]
    assert (await service.client.get(f"{base}/revisions/99")).status_code == 404


async def test_history_is_pruned_per_file_then_oldest_first_within_the_total(service) -> None:  # type: ignore[no-untyped-def]
    memory = await create_memory(service)
    limited = store(service, memory, revisions_per_file=2, max_total_bytes=65536, max_file_bytes=16384)
    version = None
    for index in range(4):
        version = await limited.write("a.md", f"{index}\n" * 10, expected=version, origin=RUN)
    assert [row.seq for row in await revisions(service, memory)] == [3, 4]
    assert await limited.changes("2") == Changes(cursor="4", paths=("a.md",))
    assert await limited.changes("1") == FullResync(cursor="4")

    # Content that fits alone prunes history, oldest first, until content and history fit the total together.
    big = "x" * 16000 + "\n"
    first = await limited.write("b.md", big, expected=None, origin=RUN)
    second = await limited.write("c.md", big, expected=None, origin=RUN)
    await limited.write("d.md", big, expected=None, origin=RUN)
    await limited.write("b.md", big.replace("x", "y"), expected=first, origin=RUN)
    await limited.write("c.md", big.replace("x", "z"), expected=second, origin=RUN)
    totals = await counters(service, memory)
    assert totals.content_bytes + totals.history_bytes <= 65536
    assert [(row.seq, row.path) for row in await revisions(service, memory)] == [(9, "c.md")]
    assert totals.pruned_through_seq == 8
    await limited.write("e.md", big, expected=None, origin=RUN)
    assert [(row.seq, row.op) for row in await revisions(service, memory)] == [(10, "create")]
    with pytest.raises(MemoryStoreError) as full:
        await limited.write("f.md", big, expected=None, origin=RUN)
    assert full.value.code == "memory_full"
    assert (await counters(service, memory)).content_bytes == 20 + 4 * len(big)

    # Lowering a limit keeps content readable; the next write to a file enforces it.
    lowered = store(service, memory, max_file_bytes=1024, frontmatter_bytes=512)
    assert (await lowered.read("b.md")).text.startswith("y")
    current = await lowered.read("b.md")
    with pytest.raises(MemoryStoreError) as large:
        await lowered.write("b.md", current.text + "more\n", expected=current.version, origin=RUN)
    assert large.value.code == "too_large"
    await lowered.delete("b.md", expected=current.version, origin=RUN)


async def test_the_store_checks_each_write_against_the_version_read(service) -> None:  # type: ignore[no-untyped-def]
    memory = await create_memory(service)
    files_store = store(service, memory)
    v1 = await files_store.write("prefs.md", "likes tea\n", expected=None, origin=RUN)
    with pytest.raises(MemoryStoreError) as taken:
        await files_store.write("prefs.md", "x\n", expected=None, origin=RUN)
    assert taken.value.code == "version_mismatch" and taken.value.current.text == "likes tea\n"  # type: ignore[union-attr]
    v2 = await files_store.write("prefs.md", "likes coffee\n", expected=v1, origin=RUN)
    with pytest.raises(MemoryStoreError) as stale:
        await files_store.delete("prefs.md", expected=v1, origin=RUN)
    assert stale.value.current is not None and stale.value.current.version == v2
    with pytest.raises(MemoryStoreError) as gone:
        await files_store.write("missing.md", "x\n", expected=v1, origin=RUN)
    assert gone.value.code == "version_mismatch" and gone.value.current is None
    await files_store.write("archive/old.md", "old\n", expected=None, origin=RUN)
    with pytest.raises(MemoryStoreError) as occupied:
        await files_store.move("prefs.md", "archive/old.md", expected=v2, origin=RUN)
    assert occupied.value.code == "already_exists"
    moved = await files_store.move("prefs.md", "user/prefs.md", expected=v2, origin=RUN)
    assert [entry.path for entry in await files_store.list()] == ["archive/old.md", "user/prefs.md"]
    assert (await files_store.read("user/prefs.md")).version == moved
    with pytest.raises(MemoryStoreError) as missing:
        await files_store.read("prefs.md")
    assert missing.value.code == "not_found"

    # Concurrent writers are linearized by the memory's lock: change numbers stay gap-free.
    async def append(index: int) -> None:
        for _ in range(100):
            current = await files_store.read("user/prefs.md")
            try:
                await files_store.write(
                    "user/prefs.md", current.text + f"{index}\n", expected=current.version, origin=RUN
                )
                return
            except MemoryStoreError as error:
                assert error.code == "version_mismatch"
        raise AssertionError("never won the compare-and-swap")

    await asyncio.gather(*(append(index) for index in range(20)))
    lines = (await files_store.read("user/prefs.md")).text.splitlines()
    assert sorted(lines[1:]) == sorted(f"{index}" for index in range(20))
    assert (await counters(service, memory)).seq == 25
    kept = [row.seq for row in await revisions(service, memory) if row.path == "user/prefs.md"]
    assert kept == list(range(16, 26))


@pytest.mark.parametrize("write_before_read", [True, False])
async def test_change_feed_uses_one_snapshot_during_quota_pruning(service, runs_kit, write_before_read) -> None:  # type: ignore[no-untyped-def]
    await runs_kit.pause_sweeps(service)
    memory = await create_memory(service)
    files_store = store(service, memory, max_total_bytes=65536)
    await files_store.write("a.md", "x" * 32768, expected=None, origin=RUN)
    await files_store.write("a.md", "y" * 32768, expected="1", origin=RUN)
    statements: list[str] = []
    writing = False

    async def prune_with_write() -> None:
        nonlocal writing
        writing = True
        try:
            # The current files fit, but their history no longer does: revisions 1 and 2 are pruned.
            await files_store.write("b.md", "z" * 32768, expected=None, origin=RUN)
        finally:
            writing = False

    def interleave(conn, cursor, statement, parameters, context, executemany) -> None:  # type: ignore[no-untyped-def]
        if writing:
            return
        statements.append(statement)
        if len(statements) == 1:
            # The writer commits on a separate connection immediately before or after the reader's SQL.
            # After the SQL, its snapshot is fixed, even though the caller has not consumed the result yet.
            conn.connection.dbapi_connection.run_async(lambda connection: prune_with_write())

    engine = service.runtime.storage.engine.sync_engine
    hook = "before_cursor_execute" if write_before_read else "after_cursor_execute"
    event.listen(engine, hook, interleave)
    try:
        async with asyncio.timeout(5):
            result = await files_store.changes("1")
    finally:
        event.remove(engine, hook, interleave)

    expected = FullResync(cursor="3") if write_before_read else Changes(cursor="2", paths=("a.md",))
    assert result == expected
    assert len(statements) == 1
    assert (await counters(service, memory)).pruned_through_seq == 2
    assert await files_store.changes("1") == FullResync(cursor="3")
    assert await files_store.changes("2") == Changes(cursor="3", paths=("b.md",))
    assert await files_store.changes("3") == Changes(cursor="3", paths=())


async def test_the_change_feed_and_search(service) -> None:  # type: ignore[no-untyped-def]
    memory = await create_memory(service)
    files_store = store(service, memory)
    assert await files_store.changes(None) == FullResync(cursor="0")
    await files_store.write("notes/a.md", "f(x).y is literal\nSecond Line\n", expected=None, origin=RUN)
    version = await files_store.write("b.md", "fxxy\n", expected=None, origin=RUN)
    assert await files_store.changes("1") == Changes(cursor="2", paths=("b.md",))
    assert await files_store.changes("2") == Changes(cursor="2", paths=())
    assert await files_store.changes("9") == FullResync(cursor="2")
    assert await files_store.changes("x") == FullResync(cursor="2")
    await files_store.move("b.md", "c.md", expected=version, origin=RUN)
    assert await files_store.changes("2") == Changes(cursor="4", paths=("b.md", "c.md"))

    literal, more = await files_store.search("f(x).y", regex=False, case_sensitive=False, path="", limit=10)
    assert [(match.path, match.line) for match in literal] == [("notes/a.md", 1)] and not more
    folded, _ = await files_store.search("second", regex=False, case_sensitive=False, path="notes/", limit=10)
    assert [match.text for match in folded] == ["Second Line"]
    exact, _ = await files_store.search("second", regex=False, case_sensitive=True, path="", limit=10)
    assert exact == []
    pattern, more = await files_store.search("f.x", regex=True, case_sensitive=True, path="", limit=1)
    assert [match.path for match in pattern] == ["c.md"] and more
    with pytest.raises(MemoryStoreError) as invalid:
        await files_store.search("(", regex=True, case_sensitive=True, path="", limit=10)
    assert invalid.value.code == "invalid_pattern"
    with pytest.raises(MemoryStoreError) as directory:
        await files_store.search("x", regex=False, case_sensitive=True, path="notes", limit=10)
    assert directory.value.code == "invalid_path"

    purged = await service.client.delete(
        f"{service.api}/memories/{memory['id']}/revisions", params={"path": "notes/a.md"}
    )
    assert purged.json() == {"purged": 1}
    assert (await counters(service, memory)).pruned_through_seq == 1
    assert await files_store.changes("0") == FullResync(cursor="4")
    assert await files_store.changes("2") == Changes(cursor="4", paths=("b.md", "c.md"))


async def test_deleting_a_memory_deletes_its_store(service) -> None:  # type: ignore[no-untyped-def]
    memory = await create_memory(service)
    files_store = store(service, memory)
    await files_store.write("a.md", "a\n", expected=None, origin=RUN)
    deleted = await service.client.delete(f"{service.api}/memories/{memory['id']}", headers={"if-match": etag(memory)})
    assert deleted.status_code == 204
    for call in (files_store.list(), files_store.read("a.md"), files_store.changes(None)):
        with pytest.raises(MemoryStoreError) as gone:
            await call
        assert gone.value.code == "memory_deleted"
    with pytest.raises(MemoryStoreError) as write:
        await files_store.write("b.md", "b\n", expected=None, origin=RUN)
    assert write.value.code == "memory_deleted"
    async with short_session(service.runtime.storage) as session:
        assert (await session.scalars(select(MemoryFileRow).where(MemoryFileRow.memory_id == memory["id"]))).all() == []
        assert await revisions(service, memory) == []


async def test_verbs_follow_the_memory_rules(service) -> None:  # type: ignore[no-untyped-def]
    storage, settings, tenant = service.runtime.storage, service.runtime.settings.memory, service.tenant
    workspace_id = tenant.workspace_id

    def member(role: str) -> Principal:
        return Principal(
            tenant.principal_id, "user", (Grant(tenant.organization_id, workspace_id, BUILT_IN_ROLES[role]),)
        )

    viewer, runner, builder = member("viewer"), member("runner"), member("builder")
    memory = await memories.create_memory(storage, builder, workspace_id, MemoryCreate(name="Team"), settings=settings)
    for actor in (viewer, runner):
        with pytest.raises(ServiceError) as create:
            await memories.create_memory(storage, actor, workspace_id, MemoryCreate(name="X"), settings=settings)
        with pytest.raises(ServiceError) as guide:
            await memories.update_memory(
                storage,
                actor,
                workspace_id,
                memory.id,
                MemoryUpdate(guide="x"),
                if_match=f'"{memory.id}:{memory.version}"',
                settings=settings,
            )
        assert (create.value.code, guide.value.code) == ("forbidden", "forbidden")
    body = MemoryFileCreate(path="a.md", content="a\n")
    with pytest.raises(ServiceError) as viewing:
        await files.create_file(storage, viewer, workspace_id, memory.id, body, settings=settings)
    assert viewing.value.code == "forbidden"
    created = await files.create_file(storage, runner, workspace_id, memory.id, body, settings=settings)
    await files.replace_file(
        storage,
        runner,
        workspace_id,
        memory.id,
        "a.md",
        MemoryFileReplace(content="b\n"),
        if_match=f'"{created.id}:{created.version}"',
        settings=settings,
    )
    restored = await files.restore_revision(
        storage, runner, workspace_id, memory.id, 2, if_match=f'"{created.id}:2"', settings=settings
    )
    assert restored.file is not None and restored.file.content == "a\n"
    assert (await files.read_file(storage, viewer, workspace_id, memory.id, "a.md", settings=settings)).content == "a\n"
    with pytest.raises(ServiceError) as purge:
        await files.purge_history(storage, runner, workspace_id, memory.id, "a.md", settings=settings)
    assert purge.value.code == "forbidden"
    purged = await files.purge_history(storage, builder, workspace_id, memory.id, "a.md", settings=settings)
    assert purged.purged == 3
