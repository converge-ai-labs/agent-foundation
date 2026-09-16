"""Document mode reaches actual model context without preloading memory bodies."""

import json
from dataclasses import dataclass, field
from typing import Literal

import pytest
from a13n_harness import AgentSpec, HarnessBuilder
from a13n_harness.capabilities.memory import MemoryCapability
from a13n_harness.memory_documents import (
    MemoryDocumentContent,
    MemoryDocumentIndex,
    MemoryDocumentReference,
    MemoryDocumentStore,
)
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

pytestmark = pytest.mark.anyio


@dataclass
class Store(MemoryDocumentStore):
    reads: list[str] = field(default_factory=list)
    indexes: int = 0
    revoked: bool = False

    async def index(self, *, cursor=None):
        self.indexes += 1
        if self.revoked:
            raise PermissionError("Private provider diagnostic")
        return MemoryDocumentIndex("# MEMORY.md\n- [Release](memory://mdoc_release): Deployment checklist", "next")

    async def read(self, document_id):
        self.reads.append(document_id)
        if self.revoked:
            raise PermissionError("Private provider diagnostic")
        return MemoryDocumentContent(document_id, "Release", "BODY-ONLY-EVIDENCE")

    async def search(self, query, *, limit):
        return (MemoryDocumentReference("mdoc_release", "Release", "Deployment checklist"),)

    async def create(
        self, text, *, title, description, kind: Literal["daily", "long_term"], correction_of, request_key
    ):
        raise AssertionError("No write was requested")

    async def delete(self, document_id):
        raise AssertionError("No deletion was requested")


async def test_index_in_actual_model_context_and_body_only_after_read():
    store = Store()
    calls = 0

    async def model(messages, info):
        nonlocal calls
        assert {tool.name for tool in info.function_tools} == {
            "memory_index",
            "memory_read",
            "memory_search",
            "memory_add",
            "memory_forget",
        }
        schemas = {tool.name: tool.parameters_json_schema for tool in info.function_tools}
        assert "scope" not in schemas["memory_search"]["properties"]
        assert "provider_id" not in schemas["memory_add"]["properties"]
        if calls == 0:
            assert "MEMORY.md" in repr(messages) and "Deployment checklist" in repr(messages)
            assert "BODY-ONLY-EVIDENCE" not in repr(messages)
            assert store.reads == []
            calls += 1
            yield {
                0: DeltaToolCall(
                    name="memory_read",
                    json_args=json.dumps({"reference": "memory://mdoc_release"}),
                    tool_call_id="read",
                )
            }
        else:
            assert "BODY-ONLY-EVIDENCE" in repr(messages)
            yield "done"

    harness = HarnessBuilder().build(
        AgentSpec(),
        model=FunctionModel(stream_function=model),
        output_type=str,
        capabilities=(MemoryCapability(document_store=store),),
    )
    result = await harness.run("Read the release checklist")
    assert result.output_or_raise() == "done"
    assert store.reads == ["mdoc_release"]


@pytest.mark.parametrize("reference", ["../../secret", "file:///tmp/secret", "https://example.com", "MEMORY.md"])
async def test_document_tool_rejects_host_paths_and_urls(reference):
    store = Store()
    calls = 0

    async def model(messages, info):
        nonlocal calls
        if calls == 0:
            calls += 1
            yield {
                0: DeltaToolCall(name="memory_read", json_args=json.dumps({"reference": reference}), tool_call_id="bad")
            }
        else:
            assert '"ok":false' in str(messages).replace(" ", "") or "memory_unavailable" in repr(messages)
            yield "done"

    harness = HarnessBuilder().build(
        AgentSpec(),
        model=FunctionModel(stream_function=model),
        output_type=str,
        capabilities=(MemoryCapability(document_store=store),),
    )
    assert (await harness.run("Read")).output_or_raise() == "done"
    assert store.reads == []


