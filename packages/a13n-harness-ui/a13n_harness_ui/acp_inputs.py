"""Project ACP interactions onto the existing in-flight input surface."""

from __future__ import annotations

import json
import shlex
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from a13n_harness.toolsets.interaction import AskUserQuestionRequest
from acp import schema
from fastmcp.client.elicitation import ElicitResult
from mcp.types import ElicitRequestFormParams, ElicitRequestParams, ElicitRequestURLParams
from pydantic import JsonValue, TypeAdapter, ValidationError

from a13n_harness_ui.mcp_runtime.inputs import (
    Inputs,
    PermissionInputRequest,
    PermissionOptionView,
    PermissionRequestDetail,
)

_MAX_PERMISSION_ARGUMENTS_BYTES = 16 * 1024
_JSON_OBJECT_ADAPTER = TypeAdapter(dict[str, JsonValue])
_COMMAND_KEYS = frozenset({"command", "args", "CommandLine", "cmd"})
_CWD_KEYS = frozenset({"cwd", "Cwd"})
_ENV_KEYS = frozenset({"env", "environment"})


def _non_empty_str(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _extract_command(raw: dict[str, Any]) -> str | None:
    command = raw.get("command")
    args = raw.get("args")
    if isinstance(command, str) and command.strip():
        if isinstance(args, list) and all(isinstance(item, str) for item in args):
            return shlex.join([command, *args]) if args else command.strip()
        return command.strip()
    if isinstance(command, list) and command and all(isinstance(item, str) for item in command):
        return shlex.join(command)
    for key in ("CommandLine", "cmd"):
        if (text := _non_empty_str(raw.get(key))) is not None:
            return text
    return None


def _extract_env_keys(raw: dict[str, Any]) -> tuple[str, ...]:
    keys: set[str] = set()
    for key in _ENV_KEYS:
        value = raw.get(key)
        if isinstance(value, dict):
            keys.update(str(name) for name in value)
    return tuple(sorted(keys))


def _safe_arguments(raw: dict[str, Any], *, drop_command_fields: bool) -> dict[str, JsonValue] | None:
    excluded = set(_ENV_KEYS) | set(_CWD_KEYS)
    if drop_command_fields:
        excluded |= _COMMAND_KEYS
    filtered = {str(key): value for key, value in raw.items() if key not in excluded}
    if not filtered:
        return None
    try:
        validated = _JSON_OBJECT_ADAPTER.validate_python(filtered)
        encoded = json.dumps(validated, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (ValidationError, TypeError, ValueError):
        return None
    return validated if len(encoded) <= _MAX_PERMISSION_ARGUMENTS_BYTES else None


def _permission_request(
    call: schema.ToolCallUpdate,
    options: tuple[schema.PermissionOption, ...],
) -> PermissionInputRequest:
    raw = call.raw_input if isinstance(call.raw_input, dict) else None
    command = _extract_command(raw) if raw is not None else None
    cwd = next((value for key in ("cwd", "Cwd") if raw is not None and (value := _non_empty_str(raw.get(key)))), None)
    environment_keys = _extract_env_keys(raw) if raw is not None else ()
    title = _non_empty_str(call.title)
    if command is None and call.kind == "execute" and title is not None:
        command = title
    locations = tuple(
        dict.fromkeys(
            f"{location.path}:{location.line}" if location.line is not None else location.path
            for location in (call.locations or ())
            if location.path
        )
    )
    arguments = _safe_arguments(raw, drop_command_fields=command is not None) if raw is not None else None
    message = ""
    if title is not None:
        normalized = title.removeprefix("Run command ").removeprefix("Run ").strip()
        if command is None or normalized != command:
            message = title[:8192]
    detail = PermissionRequestDetail(
        tool_kind=call.kind,
        tool_name=_non_empty_str(call.name),
        command=command[:8192] if command is not None else None,
        cwd=cwd,
        environment_keys=environment_keys,
        locations=locations,
        arguments=arguments,
        options=tuple(
            PermissionOptionView(option_id=option.option_id, name=option.name, kind=option.kind) for option in options
        ),
    )
    return PermissionInputRequest(message=message, detail=detail)


@dataclass(frozen=True, slots=True)
class AcpInputs:
    inputs: Inputs
    thread_id: str
    agent_id: str
    require_active: Callable[[], None]
    run_id: str | None = None
    server_name: str | None = None
    timeout_seconds: float | None = None

    async def request(
        self,
        params: ElicitRequestParams | AskUserQuestionRequest | PermissionInputRequest,
        tool_call_id: str | None,
        *,
        private_form_url: str | None = None,
    ) -> ElicitResult[Any]:
        return await self.inputs.request(
            params,
            thread_id=self.thread_id,
            server_id=self.agent_id,
            server_name=self.server_name,
            source="acp",
            run_id=self.run_id,
            tool_call_id=tool_call_id,
            require_active=self.require_active,
            timeout_seconds=self.timeout_seconds,
            private_form_url=private_form_url,
        )

    async def permission(self, call: schema.ToolCallUpdate, options: tuple[schema.PermissionOption, ...]) -> str | None:
        if not options:
            return None
        result = await self.request(_permission_request(call, options), call.tool_call_id)
        return result.content["option"] if result.action == "accept" and result.content is not None else None

    async def elicitation(self, message: str, mode: str, params: dict[str, Any]) -> schema.CreateElicitationResponse:
        if mode == "form":
            requested = params["requested_schema"]
            request: ElicitRequestParams = ElicitRequestFormParams(
                message=message[:8192],
                requested_schema=(
                    requested.model_dump(mode="json", by_alias=True, exclude_none=True)
                    if isinstance(requested, schema.ElicitationSchema)
                    else requested
                ),
            )
        elif mode == "url":
            request = ElicitRequestURLParams.model_validate(
                {"message": message[:8192], "url": params["url"], "elicitationId": params["elicitation_id"]}
            )
        else:
            return schema.CancelElicitationResponse()
        result = await self.request(request, params.get("tool_call_id"))
        if result.action == "accept":
            return schema.AcceptElicitationResponse(content=result.content)
        if result.action == "decline":
            return schema.DeclineElicitationResponse()
        return schema.CancelElicitationResponse()
