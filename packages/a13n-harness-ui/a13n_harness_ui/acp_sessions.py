"""Harness UI ownership of resident outbound ACP sessions."""

from __future__ import annotations

import asyncio
import os
import shutil
from collections.abc import AsyncIterator, Mapping
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

from a13n_harness.acp import AcpChannel, AcpError, AcpHostToolBridge, AcpSessionRuntime, LocalStdioTransport
from acp import RequestError, schema
from pydantic import BaseModel, Field

from a13n_harness_ui.configuration import canonical_digest
from a13n_harness_ui.configuration.models import ACP_AGENT_PRESETS, AcpAgentPreset, AcpAgentResource
from a13n_harness_ui.errors import ThreadError
from a13n_harness_ui.surfaces import SurfaceModel


class AcpCommand(SurfaceModel):
    name: str
    description: str
    hint: str | None = None


def acp_commands(runtime: AcpSessionRuntime) -> tuple[AcpCommand, ...] | None:
    commands = runtime.client.available_commands
    if commands is None:
        return None
    return tuple(
        AcpCommand(
            name=command.name, description=command.description, hint=command.input.root.hint if command.input else None
        )
        for command in commands
    )


class AcpSessionState(BaseModel):
    digest: str
    thread_id: str | None = None
    session_id: str | None = None
    prompted: bool = True
    context_window: tuple[int, int] | None = None
    peer_title: str | None = None
    commands: tuple[AcpCommand, ...] = ()
    options: dict[str, str | bool] = Field(default_factory=dict)
    explicit_options: frozenset[str] = frozenset()
    synced_messages: int = 0
    synced_message_key: str | None = None


class AcpOptionValue(SurfaceModel):
    value: str
    name: str


class AcpOption(SurfaceModel):
    id: str
    name: str
    description: str | None = None
    category: str | None = None
    value: str | bool
    choices: tuple[AcpOptionValue, ...] = ()


class AcpOptionChange(SurfaceModel):
    id: str
    value: str | bool


class AcpOptionsPreview(SurfaceModel):
    scope: str
    options: tuple[AcpOption, ...]
    commands: tuple[AcpCommand, ...] = ()
    selections: dict[str, str | bool] = Field(default_factory=dict)


class AcpOptionSelection(SurfaceModel):
    scope: str
    values: dict[str, str | bool] = Field(default_factory=dict, max_length=64)


class AcpOptionPreviewChange(SurfaceModel):
    selection: AcpOptionSelection
    change: AcpOptionChange | None = None


def acp_options_scope(resource: AcpAgentResource, cwd: str) -> str:
    return canonical_digest({"resource": resource.model_dump(mode="json"), "cwd": cwd})


# These variables belong to the Host's stdio MCP child, not the ACP executable.
_BRIDGE_ENVIRONMENT = frozenset({"A13N_ACP_MCP_URL", "A13N_ACP_MCP_TOKEN"})


