"""Real Environment files exercise document publication, replay, and erasure."""

import asyncio
from contextlib import asynccontextmanager

import pytest
from a13n_harness.providers.environment.direct_local.files import LocalFileOperator
from a13n_harness.providers.environment.direct_local.provider import _DirectLocalFilePolicy
from a13n_harness.providers.memory.documents import (
    Append,
    DocumentInput,
    Edit,
    MemoryDocumentError,
    Patch,
    Replace,
    Replacement,
)
from a13n_harness.providers.memory.filesystem.store import FilesystemMemoryStore

pytestmark = pytest.mark.anyio


class Coordinator:
    """Test Host, restricted to these writers; not a production distributed lock."""

    def __init__(self, files):
        self.files = files
        self.lock = asyncio.Lock()
        self.fail_after_head = False

    @asynccontextmanager
    async def transaction(self, store_id, scope):
        async with self.lock:
            yield self

    async def publish(self, path, text, *, expected):
        from a13n_harness.providers.environment.models import EnvironmentError

        try:
            current = await self.files.read_bytes(path)
        except EnvironmentError as error:
            assert error.code == "environment_not_found"
            current = None
        if current != expected:
            raise MemoryDocumentError("memory_conflict")
        await self.files.write_text(path, text, mode="create" if expected is None else "replace")
        if self.fail_after_head and "/heads/" in path:
            self.fail_after_head = False
            raise TimeoutError("transport interrupted after publication")


async def allow(_):
    pass


@pytest.fixture
async def store(tmp_path):
    files = LocalFileOperator(
        root=tmp_path,
        read_only=False,
        policy=_DirectLocalFilePolicy(max_value_bytes=1024 * 1024),
        mount_id="memory",
        generation="one",
    )
    coordinator = Coordinator(files)
    result = FilesystemMemoryStore(
        files=files,
        root="/memory",
        scope="thread:test",
        store_id="mstore_test",
        principal="user_test",
        authorize=allow,
        authorize_sources=allow,
        coordinator=coordinator,
    )
    await result.initialize()
    return result, coordinator


def content(text="# 部署\n使用 Python 3.13。\n", *, kind="semantic"):
    return DocumentInput(kind=kind, title="部署说明", description="运行环境", text=text, path=f"{kind}/deployment.md")


async def test_create_reconnect_search_and_exact_read(store):
    memory, _ = store
    saved = await memory.create_document(content(), request_key="create")
    replay = await memory.create_document(content(), request_key="create")
    assert replay.document == saved.document
    assert "部署说明" in (await memory.index()).text
    assert (await memory.search("Python", limit=5))[0].id == saved.document.id
    assert (await memory.search("部署", limit=5))[0].id == saved.document.id
    assert (await memory.read(saved.document.id)).text == content().text
    with pytest.raises(MemoryDocumentError, match="memory_conflict"):
        await memory.create_document(content("different"), request_key="create")


@pytest.mark.parametrize(
    "change",
    [
        Replace(type="replace", text="replacement"),
        Append(type="append", text="tail"),
        Edit(type="edit", edits=(Replacement(old_string="3.13", new_string="3.14"),)),
        Patch(type="patch", patch="@@ -2,1 +2,1 @@\n-使用 Python 3.13。\n+使用 Python 3.14。\n"),
    ],
)
async def test_revise_history_replay_and_conflict(store, change):
    memory, _ = store
    original = (await memory.create_document(content(), request_key="create")).document
    revised = await memory.revise(original.id, expected_version=1, change=change, request_key="revise")
    assert revised.document.version == 2
    assert (await memory.document(original.id, version=1)) == original
    assert (await memory.revise(original.id, expected_version=1, change=change, request_key="revise")) == revised
    with pytest.raises(MemoryDocumentError, match="memory_conflict"):
        await memory.revise(original.id, expected_version=1, change=change, request_key="another")


async def test_partial_edit_batch_leaves_original(store):
    memory, _ = store
    original = (await memory.create_document(content(), request_key="create")).document
    change = Edit(
        type="edit",
        edits=(Replacement(old_string="3.13", new_string="3.14"), Replacement(old_string="missing", new_string="bad")),
    )
    with pytest.raises(MemoryDocumentError, match="memory_edit_conflict"):
        await memory.revise(original.id, expected_version=1, change=change, request_key="invalid")
    assert await memory.document(original.id) == original


