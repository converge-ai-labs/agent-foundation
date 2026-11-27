"""Operator-initiated ACP authentication, separate from conversation Runs."""

from __future__ import annotations

import asyncio
import os
import re
import signal
from contextlib import suppress
from pathlib import Path
from shutil import which
from tempfile import TemporaryDirectory
from typing import Any, Literal
from urllib.parse import urlsplit

from a13n_harness.acp import AcpError, AcpRunBinding, AcpSessionRuntime, AcpSessionSetup, LocalStdioTransport
from acp import RequestError, schema
from mcp.types import ElicitRequestFormParams
from pydantic import BaseModel, Field, model_validator

from a13n_harness_ui.acp_inputs import AcpInputs
from a13n_harness_ui.acp_sessions import (
    MODEL_OPTION_GRACE_SECONDS,
    AcpOptionPreviewChange,
    AcpOptionsPreview,
    acp_commands,
    acp_options,
    acp_options_scope,
    local_transport,
)
from a13n_harness_ui.configuration.models import AcpAgentCandidate, AcpAgentResource
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


class AcpPresetAuthRequest(AcpAgentCandidate, AcpAuthRequest):
    pass


class AcpAuthMethod(BaseModel):
    id: str
    name: str
    description: str | None = None
    kind: Literal["agent", "terminal", "browser"]
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


ACP_SETUP_AUTH_SCOPE = "acp-setup-preview"

_AGY = "Antigravity ACP Server"
_GOOGLE_URL = re.compile(
    r"https://accounts\.google\.com/oauth/[^\s<>]+|https://accounts\.google\.com/o/oauth2/[^\s<>]+"
)


async def _agy_browser_login(command: str, transport: LocalStdioTransport, interaction: AcpInputs) -> bool:
    # Splash the URL in WebUI instead of opening a browser on the Host. Both
    # the OAuth exchange and credential storage remain owned by the CLI.
    with TemporaryDirectory(prefix="a13n-agy-login-") as directory:
        for name in ("xdg-open", "open"):
            shim = Path(directory) / name
            shim.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            shim.chmod(0o700)
        environment = {
            **transport.environment,
            "PATH": directory + os.pathsep + transport.environment.get("PATH", os.defpath),
        }
        process = await asyncio.create_subprocess_exec(
            command,
            "acp",
            "--login",
            cwd=transport.cwd,
            env=environment,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        assert process.stdout is not None and process.stdin is not None
        try:
            url = None
            while line := await process.stdout.readline():
                match = _GOOGLE_URL.search(line.decode(errors="replace"))
                if match is not None:
                    url = match.group()
                    break
            if url is None or urlsplit(url).netloc != "accounts.google.com":
                raise HarnessUiError("Antigravity did not provide a Google sign-in link.", code="acp_auth_failed")
            answer = await interaction.request(
                ElicitRequestFormParams(
                    message="",
                    requested_schema={
                        "type": "object",
                        "required": ["callback"],
                        "properties": {
                            "callback": {"type": "string", "minLength": 1, "maxLength": 8192},
                        },
                    },
                ),
                None,
                private_form_url=url,
            )
            if answer.action != "accept" or not answer.content:
                return False
            callback = answer.content["callback"].strip()
            if not callback or "\r" in callback or "\n" in callback:
                raise HarnessUiError("Enter one callback URL or authorization code.", code="acp_auth_failed")
            process.stdin.write((callback + "\n").encode())
            await process.stdin.drain()
            process.stdin.close()
            while await process.stdout.read(65536):
                pass  # Do not retain or log OAuth output; it may echo the callback.
            if await process.wait() != 0:
                raise HarnessUiError("Antigravity did not complete Google sign-in.", code="acp_auth_failed")
            return True
        finally:
            if process.returncode is None:
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                await process.wait()


async def preview_acp_options(
    resource: AcpAgentResource, cwd: str, request: AcpOptionPreviewChange | None
) -> AcpOptionsPreview:
    scope = acp_options_scope(resource, cwd)
    selections = {} if request is None else dict(request.selection.values)
    if request is not None and request.selection.scope != scope:
        raise HarnessUiError("The ACP Agent or workspace changed. Select its options again.", code="acp_option_invalid")
    try:
        async with asyncio.timeout(15), AcpSessionRuntime.open(local_transport(resource, cwd)) as runtime:
            session_id = await runtime.open_session(AcpSessionSetup(cwd=cwd))
            try:
                await runtime.wait_for_model_option(MODEL_OPTION_GRACE_SECONDS)
                if request is not None and request.change is not None:
                    change = request.change
                    selections[change.id] = change.value
                    model = next((option for option in acp_options(runtime) if option.is_model), None)
                    strict = frozenset({change.id}) if model and change.id == model.id else frozenset(selections)
                else:
                    strict = frozenset(selections)
                await runtime.restore_options(selections, strict=strict)
                options = acp_options(runtime)
                return AcpOptionsPreview(
                    scope=scope,
                    options=options,
                    commands=acp_commands(runtime) or (),
                    selections={item.id: item.value for item in options if selections.get(item.id) == item.value},
                )
            finally:
                capabilities = runtime.capabilities.session_capabilities
                if capabilities is not None and capabilities.close is not None:
                    await runtime.connection.close_session(session_id)
    except AcpError as exc:
        raise HarnessUiError(str(exc), code=exc.code) from exc
    except (RequestError, ConnectionError, OSError, TimeoutError) as exc:
        raise HarnessUiError("Could not read the external ACP options.", code="acp_session_unavailable") from exc


async def acp_auth(
    resource: AcpAgentResource, cwd: Path, request: AcpAuthRequest, inputs: Inputs | None, *, scope: str | None = None
) -> AcpAuthStatus:
    scope = scope or auth_input_scope(resource.id)
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
                if info is not None and info.name == _AGY and inputs is not None and os.name == "posix":
                    methods = {"agy-browser": AcpAuthMethod(id="agy-browser", name="Google", kind="browser"), **methods}
                if (
                    info is not None
                    and info.name == _AGY
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
                if method is not None and method.kind == "browser" and inputs is not None:
                    interaction = AcpInputs(inputs, scope, resource.id, lambda: None, timeout_seconds=300)
                    completed = await _agy_browser_login(command, transport, interaction)
                    return status.model_copy(update={"state": "login_completed"}) if completed else status
                if method is None or method.kind != "agent":
                    raise HarnessUiError(
                        "The Agent did not advertise this protocol login method.", code="acp_auth_method_invalid"
                    )
                binding = AcpRunBinding()
                if inputs is not None:

                    def require_active() -> None:
                        if runtime.client.binding is not binding:
                            raise HarnessUiError("The ACP login is no longer active.", code="mcp_input_stale")

                    interaction = AcpInputs(inputs, scope, resource.id, require_active)

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