def local_environment(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    inherited = os.environ if environ is None else environ
    environment = {key: value for key, value in inherited.items() if key not in _BRIDGE_ENVIRONMENT}
    if os.name == "posix":
        user_bin = str(Path(environment.get("HOME") or Path.home()) / ".local" / "bin")
        path = environment.get("PATH", os.defpath).split(os.pathsep)
        if user_bin not in path and Path(user_bin).is_dir():
            environment["PATH"] = os.pathsep.join([*path, user_bin])
    return environment


class AcpPresetView(SurfaceModel):
    id: AcpAgentPreset
    name: str
    executable: str | None = None
    detected_path: str | None = None
    args: tuple[str, ...] = ()
    install_url: str | None = None


def acp_presets() -> tuple[AcpPresetView, ...]:
    path = local_environment().get("PATH", os.defpath)
    metadata = {
        "antigravity": ("Antigravity", "https://antigravity.google/docs/getting-started?tab=cli"),
        "claude_code": ("Claude Code", "https://github.com/agentclientprotocol/claude-agent-acp"),
        "codex": ("Codex", "https://github.com/agentclientprotocol/codex-acp"),
        "opencode": ("OpenCode", "https://opencode.ai/docs/"),
        "gemini_cli": ("Gemini CLI", "https://github.com/google-gemini/gemini-cli"),
    }
    return (
        *(
            AcpPresetView(
                id=key,
                name=metadata[key][0],
                executable=command,
                detected_path=shutil.which(command, path=path),
                args=("acp", "--dangerously-skip-permissions", "--process-per-session")
                if key == "antigravity"
                else args,
                install_url=metadata[key][1],
            )
            for key, (command, args) in ACP_AGENT_PRESETS.items()
        ),
        AcpPresetView(id="custom", name="Custom ACP Agent"),
    )


def local_transport(
    resource: AcpAgentResource, cwd: str, *, environ: Mapping[str, str] | None = None
) -> LocalStdioTransport:
    executable, *arguments = resource.argv()
    return LocalStdioTransport(executable, cwd, tuple(arguments), environment=local_environment(environ))


def acp_options(runtime: AcpSessionRuntime) -> tuple[AcpOption, ...]:
    return tuple(
        AcpOption(
            id=item.id,
            name=item.name,
            description=item.description,
            category=item.category,
            value=item.current_value,
            choices=tuple(
                AcpOptionValue(value=value.value, name=value.name)
                for choice in item.options
                for value in (choice.options if isinstance(choice, schema.SessionConfigSelectGroup) else (choice,))
            )
            if isinstance(item, schema.SessionConfigOptionSelect)
            else (),
        )
        for item in runtime.config_options
    )


@dataclass(slots=True)
class _Resident:
    digest: str
    runtime: AcpSessionRuntime
    host_tools: AcpHostToolBridge
    resources: AsyncExitStack


@dataclass(slots=True)
class _ThreadSession:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    resident: _Resident | None = None

    async def retire(self) -> None:
        if self.resident is not None:
            resident, self.resident = self.resident, None
            await resident.resources.aclose()


class AcpSessions:
    """Keep one connection per Thread and retire it when its authority changes."""

    def __init__(self) -> None:
        self._threads: dict[str, _ThreadSession] = {}
        self._closed = False

    @asynccontextmanager
    async def lease(
        self,
        *,
        thread_id: str,
        digest: str,
        resource: AcpAgentResource,
        cwd: str,
        saved_session_id: str | None,
        channel: AcpChannel | None = None,
        interactive: bool = False,
    ) -> AsyncIterator[tuple[AcpSessionRuntime, AcpHostToolBridge]]:
        if self._closed:
            raise AcpError("acp_sessions_closed", "The ACP session owner is closed.")
        entry = self._threads.setdefault(thread_id, _ThreadSession())
        async with entry.lock:
            if self._closed or self._threads.get(thread_id) is not entry:
                raise AcpError("acp_session_retired", "The ACP session was retired before admission.")
            resident = entry.resident
            if resident is not None and (resident.digest != digest or resident.runtime.session_id != saved_session_id):
                await entry.retire()
                resident = None
            if resident is None:
                bridge = AcpHostToolBridge(())
                transport = local_transport(resource, cwd) if channel is None else channel
                async with AsyncExitStack() as resources:
                    capabilities = schema.ClientCapabilities(
                        fs=schema.FileSystemCapabilities(read_text_file=True, write_text_file=True),
                        terminal=True,
                        elicitation=(
                            schema.ElicitationCapabilities(
                                form=schema.ElicitationFormCapabilities(), url=schema.ElicitationUrlCapabilities()
                            )
                            if interactive
                            else None
                        ),
                    )
                    runtime = await resources.enter_async_context(
                        AcpSessionRuntime.open(
                            transport, host_tools=bridge, same_network=channel is None, capabilities=capabilities
                        )
                    )
                    resident = entry.resident = _Resident(digest, runtime, bridge, resources.pop_all())
            try:
                yield resident.runtime, resident.host_tools
            except BaseException as exc:
                # A rejected attachment never reaches the peer. Other failures
                # may have changed the external session, so retire the connection.
                if not (isinstance(exc, AcpError) and exc.code == "acp_input_unsupported"):
                    await entry.retire()
                raise

    def matches(self, thread_id: str, digest: str, session_id: str | None) -> bool:
        entry = self._threads.get(thread_id)
        resident = entry.resident if entry is not None else None
        return (
            session_id is not None
            and resident is not None
            and resident.digest == digest
            and resident.runtime.session_id == session_id
        )

    def options(self, thread_id: str) -> tuple[AcpOption, ...]:
        entry = self._threads.get(thread_id)
        if entry is None or entry.resident is None:
            return ()
        return acp_options(entry.resident.runtime)

    def commands(self, thread_id: str) -> tuple[AcpCommand, ...] | None:
        entry = self._threads.get(thread_id)
        return acp_commands(entry.resident.runtime) if entry is not None and entry.resident is not None else None

    async def set_option(self, thread_id: str, change: AcpOptionChange) -> tuple[AcpOption, ...]:
        entry = self._threads.get(thread_id)
        if entry is None or entry.resident is None:
            raise ThreadError("Start a session with this ACP Agent first.", code="acp_session_missing")
        async with entry.lock:
            if entry.resident is None:
                raise ThreadError("The ACP session has retired.", code="acp_session_missing")
            option = next((item for item in self.options(thread_id) if item.id == change.id), None)
            if option is None or (
                not isinstance(change.value, bool)
                if isinstance(option.value, bool)
                else change.value not in {choice.value for choice in option.choices}
            ):
                raise ThreadError("The ACP Agent did not offer that option value.", code="acp_option_invalid")
            try:
                async with asyncio.timeout(15):
                    await entry.resident.runtime.set_config_option(change.id, change.value)
            except (AcpError, RequestError, ConnectionError, TimeoutError) as error:
                raise ThreadError(
                    "The ACP Agent did not confirm the option change.", code="acp_option_failed"
                ) from error
            return self.options(thread_id)

    async def retire(self, thread_id: str) -> None:
        entry = self._threads.get(thread_id)
        if entry is not None:
            async with entry.lock:
                if self._threads.get(thread_id) is entry:
                    del self._threads[thread_id]
                    await entry.retire()

    async def close(self) -> None:
        self._closed = True
        await asyncio.gather(*(self.retire(thread_id) for thread_id in tuple(self._threads)))
