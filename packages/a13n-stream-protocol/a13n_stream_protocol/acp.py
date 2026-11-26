"""Project one ACP turn into the shared AG-UI display stream."""

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from a13n_harness.acp import AcpUpdate, acp_host_tool_name
from a13n_harness.tools.diagnostics import SYSTEM_FAILURE_METADATA_KEY
from acp import schema

MAX_COMPACTION_SUMMARY_BYTES = 64 * 1024


def _bound_utf8(text: str, limit: int = MAX_COMPACTION_SUMMARY_BYTES) -> str:
    if len(text) <= limit // 4:
        return text
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text
    return encoded[:limit].decode("utf-8", errors="ignore")


def _content_block_text(block: object) -> str:
    if isinstance(block, schema.TextContentBlock):
        return block.text
    if isinstance(block, schema.EmbeddedResourceContentBlock) and isinstance(
        block.resource, schema.TextResourceContents
    ):
        return block.resource.text
    return ""


@dataclass
class _Tool:
    metadata: dict[str, Any] = field(default_factory=dict)
    arguments_sent: bool = False
    result: Any = None


@dataclass
class _Compaction:
    status: str = "in_progress"
    summary: str = ""
    error: str | None = None


class AcpRunStream:
    """Pass ``update`` as the binding observer; the host owns run settlement."""

    def __init__(self, thread_id: str, run_id: str, emit: Callable[[dict[str, Any]], None]) -> None:
        self.thread_id = thread_id
        self.run_id = run_id
        self.emit = emit
        self.final_text = ""
        self.last_text = ""
        self._message: tuple[str, str] | None = None
        self._sequence = 0
        self._tools: dict[str, _Tool] = {}
        self._completed: set[str] = set()
        self._compactions: dict[str, _Compaction] = {}
        self._started = self._finished = False

    def _event(self, event_type: str, **fields: Any) -> None:
        self.emit({"type": event_type, "timestamp": round(time.time() * 1000), **fields})

    def start(self) -> None:
        if not self._started:
            self._started = True
            self._event("RUN_STARTED", threadId=self.thread_id, runId=self.run_id)

    def _close_message(self) -> None:
        if self._message is not None:
            kind, message_id = self._message
            self._event(f"{kind}_END", messageId=message_id)
            self._message = None

    def reset_reply(self) -> None:
        """Keep streamed history while starting a new candidate reply."""
        self._close_message()
        self.last_text = ""

    def _chunk(self, kind: str, text: str) -> None:
        if not text:
            return
        if self._message is None or self._message[0] != kind:
            self._close_message()
            self._sequence += 1
            message_id = f"{self.run_id}:message:{self._sequence}"
            self._message = kind, message_id
            role = "assistant" if kind == "TEXT_MESSAGE" else "reasoning"
            self._event(f"{kind}_START", messageId=message_id, role=role)
        self._event(f"{kind}_CONTENT", messageId=self._message[1], delta=text)

    def update(self, update: AcpUpdate) -> None:
        if self._finished:
            return
        self.start()
        if isinstance(update, schema.AgentMessageChunk) and isinstance(update.content, schema.TextContentBlock):
            if not update.content.text:
                return
            if self._message is None or self._message[0] != "TEXT_MESSAGE":
                self.last_text = ""
            self.last_text += update.content.text
            self.final_text += update.content.text
            self._chunk("TEXT_MESSAGE", update.content.text)
        elif isinstance(update, schema.AgentThoughtChunk) and isinstance(update.content, schema.TextContentBlock):
            self._chunk("REASONING_MESSAGE", update.content.text)
        elif isinstance(update, schema.ToolCallStart | schema.ToolCallProgress):
            self._tool(update)
        elif isinstance(update, schema.AgentPlanUpdate | schema.AgentPlanContentUpdate | schema.AgentPlanRemovedUpdate):
            self._event(
                "CUSTOM", name="a13n.acp.plan", value=update.model_dump(mode="json", by_alias=True, exclude_none=True)
            )
        elif isinstance(update, schema.UsageUpdate):
            self._event("CUSTOM", name="a13n.acp.usage", value={"used": update.used, "size": update.size})
        elif isinstance(update, schema.SessionUpdateCompactionUpdate):
            self._compaction_update(update)
        elif isinstance(update, schema.SessionUpdateCompactionSummaryChunk):
            self._compaction_chunk(update)
        # Authored input comes from the host. ACP user chunks may echo hidden context.

    def _emit_compaction(self, compaction_id: str, state: _Compaction) -> None:
        payload: dict[str, Any] = {"compactionId": compaction_id, "status": state.status}
        if state.summary:
            payload["summary"] = state.summary
        if state.error:
            payload["error"] = state.error
        self._event("CUSTOM", name="a13n.acp.compaction", value=payload)

    def _compaction_update(self, update: schema.SessionUpdateCompactionUpdate) -> None:
        self._close_message()
        state = self._compactions.setdefault(update.compaction_id, _Compaction())
        state.status = update.status
        if update.summary is not None:
            parts = [text for block in update.summary if (text := _content_block_text(block))]
            state.summary = _bound_utf8("\n".join(parts))
        if update.error is not None:
            state.error = update.error
        elif update.status == "completed":
            state.error = None
        self._emit_compaction(update.compaction_id, state)

    def _compaction_chunk(self, update: schema.SessionUpdateCompactionSummaryChunk) -> None:
        self._close_message()
        state = self._compactions.setdefault(update.compaction_id, _Compaction())
        text = _content_block_text(update.content)
        if text:
            state.summary = _bound_utf8(state.summary + text)
        self._emit_compaction(update.compaction_id, state)

    def _tool(self, update: schema.ToolCallStart | schema.ToolCallProgress) -> None:
        self._close_message()
        call_id = update.tool_call_id
        if call_id in self._completed:
            return
        new = call_id not in self._tools
        tool = self._tools.setdefault(call_id, _Tool())
        values = update.model_dump(mode="json", by_alias=True, exclude_none=True)
        tool.metadata.update(
            {key: values[key] for key in ("title", "kind", "status", "locations", "content") if key in values}
        )
        if new:
            title = update.title or "Tool"
            name = update.name or title.removeprefix("Run ")
            canonical = acp_host_tool_name(name)
            self._event(
                "TOOL_CALL_START",
                toolCallId=call_id,
                toolCallName=canonical if canonical != name else update.name or title,
            )
        if not tool.arguments_sent and update.raw_input is not None:
            tool.arguments_sent = True
            self._event("TOOL_CALL_ARGS", toolCallId=call_id, delta=json.dumps(values["rawInput"], ensure_ascii=False))
        if update.raw_output is not None:
            tool.result = values["rawOutput"]
        if tool.metadata:
            self._event("CUSTOM", name="a13n.acp.tool", value={"toolCallId": call_id, **tool.metadata})
        if update.status in {"completed", "failed"}:
            self._close_tool(call_id, tool, failed=update.status == "failed")

    def _close_tool(self, call_id: str, tool: _Tool, *, failed: bool) -> None:
        del self._tools[call_id]
        self._completed.add(call_id)
        self._event("TOOL_CALL_END", toolCallId=call_id)
        result = tool.result
        if result is None:
            result = "\n".join(
                entry["content"]["text"]
                for entry in tool.metadata.get("content", [])
                if entry["type"] == "content" and entry["content"]["type"] == "text"
            )
        if not isinstance(result, str):
            result = json.dumps(result, ensure_ascii=False)
        self._event(
            "TOOL_CALL_RESULT",
            messageId=f"{self.run_id}:{call_id}:result",
            role="tool",
            toolCallId=call_id,
            content=result,
            **({"metadata": {SYSTEM_FAILURE_METADATA_KEY: True}} if failed else {}),
        )

    def finish(self, *, error: str | None = None) -> None:
        if self._finished:
            return
        self.start()
        self._finished = True
        self._close_message()
        for call_id, tool in tuple(self._tools.items()):
            self._close_tool(call_id, tool, failed=True)
        if error is not None:
            self._event("RUN_ERROR", message=error)
        else:
            self._event("RUN_FINISHED", threadId=self.thread_id, runId=self.run_id)