async def test_revoked_index_is_explicit_and_old_link_reauthorizes():
    store = Store()
    calls = 0

    async def model(messages, info):
        nonlocal calls
        if calls == 0:
            assert "Deployment checklist" in repr(messages)
            store.revoked = True
            calls += 1
            yield {
                0: DeltaToolCall(name="memory_read", json_args='{"reference":"mdoc_release.md"}', tool_call_id="old")
            }
        else:
            assert "BODY-ONLY-EVIDENCE" not in repr(messages)
            assert "Private provider diagnostic" not in repr(messages)
            yield "done"

    harness = HarnessBuilder().build(
        AgentSpec(),
        model=FunctionModel(stream_function=model),
        output_type=str,
        capabilities=(MemoryCapability(document_store=store),),
    )
    assert (await harness.run("Read")).output_or_raise() == "done"
    assert store.reads == ["mdoc_release"]
    store.reads.clear()

    async def disabled_model(messages, info):
        assert "MEMORY.md is unavailable" in repr(messages)
        assert "Deployment checklist" not in repr(messages)
        yield "done"

    unavailable = HarnessBuilder().build(
        AgentSpec(),
        model=FunctionModel(stream_function=disabled_model),
        output_type=str,
        capabilities=(MemoryCapability(document_store=store),),
    )
    assert (await unavailable.run("Recall")).output_or_raise() == "done"


@pytest.mark.parametrize(
    ("read", "write", "names"),
    [
        (True, False, {"memory_index", "memory_read", "memory_search"}),
        (False, True, {"memory_add", "memory_forget"}),
    ],
)
async def test_document_modes_expose_only_allowed_operations(read, write, names):
    store = Store()

    async def model(messages, info):
        assert {tool.name for tool in info.function_tools} == names
        assert ("Deployment checklist" in repr(messages)) is read
        yield "done"

    harness = HarnessBuilder().build(
        AgentSpec(),
        model=FunctionModel(stream_function=model),
        output_type=str,
        capabilities=(MemoryCapability(document_store=store, document_read=read, document_write=write),),
    )
    assert (await harness.run("Hello")).output_or_raise() == "done"


@pytest.mark.parametrize("field", ["text", "next_cursor"])
async def test_index_budget_applies_after_context_encoding(field):
    class ExpandingStore(Store):
        async def index(self, *, cursor=None):
            values = {"text": "# MEMORY.md", "next_cursor": None, field: "<&>" * 4000}
            return MemoryDocumentIndex(**values)

    async def model(messages, info):
        assert "MEMORY.md is unavailable" in repr(messages)
        assert "\\\\u003c" not in repr(messages)
        yield "done"

    harness = HarnessBuilder().build(
        AgentSpec(),
        model=FunctionModel(stream_function=model),
        output_type=str,
        capabilities=(MemoryCapability(document_store=ExpandingStore()),),
    )
    assert (await harness.run("Recall")).output_or_raise() == "done"


async def test_index_tool_uses_same_budget_as_initial_context():
    class ExpandingPageStore(Store):
        async def index(self, *, cursor=None):
            if cursor is None:
                return await super().index()
            assert cursor == "next"
            return MemoryDocumentIndex("<" * 6000)

    calls = 0

    async def model(messages, info):
        nonlocal calls
        if calls == 0:
            assert "Deployment checklist" in repr(messages)
            calls += 1
            yield {0: DeltaToolCall(name="memory_index", json_args='{"cursor":"next"}', tool_call_id="page")}
        else:
            assert "memory_unavailable" in repr(messages)
            assert "<" * 6000 not in repr(messages)
            yield "done"

    harness = HarnessBuilder().build(
        AgentSpec(),
        model=FunctionModel(stream_function=model),
        output_type=str,
        capabilities=(MemoryCapability(document_store=ExpandingPageStore()),),
    )
    assert (await harness.run("Continue the memory index")).output_or_raise() == "done"


async def test_index_encoding_counts_utf8_envelope_and_preserves_navigation():
    empty = MemoryDocumentIndex("").render_context()
    available = 32 * 1024 - len(empty.encode())
    assert len(MemoryDocumentIndex("a" * available).render_context().encode()) == 32 * 1024
    with pytest.raises(ValueError, match="context budget"):
        MemoryDocumentIndex("a" * (available + 1)).render_context()
    with pytest.raises(ValueError, match="context budget"):
        MemoryDocumentIndex("界" * (available // 3 + 1)).render_context()
    original = MemoryDocumentIndex('# MEMORY.md\n[<发布> & "ops"](memory://mdoc_release)', "next")
    content = original.render_context()
    payload = content.split("\n", 2)[2].rsplit("\n", 1)[0]
    assert "<发布>" not in content
    assert json.loads(payload) == {"text": original.text, "next_cursor": "next"}
