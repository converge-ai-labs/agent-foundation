"""Publish originals before retaining small references on actual tool returns."""

from __future__ import annotations

import base64

from a13n_harness import HarnessEvent
from ag_ui.core import CustomEvent
from anyio import fail_after
from mcp.types import BlobResourceContents, TextResourceContents
from pydantic import ValidationError
from pydantic_ai.messages import FunctionToolResultEvent, ToolReturnPart

from a13n_harness_ui.errors import HarnessUiError
from a13n_harness_ui.mcp_runtime.connections import Connection
from a13n_harness_ui.storage.objects import ImmutableObjectStore, ObjectKind

from .connections import MIME_TYPE, CapturedCall, Connections, resource_uri
from .models import AppPresentation, AppReference, AppResource, AppSnapshot

METADATA_KEY = "a13n.harness-ui.mcp_apps"


class AppSnapshots:
    def __init__(self, objects: ImmutableObjectStore, connections: Connections) -> None:
        self.objects = objects
        self.connections = connections

    async def observe(self, item: object, *, thread_id: str, run_id: str) -> tuple[CustomEvent, ...]:
        if not isinstance(item, HarnessEvent) or item.run_id != run_id:
            return ()
        event = item.event
        if not isinstance(event, FunctionToolResultEvent) or not isinstance(event.part, ToolReturnPart):
            return ()
        part = event.part
        calls = self.connections.captures.take(run_id, part.tool_call_id)
        if not calls:
            return ()
        references = []
        for call in calls:
            reference = await self.capture(call, thread_id=thread_id, run_id=run_id, call_id=part.tool_call_id)
            references.append(reference.model_dump(mode="json"))
        if part.metadata is None or isinstance(part.metadata, dict):
            part.metadata = {**(part.metadata or {}), METADATA_KEY: references}
        return (
            CustomEvent(
                name=METADATA_KEY,
                value={"run_id": run_id, "event": {"tool_call_id": part.tool_call_id, "apps": references}},
            ),
        )

    async def read(self, reference: AppReference) -> AppPresentation:
        """Reading an original never connects, activates a View, or repeats a tool."""
        if reference.snapshot is None or reference.snapshot.object_kind != ObjectKind.mcp_app_snapshot:
            raise HarnessUiError("The original App presentation is unavailable.", code="mcp_app_not_found")
        snapshot = await self.objects.read_model(reference.snapshot, AppSnapshot)
        if (
            snapshot.app_id != reference.app_id
            or snapshot.thread_id != reference.thread_id
            or snapshot.run_id != reference.run_id
            or snapshot.tool_call_id != reference.tool_call_id
            or snapshot.server_id != reference.server_id
            or snapshot.tool.get("name") != reference.tool_name
        ):
            raise HarnessUiError("App reference does not match its original.", code="mcp_app_reference_invalid")
        resource = None
        if snapshot.resource is not None:
            if snapshot.resource.object_kind != ObjectKind.mcp_app_resource:
                raise HarnessUiError("App resource has the wrong kind.", code="mcp_app_reference_invalid")
            resource = await self.objects.read_model(snapshot.resource, AppResource)
        connection = self.connections.get(reference.thread_id, reference.server_id)
        return AppPresentation(
            reference=reference,
            snapshot=snapshot,
            resource=resource,
            connected=connection is not None
            and connection.connected
            and connection.generation == snapshot.connection_generation,
        )

    async def capture(self, call: CapturedCall, *, thread_id: str, run_id: str, call_id: str) -> AppReference:
        reference = AppReference(
            app_id=call.app_id,
            thread_id=thread_id,
            run_id=run_id,
            tool_call_id=call_id,
            server_id=call.connection.server_id,
            tool_name=call.tool.name,
        )
        resource = None
        unavailable = None
        try:
            with fail_after(10):
                uri = resource_uri(call.tool)
                assert uri is not None
                saved = await self.objects.publish_model(
                    object_kind=ObjectKind.mcp_app_resource, value=await read_app_resource(call.connection, uri)
                )
                resource = saved.ref
        except Exception:
            # The tool already completed. Presentation errors neither retry it nor alter model content.
            unavailable = "The original App resource could not be retained. The tool result is still available."
        snapshot = AppSnapshot(
            connection_generation=call.connection.generation,
            app_id=reference.app_id,
            thread_id=thread_id,
            run_id=run_id,
            tool_call_id=call_id,
            server_id=reference.server_id,
            tool=call.tool.model_dump(mode="json", by_alias=True, exclude_none=True),
            arguments=call.arguments,
            result=call.result.model_dump(mode="json", by_alias=True, exclude_none=True),
            resource=resource,
            unavailable=unavailable,
        )
        try:
            saved = await self.objects.publish_model(object_kind=ObjectKind.mcp_app_snapshot, value=snapshot)
            return reference.model_copy(update={"snapshot": saved.ref, "unavailable": unavailable})
        except Exception:
            return reference.model_copy(update={"unavailable": "The original App presentation could not be saved."})


async def read_app_resource(connection: Connection, uri: str) -> AppResource:
    result = await connection.client.read_resource_mcp(uri)
    contents = [item for item in result.contents if str(item.uri) == uri and item.mime_type == MIME_TYPE]
    if len(contents) != 1:
        raise ValueError("Expected exactly one MCP App HTML resource")
    content = contents[0]
    if isinstance(content, TextResourceContents):
        html = content.text
    elif isinstance(content, BlobResourceContents):
        html = base64.b64decode(content.blob, validate=True).decode("utf-8")
    else:
        raise ValueError("Unsupported MCP App resource content")
    return AppResource(uri=uri, html=html, metadata=content.meta or {})


def app_references(part: ToolReturnPart) -> tuple[AppReference, ...]:
    if not isinstance(part.metadata, dict):
        return ()
    values = part.metadata.get(METADATA_KEY)
    if not isinstance(values, list):
        return ()
    try:
        return tuple(AppReference.model_validate(value, strict=False) for value in values)
    except (ValidationError, TypeError):
        return ()
