"""Explicit platform tool contracts and their authenticated internal MCP boundary.

Feature contributions own names, schemas, effects and calls. OpenAPI admission never
adds an internal capability. Transport serialization supplies no authorization.
"""

import inspect
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Annotated, Any, Literal, get_args, get_origin, get_type_hints

from fastapi import FastAPI, Request
from fastmcp.server.dependencies import get_http_request
from fastmcp.tools import Tool, ToolResult
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError, create_model

from a13n_service.infra.errors import ServiceError
from a13n_service.infra.http import request_id
from a13n_service.infra.platform import PLATFORM_METADATA, PlatformGroup
from a13n_service.infra.reads import tool_response
from a13n_service.tenancy.authorize import Principal
from a13n_service.tenancy.requests import cached_credential, current_workspace


@dataclass(frozen=True)
class ToolContext[RuntimeT]:
    runtime: RuntimeT
    actor: Principal
    workspace_id: str
    request: Request
    origin: str | None


@dataclass(frozen=True)
class Definition:
    name: str
    group: PlatformGroup
    effect: Literal["read", "write"]
    description: str
    inputs: type[BaseModel]
    handler: Callable[..., Awaitable[object]]
    containers: dict[str, type[BaseModel]]


def definition(
    handler: Callable[..., Awaitable[object]], *, group: PlatformGroup, effect: Literal["read", "write"]
) -> Definition:
    """Compile an explicitly contributed typed callable, expanding its input record.

    The original record validates again before invocation, preserving model validators
    and the distinction between omitted fields and explicit nulls.
    """
    hints = get_type_hints(handler, include_extras=True)
    fields: dict[str, Any] = {}
    containers = {}
    for name, parameter in inspect.signature(handler).parameters.items():
        if name == "ctx":
            continue
        annotation = hints[name]
        underlying = get_args(annotation)[0] if get_origin(annotation) is Annotated else annotation
        if name in {"body", "filters"} and isinstance(underlying, type) and issubclass(underlying, BaseModel):
            containers[name] = underlying
            for field_name, field in underlying.model_fields.items():
                if field_name in fields:
                    raise ValueError(f"Conflicting platform input: {handler.__name__}.{field_name}")
                fields[field_name] = (field.annotation, field)
        else:
            if name in fields:
                raise ValueError(f"Conflicting platform input: {handler.__name__}.{name}")
            fields[name] = (annotation, ... if parameter.default is inspect.Parameter.empty else parameter.default)
    inputs = create_model(handler.__name__ + "Input", __config__=ConfigDict(extra="forbid"), **fields)
    return Definition(
        handler.__name__,
        group,
        effect,
        inspect.getdoc(handler) or handler.__name__.replace("_", " ").capitalize(),
        inputs,
        handler,
        containers,
    )


def validate_catalog(definitions: Sequence[Definition]) -> None:
    names: set[str] = set()
    for contract in definitions:
        if contract.name in names:
            raise ValueError(f"Duplicate platform tool: {contract.name}")
        names.add(contract.name)
        if "request_body" in contract.inputs.model_fields:
            raise ValueError(f"Platform tool cannot expose an HTTP body: {contract.name}")
        version = contract.inputs.model_fields.get("expected_version")
        if version is not None and not version.is_required():
            raise ValueError(f"Platform precondition must be required: {contract.name}")


class PlatformTool(Tool):
    def __init__(self, contract: Definition, app: FastAPI):
        super().__init__(
            name=contract.name,
            description=contract.description
            + " Never automatically retry a write after an unknown outcome; read the resource to reconcile it.",
            parameters=contract.inputs.model_json_schema(),
            annotations=ToolAnnotations(
                read_only_hint=contract.effect == "read",
                destructive_hint=contract.effect == "write",
                idempotent_hint=False,
                open_world_hint=True,
            ),
            meta={PLATFORM_METADATA: {"group": contract.group, "effect": contract.effect}},
        )
        self._contract = contract
        self._app = app

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        incoming = get_http_request()
        # Parent state owns the runtime, catalog and import budgets, never FastMCP's child app.
        request = Request({**incoming.scope, "app": self._app})
        credential = cached_credential(request)
        contract = self._contract
        identity = request_id(request.scope)
        try:
            if credential is None or credential.platform is None or contract.group not in credential.platform.groups:
                raise ServiceError("forbidden", "Platform tool is not admitted")
            parsed = contract.inputs.model_validate(arguments)
            kwargs = {name: getattr(parsed, name) for name in parsed.model_fields_set}
            if isinstance(version := kwargs.get("expected_version"), str):
                kwargs["expected_version"] = version if version.startswith('"') else f'"{version}"'
            for name, model in contract.containers.items():
                kwargs[name] = model.model_validate(
                    {key: kwargs.pop(key) for key in model.model_fields if key in kwargs}
                )
            context = ToolContext(
                self._app.state.runtime,
                credential.principal,
                await current_workspace(credential.principal, request.headers.get("x-workspace-id")),
                request,
                credential.platform.run_id,
            )
            token = tool_response.set(True)
            try:
                result = await contract.handler(context, **kwargs)
            finally:
                tool_response.reset(token)
            value = TypeAdapter(Any).dump_python(result, mode="json")
            output = value if isinstance(value, dict) else {"result": value}
            return ToolResult(structured_content={**output, "request_id": identity})
        except ValidationError as error:
            return ToolResult(
                structured_content={
                    "error": {
                        "code": "invalid_argument",
                        "fields": [
                            {"path": list(item["loc"]), "type": item["type"]}
                            for item in error.errors(include_input=False, include_context=False)
                        ],
                    },
                    "request_id": identity,
                },
                is_error=True,
            )
        except ServiceError as error:
            return ToolResult(
                structured_content={
                    "error": {"code": error.code, "message": error.message, "details": error.details},
                    "request_id": identity,
                },
                is_error=True,
            )
