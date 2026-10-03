from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import AsyncIterator
from dataclasses import replace

import pytest
from a13n_harness import (
    AgentSpec,
    HarnessBuilder,
    HarnessModelCharacteristics,
    HarnessState,
    RunBindings,
    StateStore,
    StoredRef,
)
from a13n_harness.capabilities.subagents import SUBAGENT_CAPABILITY_ID, InlineSubagentCollectionState
from a13n_harness.errors import StateError
from a13n_harness.state import AgentContextStateSnapshot, CapabilityState
from a13n_harness.storage import STORED, RunStorage
from pydantic_ai import BinaryContent
from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel

from .test_delegation import (
    _bindings_factory,
    _child_definition,
    _latest_user_text,
    _parent_definition,
    _previous_child_id,
    _returns_after_latest_user,
)

pytestmark = pytest.mark.anyio

LARGE = b"\x89PNG" + bytes(range(256)) * 4
SMALL = b"tiny"


class MemoryStore(StateStore):
    def __init__(self) -> None:
        super().__init__(content_threshold=512)
        self.saved: dict[str, tuple[str, bytes]] = {}

    async def save(self, data: bytes, kind: str) -> StoredRef:
        key = f"{kind}/{len(self.saved)}"
        self.saved[key] = (kind, data)
        return StoredRef(key=key, digest=hashlib.sha256(data).hexdigest(), size=len(data))

    async def load(self, ref: StoredRef) -> bytes:
        return self.saved[ref.key][1]

    def kinds(self) -> list[str]:
        return [kind for kind, _ in self.saved.values()]


def _messages(*contents: bytes) -> list[ModelMessage]:
    return [
        ModelRequest(
            parts=[
                UserPromptPart(["look", *(BinaryContent(data, media_type="image/png") for data in contents)]),
                ToolReturnPart(
                    tool_name="view",
                    tool_call_id="call-1",
                    content={"image": BinaryContent(LARGE, media_type="image/png", vendor_metadata={"keep": 1})},
                ),
            ]
        )
    ]


def _binaries(messages: list[ModelMessage] | tuple[ModelMessage, ...]) -> list[BinaryContent]:
    request = messages[0]
    assert isinstance(request, ModelRequest)
    prompt, returned = request.parts
    assert isinstance(prompt, UserPromptPart) and isinstance(returned, ToolReturnPart)
    return [item for item in prompt.content if isinstance(item, BinaryContent)] + [returned.content["image"]]


def _state(messages: list[ModelMessage], **entries: object) -> HarnessState:
    snapshot = AgentContextStateSnapshot(
        entries={key: CapabilityState(version="1", data=value) for key, value in entries.items()}
    )
    return HarnessState(schema_version="1", thread_id="thr_a", message_history=messages, agent_context_state=snapshot)


async def test_large_contents_export_once_as_references_and_resolve_back() -> None:
    store = MemoryStore()
    storage = RunStorage(store)
    # A Capability may hold input too, as steering retains it for replay after compaction.
    retained = ModelMessagesTypeAdapter.dump_python(_messages(LARGE), mode="json")
    state = await storage.export(_state(_messages(LARGE, SMALL), retained=retained))

    assert store.kinds() == ["content"]
    assert len(state.refs) == 1 and state.refs[0].size == len(LARGE)
    large, small, returned = _binaries(state.message_history)
    assert large.data == returned.data == b""
    assert large.vendor_metadata == {STORED: state.refs[0].model_dump()}
    assert returned.vendor_metadata == {"keep": 1, STORED: state.refs[0].model_dump()}
    assert small.data == SMALL
    assert base64.urlsafe_b64encode(LARGE).decode() not in state.model_dump_json()

    loader = RunStorage(store)
    resolved = await loader.resolve_messages(state.message_history)
    assert [item.data for item in _binaries(resolved)] == [LARGE, SMALL, LARGE]
    assert _binaries(resolved)[2].vendor_metadata == {"keep": 1}
    context = await loader.resolve_context(state.agent_context_state)
    restored = context.entries["retained"].data
    assert [item.data for item in _binaries(ModelMessagesTypeAdapter.validate_python(restored))] == [LARGE, LARGE]

    again = await storage.export(_state([*resolved, *_messages(LARGE)]))
    assert again.refs == state.refs
    assert store.kinds() == ["content"]


async def test_without_a_store_contents_stay_inline_and_references_cannot_load() -> None:
    state = _state(_messages(LARGE))
    assert await RunStorage(None).export(state) == state

    exported = await RunStorage(MemoryStore()).export(state)
    with pytest.raises(StateError) as error:
        await RunStorage(None).resolve_messages(exported.message_history)
    assert error.value.code == "state_store_missing"