async def test_uncertain_publication_replay(store):
    memory, coordinator = store
    coordinator.fail_after_head = True
    with pytest.raises(MemoryDocumentError, match="memory_write_unconfirmed"):
        await memory.create_document(content(), request_key="create")
    original = (await memory.create_document(content(), request_key="create")).document
    coordinator.fail_after_head = True
    with pytest.raises(MemoryDocumentError, match="memory_write_unconfirmed"):
        await memory.revise(
            original.id, expected_version=1, change=Append(type="append", text="tail"), request_key="append"
        )
    saved = await memory.revise(
        original.id, expected_version=1, change=Append(type="append", text="tail"), request_key="append"
    )
    assert saved.document.text.count("tail") == 1


async def test_episode_immutable_and_ast_sections(store):
    memory, _ = store
    original = (
        await memory.create_document(
            content("# 标题\n```\n# not a heading\n```\n## 标题\n正文\n", kind="episodic"), request_key="create"
        )
    ).document
    headings = await memory.toc(original.id)
    assert len(headings) == 2
    assert headings[0].locator != headings[1].locator
    section = await memory.read_range(original.id, version=1, section=headings[1].locator)
    assert section.text == "## 标题\n正文\n"
    with pytest.raises(MemoryDocumentError, match="memory_revision_unsupported"):
        await memory.revise(
            original.id, expected_version=1, change=Append(type="append", text="bad"), request_key="revise"
        )


async def test_delete_erases_content_and_prevents_resurrection(store, tmp_path):
    memory, _ = store
    original = (await memory.create_document(content("SENSITIVE_ORIGINAL"), request_key="create")).document
    await memory.revise(
        original.id, expected_version=1, change=Replace(type="replace", text="SENSITIVE_NEW"), request_key="revise"
    )
    await memory.delete(original.id)
    await memory.delete(original.id)
    with pytest.raises(MemoryDocumentError, match="memory_not_found"):
        await memory.document(original.id, version=1)
    with pytest.raises(MemoryDocumentError, match="memory_not_found"):
        await memory.create_document(content("SENSITIVE_ORIGINAL"), request_key="create")
    assert all(b"SENSITIVE" not in path.read_bytes() for path in tmp_path.rglob("*") if path.is_file())


async def test_missing_marker_and_missing_coordinator_never_initialize(store):
    memory, coordinator = store
    memory.coordinator = None
    with pytest.raises(MemoryDocumentError, match="memory_write_unsupported"):
        await memory.create_document(content(), request_key="create")
    await coordinator.files.remove(memory._path(".internal", "store.json"))
    with pytest.raises(MemoryDocumentError, match="memory_storage_unavailable"):
        await memory.index()


async def test_file_tools_and_mem0_tools_coexist_and_route(store):
    import json

    from a13n_harness import AgentSpec, HarnessBuilder
    from a13n_harness.capabilities.memory import MemoryCapability, MemoryEntry
    from a13n_harness.providers.memory.mem0_platform import Mem0PlatformBackend
    from pydantic_ai.models.function import DeltaToolCall, FunctionModel
    from tests.test_memory import _FakeMem0Client

    memory, _ = store
    document = (await memory.create_document(content(), request_key="create")).document
    client = _FakeMem0Client()
    calls = 0

    async def model(messages, info):
        nonlocal calls
        names = {tool.name for tool in info.function_tools}
        assert "preferences_memory_search" in names
        assert "project_memory_revise" in names
        assert "project_memory_toc" in names
        assert "memory_search" not in names
        if calls == 0:
            calls += 1
            yield {
                0: DeltaToolCall(
                    name="project_memory_read",
                    json_args=json.dumps({"reference": f"memory://project/{document.id}", "version": 1}),
                    tool_call_id="read",
                )
            }
        elif calls == 1:
            assert "Python 3.13" in repr(messages)
            calls += 1
            yield {
                0: DeltaToolCall(
                    name="preferences_memory_add",
                    json_args=json.dumps({"text": "Prefer tea", "scope": "thread"}),
                    tool_call_id="save",
                )
            }
        else:
            yield "done"

    capability = MemoryCapability(
        entries=(
            MemoryEntry(
                "preferences", "records", "Personal facts", MemoryCapability(backend=Mem0PlatformBackend(client))
            ),
            MemoryEntry("project", "documents", "Project procedures", MemoryCapability(document_store=memory)),
        )
    )
    harness = HarnessBuilder().build(
        AgentSpec(), model=FunctionModel(stream_function=model), output_type=str, capabilities=(capability,)
    )
    result = await harness.run("Read the project and remember my preference")
    assert result.output_or_raise() == "done"
    assert len(client.add_calls) == 1
    assert len(await memory._documents()) == 1
    assert len(client.search_calls) == 1


