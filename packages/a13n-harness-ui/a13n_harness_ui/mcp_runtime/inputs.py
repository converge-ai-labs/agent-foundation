"""Process-local human decisions; answering never resumes or admits an Agent Run."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import uuid4

from fastmcp.client.elicitation import ElicitResult
from jsonschema import Draft202012Validator, ValidationError
from mcp.types import ElicitRequestFormParams, ElicitRequestParams
from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from a13n_harness_ui.errors import HarnessUiError

from .connections import Connection, Operation


class InputModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class McpInputResponse(InputModel):
    action: Literal["accept", "decline", "cancel"]
    content: dict[str, JsonValue] | None = None

    @model_validator(mode="after")
    def content_only_on_accept(self) -> McpInputResponse:
        if self.action != "accept" and self.content is not None:
            raise ValueError("Only an accepted form response can include content.")
        return self


class McpInputRequestView(InputModel):
    request_id: str
    thread_id: str
    server_id: str
    run_id: str | None = None
    tool_call_id: str | None = None
    view_id: str | None = None
    mode: Literal["form", "url"]
    message: str = Field(max_length=8192)
    schema_: dict[str, JsonValue] | None = Field(default=None, alias="schema")
    url: str | None = None
    state: Literal["pending", "accepted", "declined", "cancelled", "expired", "unavailable"] = "pending"
    expires_at: datetime


class McpIntegrationView(InputModel):
    thread_id: str
    server_id: str
    generation: str
    connected: bool
    retired: bool


@dataclass
class _Request:
    view: McpInputRequestView
    connection: Connection
    operation: Operation
    future: asyncio.Future[ElicitResult[Any]]
    response: McpInputResponse | None = None


def validate_schema(schema: dict[str, Any]) -> None:
    """Accept the renderable primitive subset, not arbitrary or remote JSON Schema."""
    if len(json.dumps(schema).encode()) > 64 * 1024:
        raise ValueError("MCP input schema is too large.")
    if schema.get("type") != "object" or not isinstance(schema.get("properties", {}), dict):
        raise ValueError("MCP forms require an object of primitive fields.")
    properties = schema.get("properties", {})
    if len(properties) > 32:
        raise ValueError("MCP forms support at most 32 fields.")

    def titled_options(value: Any) -> bool:
        return (
            isinstance(value, list)
            and bool(value)
            and all(
                isinstance(option, dict)
                and set(option) <= {"const", "title"}
                and isinstance(option.get("const"), str)
                and isinstance(option.get("title"), str)
                for option in value
            )
        )

    for prop in properties.values():
        if not isinstance(prop, dict) or prop.get("type") not in ("string", "number", "integer", "boolean", "array"):
            raise ValueError("MCP forms support only primitive fields and enum selections.")
        if prop.get("format") in ("password", "secret") or prop.get("writeOnly") is True:
            raise ValueError("Sensitive input must use an external browser flow, not an MCP form.")
        if "oneOf" in prop and (prop.get("type") != "string" or not titled_options(prop["oneOf"])):
            raise ValueError("MCP oneOf fields must be titled string selections.")
        if prop.get("type") == "array":
            items = prop.get("items", {})
            if not isinstance(items, dict) or not (
                (items.get("type") == "string" and isinstance(items.get("enum"), list))
                or (set(items) == {"anyOf"} and titled_options(items["anyOf"]))
            ):
                raise ValueError("MCP arrays must be string enum selections.")

    def check(value: Any) -> None:
        if isinstance(value, dict):
            if any(key in value for key in ("$ref", "$dynamicRef", "allOf", "not", "patternProperties")):
                raise ValueError("Unsupported MCP form schema.")
            if "anyOf" in value and not (set(value) == {"anyOf"} and titled_options(value["anyOf"])):
                raise ValueError("MCP anyOf is supported only for titled string selections.")
            if "oneOf" in value and not titled_options(value["oneOf"]):
                raise ValueError("Unsupported MCP form schema.")
            for child in value.values():
                check(child)
        elif isinstance(value, list):
            for child in value:
                check(child)

    check(schema)
    Draft202012Validator.check_schema(schema)


class Inputs:
    def __init__(self, changed: Callable[[str], Awaitable[None]], *, timeout_seconds: float = 300) -> None:
        self._changed = changed
        self._timeout = timeout_seconds
        self._requests: dict[str, _Request] = {}
        self._closed = False

    async def __call__(
        self, connection: Connection, operation: Operation, params: ElicitRequestParams
    ) -> ElicitResult[Any]:
        if self._closed:
            return ElicitResult(action="cancel")
        # Bounded reconciliation history never evicts a pending operation.
        for key in tuple(self._requests):
            if len(self._requests) < 128:
                break
            if self._requests[key].view.state != "pending":
                self._requests.pop(key)
        if len(self._requests) >= 128:
            return ElicitResult(action="decline")
        schema, url = None, None
        if isinstance(params, ElicitRequestFormParams):
            validate_schema(params.requested_schema)
            schema = params.requested_schema
        else:
            parsed = urlsplit(params.url)
            if parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError("MCP input URL must be an HTTP(S) browser destination without credentials.")
            url = params.url
        view = McpInputRequestView(
            request_id=f"mcpinp_{uuid4().hex}",
            thread_id=connection.thread_id,
            server_id=connection.server_id,
            run_id=operation.run_id,
            tool_call_id=operation.tool_call_id,
            view_id=operation.view_id,
            mode=params.mode,
            message=params.message,
            schema=schema,
            url=url,
            expires_at=datetime.now(UTC) + timedelta(seconds=self._timeout),
        )
        request = _Request(view, connection, operation, asyncio.get_running_loop().create_future())
        self._requests[view.request_id] = request
        try:
            await self._changed(connection.thread_id)
            async with asyncio.timeout(self._timeout):
                return await asyncio.shield(request.future)
        except TimeoutError:
            request.view = request.view.model_copy(update={"state": "expired"})
            return ElicitResult(action="cancel")
        finally:
            if request.view.state == "pending":
                request.view = request.view.model_copy(update={"state": "unavailable"})
            if not request.future.done():
                request.future.cancel()
            await self._changed(connection.thread_id)

    def thread_ids(self) -> set[str]:
        return {item.view.thread_id for item in self._requests.values()}

    def requests(self, thread_ids: set[str]) -> tuple[McpInputRequestView, ...]:
        return tuple(
            item.view.model_copy(deep=True) for item in self._requests.values() if item.view.thread_id in thread_ids
        )

    async def respond(self, thread_ids: set[str], request_id: str, response: McpInputResponse) -> McpInputRequestView:
        request = self._requests.get(request_id)
        if request is None or request.view.thread_id not in thread_ids:
            raise HarnessUiError("MCP input request is unavailable.", code="mcp_input_not_found")
        if request.response is not None:
            if request.response == response:
                return request.view.model_copy(deep=True)
            raise HarnessUiError("MCP input was already answered differently.", code="mcp_input_conflict")
        if request.view.state == "pending" and datetime.now(UTC) >= request.view.expires_at:
            request.view = request.view.model_copy(update={"state": "expired"})
            if not request.future.done():
                request.future.set_result(ElicitResult(action="cancel"))
            await self._changed(request.view.thread_id)
        if request.view.state != "pending" or request.future.done():
            raise HarnessUiError("MCP input is no longer pending.", code="mcp_input_stale")
        request.connection.require_admission()
        if request.connection._active_operation is not request.operation:
            raise HarnessUiError("The MCP operation is unavailable.", code="mcp_input_stale")
        if response.action == "accept":
            if request.view.mode == "url":
                if response.content is not None:
                    raise HarnessUiError("URL confirmation cannot include form data.", code="mcp_input_invalid")
            else:
                schema = request.view.schema_ or {}
                try:
                    properties = schema.get("properties", {})
                    if (
                        not isinstance(properties, dict)
                        or response.content is None
                        or set(response.content) - set(properties)
                    ):
                        raise ValueError("Form content contains missing or unknown fields.")
                    if len(json.dumps(response.content).encode()) > 64 * 1024:
                        raise ValueError("MCP input response is too large.")
                    Draft202012Validator(schema).validate(response.content)
                except (ValueError, ValidationError) as exc:
                    raise HarnessUiError(
                        "MCP input does not match the requested form.", code="mcp_input_invalid"
                    ) from exc
        request.response = response.model_copy(deep=True)
        request.view = request.view.model_copy(
            update={"state": {"accept": "accepted", "decline": "declined", "cancel": "cancelled"}[response.action]}
        )
        request.future.set_result(ElicitResult(action=response.action, content=response.content))
        await self._changed(request.view.thread_id)
        return request.view.model_copy(deep=True)

    async def close(self) -> None:
        self._closed = True
        for request in self._requests.values():
            if request.view.state == "pending":
                request.view = request.view.model_copy(update={"state": "unavailable"})
                if not request.future.done():
                    request.future.set_result(ElicitResult(action="cancel"))
