"""Operator-initiated ACP authentication, separate from conversation Runs."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from shutil import which
from typing import Any, Literal

from a13n_harness.acp import AcpError, AcpRunBinding, AcpSessionRuntime, AcpSessionSetup
from acp import RequestError, schema
from pydantic import BaseModel, Field, model_validator

from a13n_harness_ui.acp_inputs import AcpInputs
from a13n_harness_ui.acp_sessions import local_transport
from a13n_harness_ui.configuration.models import AcpAgentResource
from a13n_harness_ui.errors import HarnessUiError
from a13n_harness_ui.mcp_runtime.inputs import Inputs


class AcpAuthRequest(BaseModel):
    action: Literal["probe", "login", "logout"]
    method_id: str | None = Field(default=None, min_length=1, max_length=256)

    @model_validator(mode="after")
    def _validate_method(self) -> AcpAuthRequest:
        if (self.action == "login") != (self.method_id is not None):
            raise ValueError("Select an authentication method only when logging in.")
        return self


class AcpAuthMethod(BaseModel):
    id: str
    name: str
    description: str | None = None
    kind: Literal["agent", "terminal"]
    argv: tuple[str, ...] = ()
    environment_keys: tuple[str, ...] = ()


class AcpAuthStatus(BaseModel):
    state: Literal["executable_missing", "ready", "auth_required", "login_completed", "logout_completed"]
    agent_name: str | None = None
    agent_version: str | None = None
    default_model: str | None = None
    methods: tuple[AcpAuthMethod, ...] = ()
    logout_supported: bool = False


def auth_input_scope(agent_id: str) -> str:
    return f"acp-auth/{agent_id}"


async def acp_auth(
    resource: AcpAgentResource, cwd: Path, request: AcpAuthRequest, inputs: Inputs | None
) -> AcpAuthStatus:
    transport = local_transport(resource, str(cwd))
    executable = Path(transport.executable)
    if executable.parent != Path(".") or os.path.dirname(transport.executable):
        command = which(str(cwd / executable))
    else:
        command = which(transport.executable, path=transport.environment.get("PATH", os.defpath))
    if command is None:
        return AcpAuthStatus(state="executable_missing")

    capabilities = schema.ClientCapabilities(
        elicitation=(
            schema.ElicitationCapabilities(
                form=schema.ElicitationFormCapabilities(), url=schema.ElicitationUrlCapabilities()
            )
            if inputs
            else None
        )
    )
    try:
        async with asyncio.timeout(310 if request.action == "login" else 15):
            async with AcpSessionRuntime.open(transport, capabilities=capabilities) as runtime:
                methods = {
                    method.id: AcpAuthMethod(
                        id=method.id,
                        name=method.name,
                        description=method.description,
                        kind="terminal",
                        argv=(transport.executable, *transport.arguments, *(method.args or ())),
                        environment_keys=tuple(sorted(method.env or {})),
                    )
                    if isinstance(method, schema.AuthMethodTerminal)
                    else AcpAuthMethod(id=method.id, name=method.name, description=method.description, kind="agent")
                    for method in runtime.initialization.auth_methods or ()
                }
                info = runtime.initialization.agent_info
                if (
                    info is not None
                    and info.name == "Antigravity ACP Server"
                    and not any(method.kind == "terminal" for method in methods.values())
                ):
                    methods["agy-terminal"] = AcpAuthMethod(
                        id="agy-terminal",
                        name="Terminal login",
                        kind="terminal",
                        argv=(transport.executable, *transport.arguments, "--login"),
                    )
                status = AcpAuthStatus(
                    state="auth_required",
                    agent_name=info.name if info else None,
                    agent_version=info.version if info else None,
                    methods=tuple(methods.values()),
                    logout_supported=runtime.capabilities.auth is not None
                    and runtime.capabilities.auth.logout is not None,
                )
                if request.action == "probe":
                    try:
                        session_id = await runtime.open_session(AcpSessionSetup(cwd=str(cwd)))
                    except AcpError as exc:
                        if exc.code != "acp_auth_required":
                            raise
                        return status
                    session_capabilities = runtime.capabilities.session_capabilities
                    if session_capabilities is not None and session_capabilities.close is not None:
                        await runtime.connection.close_session(session_id)
                    model = next(
                        (
                            option
                            for option in runtime.config_options
                            if isinstance(option, schema.SessionConfigOptionSelect)
                            and (option.category == "model" or option.id == "model")
                        ),
                        None,
                    )
                    default_model = None
                    if model is not None:
                        default_model = next(
                            (
                                choice.name
                                for entry in model.options
                                for choice in (
                                    entry.options if isinstance(entry, schema.SessionConfigSelectGroup) else (entry,)
                                )
                                if choice.value == model.current_value
                            ),
                            model.current_value,
                        )
                    return status.model_copy(update={"state": "ready", "default_model": default_model})
                if request.action == "logout":
                    await runtime.logout()
                    return status.model_copy(update={"state": "logout_completed"})
                assert request.method_id is not None
                method = methods.get(request.method_id)
                if method is None or method.kind != "agent":
                    raise HarnessUiError(
                        "The Agent did not advertise this protocol login method.", code="acp_auth_method_invalid"
                    )
                binding = AcpRunBinding()
                if inputs is not None:

                    def require_active() -> None:
                        if runtime.client.binding is not binding:
                            raise HarnessUiError("The ACP login is no longer active.", code="mcp_input_stale")

                    interaction = AcpInputs(inputs, auth_input_scope(resource.id), resource.id, require_active)

                    async def elicitation(
                        message: str, mode: str, params: dict[str, Any]
                    ) -> schema.CreateElicitationResponse:
                        return await interaction.elicitation(message, mode, params)

                    binding.elicitation = elicitation
                await runtime.authenticate(request.method_id, binding=binding)
                return status.model_copy(update={"state": "login_completed"})
    except AcpError as exc:
        raise HarnessUiError(str(exc), code=exc.code) from exc
    except RequestError as exc:
        raise HarnessUiError(
            "The external Agent rejected the authentication operation.", code="acp_auth_failed"
        ) from exc
    except (OSError, TimeoutError) as exc:
        raise HarnessUiError(
            "The external Agent did not complete the authentication operation.", code="acp_auth_unavailable"
        ) from exc