async def test_concurrent_revisions_have_one_winner(store):
    memory, _ = store
    document = (await memory.create_document(content(), request_key="create")).document
    results = await asyncio.gather(
        *[
            memory.revise(
                document.id, expected_version=1, change=Append(type="append", text=f"tail{i}"), request_key=f"write{i}"
            )
            for i in range(2)
        ],
        return_exceptions=True,
    )
    assert sum(isinstance(result, MemoryDocumentError) for result in results) == 1
    assert (await memory.document(document.id)).version == 2


async def test_request_key_cannot_cross_operations(store):
    memory, _ = store
    document = (await memory.create_document(content(), request_key="create")).document
    with pytest.raises(MemoryDocumentError, match="memory_conflict"):
        await memory.revise(
            document.id, expected_version=1, change=Append(type="append", text="bad"), request_key="create"
        )
    assert (await memory.document(document.id)) == document


async def test_unchanged_revision_receipt_survives_later_write(store):
    memory, _ = store
    document = (await memory.create_document(content(), request_key="create")).document
    change = Replace(type="replace", text=document.text)
    unchanged = await memory.revise(document.id, expected_version=1, change=change, request_key="noop")
    assert unchanged.unchanged
    await memory.revise(document.id, expected_version=1, change=Append(type="append", text="tail"), request_key="next")
    assert (await memory.revise(document.id, expected_version=1, change=change, request_key="noop")) == unchanged


@pytest.mark.parametrize("boundary", ["/requests/", "/revisions/", "/commits/", "/heads/"])
async def test_creation_recovers_each_publication_boundary(store, boundary):
    memory, coordinator = store
    publish = coordinator.publish
    failed = False

    async def interrupted(path, text, *, expected):
        nonlocal failed
        await publish(path, text, expected=expected)
        if boundary in path and not failed:
            failed = True
            raise TimeoutError("lost acknowledgement")

    coordinator.publish = interrupted
    with pytest.raises(MemoryDocumentError, match="memory_write_unconfirmed"):
        await memory.create_document(content(), request_key="create")
    saved = await memory.create_document(content(), request_key="create")
    assert saved.document.version == 1
    assert (await memory.create_document(content(), request_key="create")) == saved


@pytest.mark.parametrize("boundary", ["/requests/", "/revisions/", "/commits/", "/heads/"])
async def test_revision_recovers_each_publication_boundary(store, boundary):
    memory, coordinator = store
    document = (await memory.create_document(content(), request_key="create")).document
    publish = coordinator.publish
    failed = False

    async def interrupted(path, text, *, expected):
        nonlocal failed
        await publish(path, text, expected=expected)
        if boundary in path and not failed:
            failed = True
            raise TimeoutError("lost acknowledgement")

    coordinator.publish = interrupted
    change = Append(type="append", text="unique tail")
    with pytest.raises(MemoryDocumentError, match="memory_write_unconfirmed"):
        await memory.revise(document.id, expected_version=1, change=change, request_key="append")
    saved = await memory.revise(document.id, expected_version=1, change=change, request_key="append")
    assert saved.document.text.count("unique tail") == 1
    assert (await memory.document(document.id)).version == 2


@pytest.mark.parametrize(
    "patch",
    [
        "@@ -2,2 +2,2 @@\n-使用 Python 3.13。\n+bad\n",
        "@@ -2,1 +8,1 @@\n-使用 Python 3.13。\n+bad\n",
        "--- /dev/null\n+++ ignored.md\n@@ -1,0 +1,1 @@\n+bad\n",
        "--- a.md\n+++ b.md\n@@ -2,1 +2,1 @@\n-使用 Python 3.13。\n+bad\n--- c.md\n+++ d.md\n",
    ],
)
async def test_malformed_patch_rejected_without_mutation(store, patch):
    memory, _ = store
    original = (await memory.create_document(content(), request_key="create")).document
    with pytest.raises(MemoryDocumentError, match="memory_patch_invalid"):
        await memory.revise(original.id, expected_version=1, change=Patch(type="patch", patch=patch), request_key="bad")
    assert await memory.document(original.id) == original