async def test_values_only_shaped_like_binary_content_stay_as_they_are() -> None:
    # A tool may return any JSON: the first object is not binary content at all, and the second encodes its bytes in
    # the standard base64 alphabet, which Pydantic AI reads but never writes.
    lookalikes = [
        {"kind": "binary", "data": "010101"},
        {"kind": "binary", "data": base64.b64encode(LARGE).decode(), "media_type": "image/png"},
    ]
    returned = ToolReturnPart(tool_name="lookup", tool_call_id="call-2", content=lookalikes)
    messages = [*_messages(LARGE), ModelRequest(parts=[returned])]
    state = _state(messages, returned=lookalikes)

    assert await RunStorage(None).resolve_messages(messages) == tuple(messages)
    assert await RunStorage(None).resolve_context(state.agent_context_state) == state.agent_context_state

    store = MemoryStore()
    exported = await RunStorage(store).export(state)
    assert [ref.size for ref in exported.refs] == [len(LARGE)] and store.kinds() == ["content"]
    loader = RunStorage(store)
    resolved = await loader.resolve_messages(exported.message_history)
    # Saving and loading again changes nothing that serializing the state does not.
    plain = HarnessState.model_validate_json(state.model_dump_json())
    assert ModelMessagesTypeAdapter.dump_json(list(resolved)) == ModelMessagesTypeAdapter.dump_json(
        list(plain.message_history)
    )
    assert await loader.resolve_context(exported.agent_context_state) == state.agent_context_state


async def test_run_state_references_large_prompt_content_and_the_model_sees_it_again() -> None:
    seen: list[list[bytes]] = []

    async def respond(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del info
        seen.append(
            [
                item.data
                for message in messages
                if isinstance(message, ModelRequest)
                for part in message.parts
                if isinstance(part, UserPromptPart) and not isinstance(part.content, str)
                for item in part.content
                if isinstance(item, BinaryContent)
            ]
        )
        yield "done"

    store = MemoryStore()
    executable = HarnessBuilder().build(
        AgentSpec(model_characteristics=HarnessModelCharacteristics(image_input=None)),
        output_type=str,
        model=FunctionModel(stream_function=respond),
    )
    first = await executable.run(
        ["look", BinaryContent(LARGE, media_type="image/png")], bindings=RunBindings.embedded(state_store=store)
    )
    assert first.state is not None
    assert [ref.key for ref in first.state.refs] == ["content/0"]
    assert base64.urlsafe_b64encode(LARGE).decode() not in first.state.model_dump_json()

    second = await executable.run("again", previous_state=first.state, bindings=RunBindings.embedded(state_store=store))
    assert second.state is not None and second.state.refs == first.state.refs
    assert seen == [[LARGE], [LARGE]]
    assert store.kinds() == ["content"]


@pytest.mark.parametrize("with_store", [False, True])
async def test_a_run_keeps_the_state_fields_it_does_not_know(with_store: bool) -> None:
    async def respond(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        yield "done"

    executable = HarnessBuilder().build(AgentSpec(), output_type=str, model=FunctionModel(stream_function=respond))
    later = {"memory": {"note": "kept"}}
    previous = HarnessState.model_validate(HarnessState.new().model_dump(mode="json") | later)

    result = await executable.run(
        "hi", previous_state=previous, bindings=RunBindings.embedded(state_store=MemoryStore() if with_store else None)
    )
    assert result.state is not None and result.state.model_extra == later


async def test_inline_child_state_saves_to_the_store_and_a_fork_moves_it_on_load() -> None:
    async def parent_stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        if _returns_after_latest_user(messages):
            yield "parent-done"
            return
        execution_id = _previous_child_id(messages)
        name = "delegate" if execution_id is None else "resume_subagent"
        arguments = (
            {"subagent": "reviewer", "prompt": _latest_user_text(messages) or "continue"}
            if execution_id is None
            else {"execution_id": execution_id, "prompt": "continue"}
        )
        yield {0: DeltaToolCall(name=name, json_args=json.dumps(arguments), tool_call_id=f"{name}-{len(messages)}")}

    store = MemoryStore()
    executable = HarnessBuilder().build(
        _parent_definition(_child_definition(), FunctionModel(stream_function=parent_stream))
    )

    def bindings() -> RunBindings:
        return replace(_bindings_factory(), state_store=store)

    def child(state: HarnessState):
        registry = InlineSubagentCollectionState.model_validate(
            state.agent_context_state.entries[SUBAGENT_CAPABILITY_ID].data
        )
        return next(iter(registry.children.values()))

    first = await executable.run("start", bindings=bindings())
    assert first.state is not None
    saved = child(first.state)
    assert isinstance(saved.state, StoredRef)
    assert saved.state in first.state.refs
    assert store.kinds() == ["subagent_state"]

    forked = first.state.fork(thread_id="thr_fork")
    moved = child(forked)
    assert moved.state == saved.state and moved.child_thread_id != saved.child_thread_id

    resumed = await executable.run("resume", previous_state=forked, bindings=bindings())
    assert resumed.output_or_raise() == "parent-done"
    assert resumed.state is not None
    continued = child(resumed.state)
    assert isinstance(continued.state, StoredRef) and continued.state != saved.state
    assert continued.child_thread_id == moved.child_thread_id
    loaded = HarnessState.model_validate_json(store.saved[continued.state.key][1])
    assert loaded.thread_id == moved.child_thread_id
    assert "child-turn-2" in loaded.model_dump_json()

    original = HarnessState.model_validate_json(store.saved[saved.state.key][1])
    assert original.thread_id == saved.child_thread_id
