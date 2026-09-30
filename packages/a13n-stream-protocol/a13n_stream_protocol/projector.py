"""Shared native-message projection and incremental display semantics.

Stable native addresses, not text equality, join live parts to checkpoint history.
Hosts own capture timing and retention policy, not a second semantic event fold.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from itertools import chain
from typing import Any

from a13n_harness.capabilities.context import ContextRestoredEvent
from a13n_harness.events import HarnessExtensionEvent, InlineDelegationPayload
from a13n_harness.model_context import ModelInputEvent, user_prompt_content
from pydantic import JsonValue, TypeAdapter
from pydantic_ai.messages import (
    AgentStreamEvent,
    CapabilityEvent,
    CompactionPart,
    FilePart,
    FunctionToolResultEvent,
    ModelMessage,
    ModelResponse,
    NativeToolCallPart,
    NativeToolReturnPart,
    OutputToolResultEvent,
    PartDeltaEvent,
    PartEndEvent,
    PartStartEvent,
    RetryPromptPart,
    SpeechPart,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
    ToolCallPart,
    ToolCallPartDelta,
    ToolReturnPart,
    UnknownCapabilityEvent,
    UserPromptPart,
)

from a13n_stream_protocol.display import (
    BlockAppend,
    BlockKind,
    BlockPut,
    BlocksRemove,
    BlockStatus,
    DisplayBlock,
    DisplayDelta,
    DisplayOperation,
    DisplayPosition,
    DisplayScope,
    DisplaySnapshot,
    DisplayState,
    ScopePut,
)
from a13n_stream_protocol.messages import project_input_content

_JSON = TypeAdapter(JsonValue)


def _json(value: Any) -> JsonValue:
    return _JSON.validate_python(value)


class DisplayProjector:
    """Single-owner projection with a nonblocking delivery callback.

    A callback sees already-applied batches. Delivery failure must be handled by
    the Host as loss of live delivery, not by rolling back producer state.
    """

    def __init__(
        self,
        snapshot: DisplaySnapshot,
        *,
        publish: Callable[[DisplayDelta], None] | None = None,
        max_blocks: int | None = None,
        max_bytes: int | None = None,
        max_field_chars: int | None = None,
        ignored_capabilities: frozenset[str] = frozenset(),
        batch: bool = False,
    ) -> None:
        self.state = DisplayState(snapshot)
        self._publish = publish
        self._batch = batch
        self._pending: list[DisplayOperation] = []
        self._pending_bytes = 0
        self._ignored_capabilities = ignored_capabilities
        self._max_blocks = max_blocks
        self._max_bytes = max_bytes
        self._max_field_chars = max_field_chars
        if any(limit is not None and limit < 1 for limit in (max_blocks, max_bytes, max_field_chars)):
            raise ValueError("Display retention limits must be positive")
        self._sizes = {block.id: len(block.model_dump_json().encode()) for block in snapshot.blocks}
        self._bytes = sum(self._sizes.values())
        # Only currently open native addresses, never another copy of arguments.
        self._tools: dict[tuple[str, int, int], str] = {}

    def bind_delivery(self, publish: Callable[[DisplayDelta], None]) -> None:
        """Attach the Host's nonblocking sink before this producer starts."""
        if self._publish is not None or self._pending or self.state.position.sequence:
            raise ValueError("Display delivery must be bound before production")
        self._publish = publish

    def enrich_tool(self, scope_id: str, tool_call_id: str, metadata: dict[str, JsonValue]) -> None:
        """Attach Host-retained media references to the existing native tool block."""
        for block in self.state.blocks.values():
            if (
                block.scope_id != scope_id
                or block.kind != "tool_chunk"
                or block.content.get("tool_call_id") != tool_call_id
            ):
                continue
            previous = block.content.get("metadata")
            merged = {**(previous if isinstance(previous, dict) else {}), **metadata}
            if previous != merged:
                self._commit(
                    [
                        BlockPut(
                            block=block.model_copy(
                                update={
                                    "revision": block.revision + 1,
                                    "content": {**block.content, "metadata": merged},
                                }
                            ),
                            expected_revision=block.revision,
                        )
                    ]
                )
            return

    def capture(self) -> DisplaySnapshot:
        self.flush()
        return self.state.capture()

    def flush(self) -> DisplayDelta | None:
        """Assign one dense sequence to staged operations, then offer the atomic batch."""
        if not self._pending:
            return None
        position = self.state.position
        delta = DisplayDelta(
            producer=position.producer,
            from_sequence=position.sequence,
            through_sequence=position.sequence + 1,
            operations=tuple(self._pending),
        )
        self._pending = []
        self._pending_bytes = 0
        self.state.position = DisplayPosition(producer=delta.producer, sequence=delta.through_sequence)
        if self._publish is not None:
            self._publish(delta)
        return delta

    def _bounded(self, content: dict[str, JsonValue]) -> dict[str, JsonValue]:
        limit = self._max_field_chars
        if limit is None:
            return content
        truncated = False

        def bound(value: JsonValue) -> JsonValue:
            nonlocal truncated
            if isinstance(value, str) and len(value) > limit:
                truncated = True
                return value[:limit]
            if isinstance(value, dict):
                return {key: bound(item) for key, item in value.items()}
            if isinstance(value, list):
                return [bound(item) for item in value]
            return value

        result = {key: bound(value) for key, value in content.items()}
        if truncated:
            result["truncated"] = True
        return result

    def _commit(self, operations: list[DisplayOperation]) -> DisplayDelta | None:
        # Stage only changed values. Size accounting and retention do not walk
        # or copy the transcript on each token; an eviction walks its prefix.
        changed: dict[str, DisplayBlock | None] = {}
        bounded: list[DisplayOperation] = []
        omitted = self.state.omitted
        for operation in operations:
            if isinstance(operation, BlockPut):
                block = operation.block.model_copy(update={"content": self._bounded(operation.block.content)})
                changed[block.id] = block
                operation = operation.model_copy(update={"block": block})
            elif isinstance(operation, BlockAppend):
                previous = changed.get(operation.id, self.state.blocks.get(operation.id))
                assert previous is not None
                value = previous.content[operation.field]
                assert isinstance(value, str)
                content = {**previous.content, operation.field: value + operation.value}
                limited = self._bounded(content)
                block = previous.model_copy(update={"revision": operation.revision, "content": limited})
                changed[block.id] = block
                if limited != content:
                    operation = BlockPut(block=block, expected_revision=operation.expected_revision)
            elif isinstance(operation, BlocksRemove):
                changed.update(dict.fromkeys(operation.ids))
                omitted = operation.omitted
            bounded.append(operation)
        sizes = {
            key: len(block.model_dump_json().encode()) if block is not None else 0 for key, block in changed.items()
        }
        total = self._bytes + sum(size - self._sizes.get(key, 0) for key, size in sizes.items())
        count = len(self._sizes) + sum((size > 0) - (key in self._sizes) for key, size in sizes.items())
        removed: list[str] = []
        added = tuple(key for key in sizes if key not in self._sizes)
        for key in chain(self._sizes, added):
            if (self._max_blocks is None or count <= self._max_blocks) and (
                self._max_bytes is None or total <= self._max_bytes
            ):
                break
            size = sizes.get(key, self._sizes.get(key, 0))
            if size:
                removed.append(key)
                total -= size
                count -= 1
                sizes[key] = 0
        if removed:
            bounded.append(BlocksRemove(ids=tuple(removed), omitted=omitted + len(removed)))
        self.state.stage(bounded)
        self._pending.extend(bounded)
        self._pending_bytes += sum(len(operation.model_dump_json().encode()) for operation in bounded)
        for key, size in sizes.items():
            if size:
                self._sizes[key] = size
            else:
                self._sizes.pop(key, None)
        self._bytes = total
        # Bound pending work independently of the host's timer or public consumer.
        if not self._batch or len(self._pending) >= 128 or self._pending_bytes >= 8192:
            return self.flush()
        return None

    def scope(self, value: DisplayScope) -> DisplayDelta | None:
        if self.state.scopes.get(value.id) == value:
            return None
        return self._commit([ScopePut(scope=value)])

    def _put(
        self,
        *,
        id: str,
        scope: str,
        kind: BlockKind,
        content: dict[str, JsonValue],
        status: BlockStatus,
        message: int | None = None,
        part: int | None = None,
        previous: DisplayBlock | None = None,
    ) -> list[DisplayOperation]:
        previous = previous or self.state.blocks.get(id)
        revision = previous.revision if previous is not None else 0
        block = DisplayBlock(
            id=id,
            scope_id=scope,
            kind=kind,
            revision=revision + 1,
            content=content,
            status=status,
            message_index=message if message is not None else (previous.message_index if previous else None),
            part_index=part if part is not None else (previous.part_index if previous else None),
        )
        if previous is not None and previous.model_copy(update={"revision": block.revision}) == block:
            return []
        return [BlockPut(block=block, expected_revision=revision)]

    def _response_part(
        self, scope: str, message: int, index: int, part: Any, *, complete: bool
    ) -> list[DisplayOperation]:
        identifier = f"{scope}:message:{message}:part:{index}"
        if isinstance(part, TextPart | ThinkingPart):
            content: dict[str, JsonValue] = {"text": part.content}
            if isinstance(part, ThinkingPart):
                content["signature"] = part.signature or ""
            return self._put(
                id=identifier,
                scope=scope,
                message=message,
                part=index,
                kind="reasoning" if isinstance(part, ThinkingPart) else "text",
                content=content,
                status="succeeded" if complete else "running",
            )
        if isinstance(part, ToolCallPart | NativeToolCallPart):
            identifier = f"{scope}:tool:{part.tool_call_id}"
            previous = self.state.blocks.get(identifier)
            content = dict(previous.content) if previous is not None else {}
            content.update(
                tool_call_id=part.tool_call_id,
                name=part.tool_name,
                arguments=(part.args if isinstance(part.args, str) else part.args_as_json_str())
                if complete or part.args is not None
                else "",
                arguments_complete=complete,
                native=isinstance(part, NativeToolCallPart),
            )
            if isinstance(part, NativeToolCallPart):
                content["provider"] = part.provider_name
            # Argument completion never means that execution completed.
            return self._put(
                id=identifier,
                scope=scope,
                message=message,
                part=index,
                kind="tool_chunk",
                content=content,
                status=previous.status if previous is not None else "pending",
            )
        if isinstance(part, NativeToolReturnPart):
            return self._tool_result(scope, part)
        if isinstance(part, FilePart | SpeechPart):
            media = part.content if isinstance(part, FilePart) else part.audio
            projected = project_input_content(media) if media is not None else None
            content = {"media": projected[0]} if projected is not None else {}
            if isinstance(part, SpeechPart):
                content["text"] = part.transcript or ""
                content["speaker"] = part.speaker
            return self._put(
                id=identifier,
                scope=scope,
                message=message,
                part=index,
                kind="media",
                content=content,
                status="succeeded" if complete else "running",
            )
        if isinstance(part, CompactionPart):
            return self._put(
                id=identifier,
                scope=scope,
                message=message,
                part=index,
                kind="context_summary",
                content={"kind": "provider", "text": part.content},
                status="succeeded" if complete else "running",
            )
        # Unknown display-bearing native parts remain visible as unsupported,
        # without leaking binary content or arbitrary provider payloads.
        return self._put(
            id=identifier,
            scope=scope,
            message=message,
            part=index,
            kind="extension",
            status="unknown",
            content={"unsupported": True, "native_kind": part.part_kind},
        )

    def _tool_result(
        self,
        scope: str,
        part: ToolReturnPart | NativeToolReturnPart | RetryPromptPart,
        *,
        previous: DisplayBlock | None = None,
    ) -> list[DisplayOperation]:
        if not part.tool_call_id:
            return []
        identifier = f"{scope}:tool:{part.tool_call_id}"
        previous = previous or self.state.blocks.get(identifier)
        content = (
            dict(previous.content)
            if previous is not None
            else {"tool_call_id": part.tool_call_id, "name": part.tool_name}
        )
        content["result"] = part.model_response() if isinstance(part, RetryPromptPart) else part.model_response_str()
        status: BlockStatus
        if isinstance(part, ToolReturnPart | NativeToolReturnPart):
            content["outcome"] = part.outcome
            if isinstance(part, NativeToolReturnPart):
                content["provider"] = part.provider_name
            if isinstance(part, ToolReturnPart):
                content["metadata"] = _json(part.metadata or {})
            status = "succeeded" if part.outcome == "success" else "failed"
        else:
            content["retry"] = True
            status = "failed"
        if previous is not None and previous.status == "cancelled" and status == "failed":
            status = "cancelled"
        return self._put(
            id=identifier, scope=scope, kind="tool_chunk", content=content, status=status, previous=previous
        )

    def reconcile_message(
        self, scope: str, index: int, message: ModelMessage, *, tool_scopes: Mapping[str, str] | None = None
    ) -> DisplayDelta | None:
        operations: list[DisplayOperation] = []
        prefix = f"{scope}:message:{index}:"
        current: set[str] = set()
        for part_index, part in enumerate(message.parts):
            if isinstance(part, NativeToolReturnPart):
                previous = next(
                    (
                        op.block
                        for op in reversed(operations)
                        if isinstance(op, BlockPut) and op.block.id == f"{scope}:tool:{part.tool_call_id}"
                    ),
                    None,
                )
                operations.extend(self._tool_result(scope, part, previous=previous))
            elif isinstance(message, ModelResponse):
                if not isinstance(part, ToolCallPart | NativeToolCallPart | NativeToolReturnPart):
                    current.add(f"{prefix}part:{part_index}")
                operations.extend(
                    self._response_part(scope, index, part_index, part, complete=message.state == "complete")
                )
            elif isinstance(part, UserPromptPart):
                for content_index, item in enumerate(user_prompt_content(part)):
                    projected = project_input_content(item)
                    if projected is None:
                        continue
                    value, metadata = projected
                    if not metadata.display:
                        continue
                    identifier = f"{prefix}part:{part_index}:input:{content_index}"
                    current.add(identifier)
                    content: dict[str, JsonValue] = {"text": value} if isinstance(value, str) else {"media": value}
                    content["metadata"] = metadata.model_dump(mode="json")
                    content["message_metadata"] = _json(message.metadata or {})
                    operations.extend(
                        self._put(
                            id=identifier,
                            scope=scope,
                            kind="input" if isinstance(value, str) else "media",
                            content=content,
                            status="succeeded",
                            message=index,
                            part=part_index,
                        )
                    )
            elif isinstance(part, SpeechPart):
                current.add(f"{prefix}part:{part_index}")
                operations.extend(self._response_part(scope, index, part_index, part, complete=True))
            elif isinstance(part, ToolReturnPart | RetryPromptPart):
                owner = tool_scopes.get(part.tool_call_id, scope) if tool_scopes is not None else scope
                operations.extend(self._tool_result(owner, part))
        # Canonical message facts are presentation data, not a second message
        # transcript. Preserve them with native addresses for history and comments.
        operations = [
            operation.model_copy(
                update={
                    "block": operation.block.model_copy(
                        update={
                            "content": {
                                **operation.block.content,
                                "timestamp": message.timestamp.isoformat() if message.timestamp is not None else None,
                                "message_kind": "response" if isinstance(message, ModelResponse) else "request",
                                "message_metadata": _json(message.metadata or {}),
                            }
                        }
                    )
                }
            )
            if isinstance(operation, BlockPut)
            and operation.block.message_index == index
            and operation.block.scope_id == scope
            else operation
            for operation in operations
        ]
        removed = tuple(
            identifier
            for identifier in self.state.blocks
            if identifier.startswith(prefix) and identifier not in current
        )
        if removed:
            operations.append(BlocksRemove(ids=removed, omitted=self.state.omitted))
        return self._commit(operations)

    def observe(self, scope: str, message: int, event: AgentStreamEvent) -> DisplayDelta | None:
        operations: list[DisplayOperation] = []
        if isinstance(event, PartStartEvent | PartEndEvent):
            part = event.part
            key = (scope, message, event.index)
            identifier = f"{scope}:message:{message}:part:{event.index}"
            previous = self.state.blocks.get(identifier)
            # A boundary may already have reconciled this source part. Late
            # native delivery is covered by address, never by matching text.
            if previous is not None and previous.status == "succeeded":
                return None
            if isinstance(part, ToolCallPart | NativeToolCallPart):
                tool = self.state.blocks.get(f"{scope}:tool:{part.tool_call_id}")
                if tool is not None and tool.content.get("arguments_complete") is True:
                    self._tools.pop(key, None)
                    return None
                if isinstance(event, PartStartEvent):
                    self._tools[key] = part.tool_call_id
                else:
                    self._tools.pop(key, None)
            operations = self._response_part(
                scope, message, event.index, part, complete=isinstance(event, PartEndEvent)
            )
        elif isinstance(event, PartDeltaEvent):
            delta = event.delta
            identifier = f"{scope}:message:{message}:part:{event.index}"
            previous = self.state.blocks.get(identifier)
            if isinstance(delta, TextPartDelta | ThinkingPartDelta):
                if previous is None or previous.status != "running":
                    return None
                if delta.content_delta:
                    operations.append(
                        BlockAppend(
                            id=identifier,
                            field="text",
                            expected_revision=previous.revision,
                            revision=previous.revision + 1,
                            value=delta.content_delta,
                        )
                    )
                if isinstance(delta, ThinkingPartDelta) and delta.signature_delta is not None:
                    # Native thinking signatures replace their previous value;
                    # unlike text content, they are not concatenated fragments.
                    text = previous.content["text"]
                    assert isinstance(text, str)
                    operations = self._put(
                        id=identifier,
                        scope=scope,
                        kind=previous.kind,
                        status=previous.status,
                        content={
                            **previous.content,
                            "text": text + (delta.content_delta or ""),
                            "signature": delta.signature_delta,
                        },
                    )
            elif isinstance(delta, ToolCallPartDelta):
                key = (scope, message, event.index)
                call_id = self._tools.get(key)
                if call_id is None:
                    return None
                previous = self.state.blocks.get(f"{scope}:tool:{call_id}")
                if previous is None or previous.content.get("arguments_complete") is True:
                    self._tools.pop(key, None)
                    return None
                if isinstance(delta.args_delta, str) and not delta.tool_name_delta and not delta.tool_call_id:
                    operations.append(
                        BlockAppend(
                            id=previous.id,
                            field="arguments",
                            expected_revision=previous.revision,
                            revision=previous.revision + 1,
                            value=delta.args_delta,
                        )
                    )
                else:
                    arguments = previous.content["arguments"]
                    assert isinstance(arguments, str)
                    if isinstance(delta.args_delta, dict):
                        if previous.content.get("truncated") is True:
                            return None  # The complete native part reconciles at its boundary.
                        arguments = json.loads(arguments or "{}")
                    part = delta.apply(ToolCallPart(str(previous.content["name"]), arguments, tool_call_id=call_id))
                    self._tools[key] = part.tool_call_id
                    operations = self._response_part(scope, message, event.index, part, complete=False)
                    if part.tool_call_id != call_id:
                        operations.append(BlocksRemove(ids=(previous.id,), omitted=self.state.omitted))
        elif isinstance(event, FunctionToolResultEvent | OutputToolResultEvent):
            operations = self._tool_result(scope, event.part)
        elif isinstance(event, ModelInputEvent | ContextRestoredEvent):
            # Canonical reconciliation owns inputs (including display:false and
            # payload-free media). Never serialize raw native input as custom data.
            return None
        elif isinstance(event, CapabilityEvent):
            # Preserve native capability payloads without an AG-UI round trip.
            if event.kind not in self._ignored_capabilities:
                adapter = (
                    TypeAdapter(CapabilityEvent) if isinstance(event, UnknownCapabilityEvent) else TypeAdapter(Any)
                )
                operations = self._put(
                    id=f"{scope}:capability:{self.state.position.sequence}:{len(self._pending)}",
                    scope=scope,
                    kind="extension",
                    status="unknown",
                    content={"name": event.kind, "value": _json(adapter.dump_python(event, mode="json"))},
                )
        return self._commit(operations)

    def tool_status(self, scope: str, call_id: str, status: BlockStatus) -> DisplayDelta | None:
        block = self.state.blocks.get(f"{scope}:tool:{call_id}")
        if block is None:
            return None  # The Host's display retention may already have evicted it.
        return self._commit(self._put(id=block.id, scope=scope, kind=block.kind, content=block.content, status=status))

    def finish_scope(self, scope: str, status: str) -> None:
        existing = self.state.scopes.get(scope)
        if existing is None:
            return
        updated = DisplayScope.model_validate({**existing.model_dump(), "status": status})
        operations: list[DisplayOperation] = [ScopePut(scope=updated)] if updated != existing else []
        for block in self.state.blocks.values():
            if block.scope_id != scope or block.status not in {"pending", "running"}:
                continue
            interrupted: BlockStatus = "cancelled" if status == "cancelled" else "unknown"
            operations.extend(
                self._put(id=block.id, scope=scope, kind=block.kind, content=block.content, status=interrupted)
            )
        self._commit(operations)
        self._tools = {key: call for key, call in self._tools.items() if key[0] != scope}

    def summary(
        self, scope: str, operation: str, kind: str, text: str, *, files: tuple[str, ...] = ()
    ) -> DisplayDelta | None:
        return self._commit(
            self._put(
                id=f"{scope}:context:{operation}",
                scope=scope,
                kind="context_summary",
                status="succeeded",
                content={"operation_id": operation, "kind": kind, "text": text, "files": list(files)},
            )
        )

    def _execution_summary(self, scope: str, kind: str, payload: dict[str, JsonValue]) -> None:
        event_type = str(payload.get("type", ""))
        identity = payload.get("request_id") if kind == "lifecycle" else payload.get("operation_id")
        request_index = payload.get("request_index")
        if event_type == "context_snapshot" and isinstance(request_index, int):
            identity = f"model-request-{request_index + 1}"
        if not isinstance(identity, str):
            return
        name = "model_request" if kind == "lifecycle" else "context_operation"
        identifier = f"{scope}:execution:{identity}"
        previous = self.state.blocks.get(identifier)
        if previous is None and not (event_type.endswith("_started") or event_type == "context_snapshot"):
            return  # Updating an evicted summary must not resurrect older history.
        old_value = previous.content.get("value") if previous is not None else None
        value = {**(old_value if isinstance(old_value, dict) else {}), **payload, "type": name}
        value["request_id" if kind == "lifecycle" else "operation_id"] = identity
        if kind == "context":
            value["operation"] = event_type.split("_", 1)[0]
        status: BlockStatus = previous.status if previous is not None else "pending"
        if status in {"pending", "running"}:
            if event_type.endswith("_started"):
                status = "running"
            elif event_type.endswith("_completed"):
                status = "succeeded"
            elif event_type.endswith("_failed"):
                status = "failed"
        value["status"] = status
        self._commit(
            self._put(
                id=identifier,
                scope=scope,
                kind="extension",
                status=status,
                content={"name": f"a13n.display.{name}", "value": value},
            )
        )

    def observe_extension(self, *, thread_id: str, run_id: str, event: HarnessExtensionEvent) -> None:
        """Observe validated extensions at emission, never again at forwarding."""
        payload = event.payload
        if event.kind == "delegation" and isinstance(payload, dict) and payload.get("type") == "inline_delegation":
            delegation = InlineDelegationPayload.model_validate(payload)
            child_run = delegation.child_run_id
            if child_run is None:
                return
            existing = self.state.scopes.get(child_run)
            if delegation.action == "started" and child_run == run_id:
                self.scope(
                    DisplayScope(
                        id=child_run,
                        run_id=child_run,
                        thread_id=thread_id,
                        parent_scope_id=delegation.parent_run_id,
                        parent_tool_call_id=delegation.parent_tool_call_id,
                        invocation_id=delegation.invocation_id,
                    )
                )
            elif existing is not None and delegation.action in {"completed", "failed"}:
                status = "cancelled" if delegation.status == "cancelled" else delegation.action
                self.scope(existing.model_copy(update={"status": status}))
            return
        # Keep one compact execution summary per request/context operation,
        # not the observation log. Usage accounting remains a separate authority.
        if event.kind == "state" and isinstance(payload, dict) and payload.get("type") == "task_changed":
            task = payload.get("task")
            if isinstance(task, dict) and isinstance(task.get("id"), str):
                if run_id not in self.state.scopes:
                    self.scope(DisplayScope(id=run_id, run_id=run_id, thread_id=thread_id))
                self._commit(
                    self._put(
                        id=f"{run_id}:task:{task['id']}",
                        scope=run_id,
                        kind="extension",
                        status="unknown",
                        content={
                            "name": "a13n.display.task",
                            "value": task,
                            "task_state_version": payload.get("task_state_version", 0),
                        },
                    )
                )
            return
        if event.kind in {"usage", "state", "diagnostic"}:
            return
        if run_id not in self.state.scopes:
            self.scope(DisplayScope(id=run_id, run_id=run_id, thread_id=thread_id))
        if event.kind in {"lifecycle", "context"}:
            if isinstance(payload, dict):
                self._execution_summary(run_id, event.kind, payload)
            return
        name = (payload.get("type") or payload.get("name") or event.kind) if isinstance(payload, dict) else event.kind
        call = payload.get("tool_call_id") if isinstance(payload, dict) else None
        address = call or f"{self.state.position.sequence}:{len(self._pending)}"
        identifier = f"{run_id}:extension:{event.kind}:{name}:{address}"
        self._commit(
            self._put(
                id=identifier,
                scope=run_id,
                kind="extension",
                status="unknown",
                content={"name": str(name), "event_kind": event.kind, "value": payload},
            )
        )