async def test_source_revocation_and_external_revision_edit_are_not_silently_trusted(store):
    memory, coordinator = store
    original = (await memory.create_document(content(), request_key="create")).document

    async def deny(_):
        raise MemoryDocumentError("memory_source_unavailable")

    memory.authorize_sources = deny
    with pytest.raises(MemoryDocumentError, match="memory_source_unavailable"):
        await memory.document(original.id)
    memory.authorize_sources = allow
    head, _ = await memory._head(coordinator.files, original.id)
    path = memory._path(".internal", "revisions", original.id, head.commit + ".md")
    raw = await coordinator.files.read_bytes(path)
    await coordinator.files.write_text(path, raw.decode().replace("3.13", "3.14"), mode="replace")
    with pytest.raises(MemoryDocumentError, match="memory_document_changed"):
        await memory.document(original.id)


async def test_multibyte_read_continuation_and_stale_locator(store):
    memory, _ = store
    original = (await memory.create_document(content("# 中文\n" + "汉字" * 20000), request_key="create")).document
    first = await memory.read_range(original.id, length=32768)
    assert len(first.text.encode()) <= 32768 and first.next_start is not None
    second = await memory.read_range(original.id, version=1, start=first.next_start, length=32768)
    assert (first.text + second.text) == original.text[: len(first.text + second.text)]
    heading = (await memory.toc(original.id))[0]
    await memory.revise(
        original.id, expected_version=1, change=Append(type="append", text="tail"), request_key="append"
    )
    with pytest.raises(MemoryDocumentError, match="memory_section_stale"):
        await memory.read_range(original.id, section=heading.locator)


@pytest.mark.parametrize("required", [True, False])
async def test_required_document_entry_fails_before_model_work(store, required):
    from a13n_harness import AgentSpec, HarnessBuilder
    from a13n_harness.capabilities.memory import MemoryCapability, MemoryEntry
    from pydantic_ai.models.function import FunctionModel

    memory, _ = store

    async def deny(_):
        raise MemoryDocumentError("memory_unavailable")

    memory.authorize = deny
    called = False

    async def model(messages, info):
        nonlocal called
        called = True
        assert "unavailable" in repr(messages)
        yield "done"

    capability = MemoryCapability(
        entries=(
            MemoryEntry(
                "project",
                "documents",
                "Project evidence",
                MemoryCapability(document_store=memory, recall_required=required),
            ),
        )
    )
    harness = HarnessBuilder().build(
        AgentSpec(), model=FunctionModel(stream_function=model), output_type=str, capabilities=(capability,)
    )
    if required:
        from a13n_harness.errors import RunError

        with pytest.raises(RunError, match="Required memory index is unavailable"):
            await harness.run("Read memory")
        assert not called
    else:
        assert (await harness.run("Read memory")).output_or_raise() == "done"


async def test_two_records_entries_keep_tools_instructions_and_writes_independent():
    import json

    from a13n_harness import AgentSpec, HarnessBuilder
    from a13n_harness.capabilities.memory import MemoryCapability, MemoryEntry
    from a13n_harness.providers.memory.mem0_platform import Mem0PlatformBackend
    from pydantic_ai.models.function import DeltaToolCall, FunctionModel
    from tests.test_memory import _FakeMem0Client

    clients = [_FakeMem0Client(), _FakeMem0Client()]
    calls = 0

    async def model(messages, info):
        nonlocal calls
        assert "First purpose" in info.instructions and "Second purpose" in info.instructions
        assert {"first_memory_search", "second_memory_search"} <= {tool.name for tool in info.function_tools}
        if calls == 0:
            calls += 1
            yield {
                0: DeltaToolCall(
                    name="second_memory_add",
                    json_args=json.dumps({"text": "only second", "scope": "thread"}),
                    tool_call_id="save",
                )
            }
        else:
            yield "done"

    entries = tuple(
        MemoryEntry(name, "records", purpose, MemoryCapability(backend=Mem0PlatformBackend(client)))
        for name, purpose, client in zip(("first", "second"), ("First purpose", "Second purpose"), clients, strict=True)
    )
    harness = HarnessBuilder().build(
        AgentSpec(),
        model=FunctionModel(stream_function=model),
        output_type=str,
        capabilities=(MemoryCapability(entries=entries),),
    )
    assert (await harness.run("remember")).output_or_raise() == "done"
    assert not clients[0].add_calls and len(clients[1].add_calls) == 1


