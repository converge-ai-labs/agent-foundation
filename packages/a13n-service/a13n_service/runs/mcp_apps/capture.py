"""Bound originals in memory, then attach them only to an actual retained tool return."""

from dataclasses import dataclass
from typing import Any

from a13n_harness import AgentContext, HarnessEvent, HarnessStreamEvent
from a13n_logging import get_logger
from pydantic_ai.messages import FunctionToolResultEvent, ToolReturnPart

from a13n_service.infra.ids import new_object_id
from a13n_service.providers.tools.mcp_apps import Original, resource_uri
from a13n_service.resources.connections.access import McpConnection
from a13n_service.resources.connections.runtime import SelectedConnection
from a13n_service.runs.contents import MAX_APP_BYTES, MAX_PENDING_APP_BYTES, MAX_PENDING_APPS, Contents
from a13n_service.runs.mcp_apps.schemas import AppReference, SavedApp

logger = get_logger(__name__)


@dataclass(frozen=True)
class _Pending:
    context: AgentContext
    reference: AppReference
    data: bytes | None


class Captures:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.pending: list[_Pending] = []
        self.size = 0

    def add(self, revision_id: str, selected: SelectedConnection, original: Original) -> None:
        if len(self.pending) >= MAX_PENDING_APPS:
            return
        connection = selected.connection
        assert isinstance(connection, McpConnection)
        if selected.selection is None:
            raise ValueError("App capture requires the authorized connection selection")
        uri = resource_uri(original.tool)
        assert uri is not None
        reference = AppReference(
            app_id=new_object_id("app"),
            connection_id=connection.id,
            agent_revision_id=revision_id,
            source_run_id=original.context.run_id,
            tool_call_id=original.tool_call_id,
            tool_name=original.tool.name,
            resource_uri=uri,
        )
        saved = SavedApp(
            reference=reference,
            run_id=self.run_id,
            connection_url=connection.config.url,
            selection=selected.selection,
            tool=original.tool.model_dump(mode="json", by_alias=True, exclude_none=True),
            arguments=original.arguments,
            result=original.result.model_dump(mode="json", by_alias=True, exclude_none=True),
        )
        data = saved.model_dump_json().encode()
        if len(data) > MAX_APP_BYTES or self.size + len(data) > MAX_PENDING_APP_BYTES:
            data = None
            reference = reference.model_copy(update={"unavailable": True})
            logger.warning("MCP App original exceeded capture limit", extra={"app_id": reference.app_id})
        self.pending.append(_Pending(original.context, reference, data))
        self.size += len(data or b"")

    def observe(self, source: HarnessStreamEvent[Any], *, root_run_id: str | None, contents: Contents) -> dict | None:
        if not isinstance(source, HarnessEvent) or not isinstance(source.event, FunctionToolResultEvent):
            return None
        part = source.event.part
        if not isinstance(part, ToolReturnPart):
            return None
        references = []
        for pending in tuple(self.pending):
            context = pending.context
            if context.run_id != source.run_id:
                continue
            origin = context._tool_executions.result_origins.get(part.tool_call_id)
            if origin is None and part.tool_call_id in context._tool_executions.calls:
                continue  # A background receipt is not the result.
            call_id = origin.tool_call_id if origin is not None else part.tool_call_id
            run_id = origin.run_id if origin is not None else source.run_id
            ref = pending.reference
            if (ref.source_run_id, ref.tool_call_id) != (run_id, call_id):
                continue
            if pending.data is not None and not contents.app(ref.app_id, pending.data):
                ref = ref.model_copy(update={"unavailable": True})
            references.append(ref.model_dump(mode="json"))
            self.pending.remove(pending)
            self.size -= len(pending.data or b"")
        if not references:
            return None
        event: dict[str, Any] = {
            "type": "CUSTOM",
            "name": "a13n.mcp_apps",
            "value": {"event": {"tool_call_id": part.tool_call_id, "apps": references}},
        }
        if source.run_id != root_run_id:
            event["subagentRunId"] = source.run_id
        return event
