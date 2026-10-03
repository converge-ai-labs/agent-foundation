"""A run's use of its Host state store.

Exported state references what the store saved instead of holding it: each inline subagent state once its child
ends, and each binary content part larger than the store's threshold, wherever the state holds it (its messages or
a Capability's state, such as retained steering input). A saved content part stays where it was as binary content
without data, its reference under `vendor_metadata["a13n.stored"]`, and loads again when a run restores the state:
Capability state when the run's context is created, messages before the history reaches the model. Without a
store, child states and content stay inside the state.
"""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Iterator, Sequence
from typing import Any

from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter

from a13n_harness.errors import StateError
from a13n_harness.state import AgentContextStateSnapshot, HarnessState, StateStore, StoredRef

STORED = "a13n.stored"


class RunStorage:
    def __init__(self, store: StateStore | None) -> None:
        self.store = store
        # Content this run saved or loaded, by the SHA-256 of its bytes: a part is saved once, not at every export.
        self._contents: dict[str, StoredRef] = {}

    async def save_state(self, state: HarnessState) -> StoredRef | HarnessState:
        if self.store is None:
            return state
        return await self.store.save(state.model_dump_json().encode(), "subagent_state")

    async def load_state(self, stored: StoredRef | HarnessState) -> HarnessState:
        if isinstance(stored, HarnessState):
            return stored
        return HarnessState.model_validate_json(await self._require().load(stored))

    async def export(self, state: HarnessState) -> HarnessState:
        """`state` with each large binary content part saved and referenced, and its saved contents in `refs`."""
        if self.store is None:
            return state
        value = state.model_dump(mode="json")
        refs = {ref.key: ref for ref in state.refs}
        for part in _binary_parts(value):
            ref = _reference(part)
            if ref is None and len(part["data"]) > self.store.content_threshold:
                data = base64.urlsafe_b64decode(part["data"])
                if len(data) > self.store.content_threshold:
                    ref = await self._save_content(data)
                    part["data"] = ""
                    part["vendor_metadata"] = {**(part["vendor_metadata"] or {}), STORED: ref.model_dump()}
            if ref is not None:
                refs[ref.key] = ref
        return HarnessState.model_validate({**value, "refs": [ref.model_dump() for ref in refs.values()]})

    async def resolve_messages(self, messages: Sequence[ModelMessage]) -> tuple[ModelMessage, ...]:
        """The messages with every saved content part loaded again."""
        value = ModelMessagesTypeAdapter.dump_python(list(messages), mode="json")
        if not await self._resolve(value):
            return tuple(messages)
        return tuple(ModelMessagesTypeAdapter.validate_python(value))

    async def resolve_context(self, snapshot: AgentContextStateSnapshot) -> AgentContextStateSnapshot:
        """The Capability state with every saved content part loaded again."""
        value = snapshot.model_dump(mode="json")
        if not await self._resolve(value):
            return snapshot
        return AgentContextStateSnapshot.model_validate(value)

    async def _resolve(self, value: Any) -> bool:
        """Load every saved content part of a JSON value in place; whether it had any."""
        stored = [(part, ref) for part in _binary_parts(value) if (ref := _reference(part)) is not None]
        for part, ref in stored:
            part["data"] = base64.urlsafe_b64encode(await self._load_content(ref)).decode()
            metadata = {key: item for key, item in part["vendor_metadata"].items() if key != STORED}
            part["vendor_metadata"] = metadata or None
        return bool(stored)

    async def _save_content(self, data: bytes) -> StoredRef:
        digest = hashlib.sha256(data).hexdigest()
        if (ref := self._contents.get(digest)) is None:
            ref = self._contents[digest] = await self._require().save(data, "content")
        return ref

    async def _load_content(self, ref: StoredRef) -> bytes:
        data = await self._require().load(ref)
        self._contents[hashlib.sha256(data).hexdigest()] = ref
        return data

    def _require(self) -> StateStore:
        if self.store is None:
            raise StateError("State references saved values but no state store is bound.", code="state_store_missing")
        return self.store


def _binary_parts(value: Any) -> Iterator[dict[str, Any]]:
    """Every binary content part of a JSON value, as Pydantic AI serializes one, wherever it nests."""
    pending: list[Any] = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            if item.get("kind") == "binary" and isinstance(item.get("data"), str):
                yield item
            else:
                pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)


def _reference(part: dict[str, Any]) -> StoredRef | None:
    stored = (part["vendor_metadata"] or {}).get(STORED)
    return StoredRef.model_validate(stored) if stored is not None else None