async def test_named_document_reference_cannot_cross_entries(store):
    import json

    from a13n_harness import AgentSpec, HarnessBuilder
    from a13n_harness.capabilities.memory import MemoryCapability, MemoryEntry
    from pydantic_ai.models.function import DeltaToolCall, FunctionModel

    memory, _ = store
    document = (await memory.create_document(content(), request_key="create")).document
    calls = 0

    async def model(messages, info):
        nonlocal calls
        if not calls:
            calls += 1
            yield {
                0: DeltaToolCall(
                    name="first_memory_read",
                    json_args=json.dumps({"reference": f"memory://second/{document.id}"}),
                    tool_call_id="cross",
                )
            }
        else:
            assert "memory_entry_invalid" in repr(messages)
            yield "done"

    entries = tuple(
        MemoryEntry(name, "documents", "Project evidence", MemoryCapability(document_store=memory))
        for name in ("first", "second")
    )
    harness = HarnessBuilder().build(
        AgentSpec(),
        model=FunctionModel(stream_function=model),
        output_type=str,
        capabilities=(MemoryCapability(entries=entries),),
    )
    assert (await harness.run("read")).output_or_raise() == "done"


async def test_filesystem_definition_is_inert_and_requires_explicit_host_binding(store):
    from a13n_harness.providers.catalog import ProviderCatalog
    from a13n_harness.providers.memory.builtins import BUILT_IN_MEMORY_PROVIDERS
    from a13n_harness.providers.memory.filesystem.store import FilesystemMemoryBinding, open_filesystem_store

    memory, coordinator = store
    definition = ProviderCatalog(BUILT_IN_MEMORY_PROVIDERS)["filesystem"]
    assert definition.supports_documents and definition.supports_revisions
    assert not definition.supports_records and not definition.supports_changes
    config = definition.configuration_model.model_validate({})
    assert config.storage.root == "/memory"
    assert definition.credential_model is None
    with pytest.raises(TypeError, match="no record backend"):
        async with definition.open(config):
            pass
    binding = FilesystemMemoryBinding(
        files=memory.files,
        scope=memory.scope,
        store_id=memory.store_id,
        principal=memory.principal,
        authorize=allow,
        authorize_sources=allow,
        coordinator=coordinator,
    )
    with pytest.raises(MemoryDocumentError, match="memory_storage_binding_mismatch"):
        async with open_filesystem_store(
            config.model_copy(update={"storage": config.storage.model_copy(update={"root": "/elsewhere"})}),
            binding=binding,
        ):
            pass
    async with open_filesystem_store(config, binding=binding) as backend:
        result = await backend.create_document(content(), request_key="plugin")
        assert (await memory.document(result.document.id)) == result.document


async def test_navigation_uses_digest_bound_metadata_without_preloading_bodies(store):
    memory, coordinator = store
    saved = await memory.create_document(content(), request_key="create")
    original = coordinator.files.read_bytes
    reads = []

    async def tracked(path, **kwargs):
        reads.append(path)
        return await original(path, **kwargs)

    coordinator.files.read_bytes = tracked
    assert "部署说明" in (await memory.index()).text
    assert not any("/revisions/" in path for path in reads)
    await coordinator.files.remove(memory._path(".internal", "indexes", saved.document.id), recursive=True)
    assert "部署说明" in (await memory.index()).text
    assert any("/revisions/" in path for path in reads)


async def test_commit_remains_successful_when_derived_index_write_fails(store):
    memory, coordinator = store
    publish = coordinator.publish

    async def fail_index(path, text, *, expected):
        if "/indexes/" in path:
            raise TimeoutError("cache unavailable")
        await publish(path, text, expected=expected)

    coordinator.publish = fail_index
    saved = await memory.create_document(content(), request_key="create")
    assert not saved.indexed
    assert await memory.document(saved.document.id) == saved.document
    assert "部署说明" in (await memory.index()).text


@pytest.mark.parametrize(
    ("text", "patch", "expected"),
    [
        ("one\n", "@@ -0,0 +1,1 @@\n+first\n", "first\none\n"),
        ("one\n", "@@ -1,0 +2,1 @@\n+two\n", "one\ntwo\n"),
        ("one\ntwo\n", "@@ -1,1 +0,0 @@\n-one\n", "two\n"),
    ],
)
async def test_patch_empty_ranges_use_unified_diff_positions(store, text, patch, expected):
    memory, _ = store
    original = (await memory.create_document(content(text), request_key="seed")).document
    revised = await memory.revise(
        original.id, expected_version=1, change=Patch(type="patch", patch=patch), request_key="patch"
    )
    assert revised.document.text == expected


async def test_optional_document_factory_failure_keeps_other_entry_available():
    from a13n_harness import AgentSpec, HarnessBuilder
    from a13n_harness.capabilities.memory import MemoryCapability, MemoryEntry
    from a13n_harness.providers.memory.mem0_platform import Mem0PlatformBackend
    from pydantic_ai.models.function import FunctionModel
    from tests.test_memory import _FakeMem0Client

    async def unavailable(_):
        raise MemoryDocumentError("memory_storage_unavailable")

    async def model(messages, info):
        assert "Memory storage is unavailable" in repr(messages)
        names = {tool.name for tool in info.function_tools}
        assert "preferences_memory_search" in names
        assert not any(name.startswith("project_") for name in names)
        yield "done"

    capability = MemoryCapability(
        entries=(
            MemoryEntry("project", "documents", "Project facts", MemoryCapability(document_factory=unavailable)),
            MemoryEntry(
                "preferences",
                "records",
                "Personal facts",
                MemoryCapability(backend=Mem0PlatformBackend(_FakeMem0Client())),
            ),
        )
    )
    harness = HarnessBuilder().build(
        AgentSpec(), model=FunctionModel(stream_function=model), output_type=str, capabilities=(capability,)
    )
    assert (await harness.run("Recall preferences")).output_or_raise() == "done"


async def test_navigation_pagination_rejects_other_store_and_changed_head(store):
    memory, _ = store
    first = await memory.create_document(
        DocumentInput(kind="semantic", title="A", description="A", text="A", path="semantic/a.md"), request_key="a"
    )
    await memory.create_document(
        DocumentInput(kind="semantic", title="B", description="B", text="B", path="semantic/b.md"), request_key="b"
    )
    page, cursor = await memory.list_documents(limit=1)
    assert len(page) == 1 and cursor
    remaining, final = await memory.list_documents(cursor=cursor, limit=1)
    assert len(remaining) == 1 and final is None
    await memory.revise(
        first.document.id, expected_version=1, change=Append(type="append", text=" changed"), request_key="edit"
    )
    with pytest.raises(MemoryDocumentError, match="memory_cursor_invalid"):
        await memory.list_documents(cursor=cursor)


async def test_organization_replay_duplicate_defer_and_deletion_fence(store):
    from a13n_harness.providers.memory.filesystem.organization import (
        OrganizationCandidate,
        OrganizationPlan,
        apply_organization,
    )

    memory, _ = store
    value = DocumentInput(
        kind="semantic", title="Decision", description="Runtime", text="Use Python", path="semantic/runtime.md"
    )
    plan = OrganizationPlan(
        candidates=(
            OrganizationCandidate(decision="create", document=value, reason="Explicit decision", confidence=0.99),
            OrganizationCandidate(decision="create", document=value, reason="Uncertain", confidence=0.4),
        )
    )
    token = await memory.organization_token()
    assert token is None
    memory.organization_fence = (None,)
    first = await apply_organization(memory, plan, work_id="work", source="run://source")
    assert len(first.committed) == 1 and first.deferred == 1
    again = await apply_organization(memory, plan, work_id="work", source="run://source")
    assert again.ignored == 1 and len((await memory.list_documents())[0]) == 1
    memory.organization_fence = None
    await memory.delete(first.committed[0])
    memory.organization_fence = (None,)
    with pytest.raises(MemoryDocumentError, match="memory_organization_revoked"):
        await apply_organization(memory, plan, work_id="new-work", source="run://source")
    memory.organization_fence = None
    assert (await memory.list_documents())[0] == ()


async def test_organization_revision_preserves_sources_and_conflict_is_deferred(store):
    from a13n_harness.providers.memory.filesystem.organization import (
        OrganizationCandidate,
        OrganizationPlan,
        apply_organization,
    )

    memory, _ = store
    original = DocumentInput(
        kind="procedural",
        title="Deploy",
        description="Verified procedure",
        text="Step one",
        path="procedural/deploy.md",
        sources=("run://original",),
    )
    saved = await memory.create_document(original, request_key="original")
    proposal = original.model_copy(update={"text": "Step one\nStep two"})
    plan = OrganizationPlan(
        candidates=(
            OrganizationCandidate(
                decision="revise",
                document=proposal,
                reason="Verified refinement",
                confidence=0.99,
                document_id=saved.document.id,
                expected_version=1,
            ),
        )
    )
    result = await apply_organization(memory, plan, work_id="revision", source="run://refinement")
    revised = await memory.document(saved.document.id)
    assert result.committed == (saved.document.id,)
    assert revised.sources == ("run://original", "run://refinement") and revised.version == 2
    conflict = await apply_organization(memory, plan, work_id="stale-revision", source="run://refinement")
    assert conflict.deferred == 1
