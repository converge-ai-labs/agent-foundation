"""One external ACP session, with a separate binding for each active turn."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from dataclasses import dataclass
from typing import Any, Literal, cast
from uuid import uuid4

from acp import Client, RequestError, connect_to_agent, schema
from acp.client.connection import ClientSideConnection

from a13n_harness.environment.models import EnvironmentError

from .client import AcpEnvironmentClient, AcpRunBinding, ConfigOption
from .mcp import AcpHostToolBridge, AcpHostToolScope
from .transport import AcpChannel, AcpError, AcpTransport, LocalStdioTransport, StreamChannel

type McpServer = schema.HttpMcpServer | schema.SseMcpServer | schema.AcpMcpServer | schema.McpServerStdio
type PromptBlock = (
    schema.TextContentBlock
    | schema.ImageContentBlock
    | schema.AudioContentBlock
    | schema.ResourceContentBlock
    | schema.EmbeddedResourceContentBlock
)
type SteeringOutcome = Literal["accepted", "not_delivered", "unknown", "unsupported"]


def connect_streams(
    client: Client, *, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> ClientSideConnection:
    return connect_to_agent(client, StreamChannel(reader, writer))


@dataclass(frozen=True)
class AcpSessionSetup:
    cwd: str
    additional_directories: tuple[str, ...] = ()
    instructions: tuple[str, ...] = ()
    mcp_servers: tuple[McpServer, ...] = ()


class _SessionWire:
    """Adapt lifecycle fields and authentication defaults for the pinned SDK."""

    def __init__(self, channel: AcpChannel) -> None:
        self.channel = channel
        self.system_prompt = False
        self.instructions: tuple[str, ...] = ()
        self._initialize_id: object = object()
        self.error: Exception | None = None

    async def send(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        if method == "initialize":
            self._initialize_id = message["id"]
        elif method in {"session/new", "session/load", "session/resume"} and self.system_prompt and self.instructions:
            params = dict(message["params"])
            params["systemPrompt"] = {
                "mode": "append",
                "content": [{"type": "text", "text": text} for text in self.instructions],
            }
            message = {**message, "params": params}
        await self.channel.send(message)

    async def receive(self) -> dict[str, Any] | None:
        try:
            message = await self.channel.receive()
        except (AcpError, OSError, EnvironmentError) as exc:
            self.error = exc
            return None
        if message is not None and message.get("id") == self._initialize_id and "result" in message:
            result = message["result"]
            if isinstance(result, dict):
                capabilities = result.get("agentCapabilities") or {}
                sessions = capabilities.get("sessionCapabilities") or {} if isinstance(capabilities, dict) else {}
                self.system_prompt = isinstance(sessions, dict) and isinstance(sessions.get("systemPrompt"), dict)
                methods = result.get("authMethods")
                if isinstance(methods, list):
                    # An omitted type means agent; the SDK's union otherwise selects terminal first.
                    methods = [
                        {"type": "agent", **method}
                        for method in methods
                        if isinstance(method, dict) and method.get("type", "agent") in ("agent", "terminal")
                    ]
                    message = {**message, "result": {**result, "authMethods": methods}}
        return message

    @asynccontextmanager
    async def report_errors(self) -> AsyncIterator[None]:
        try:
            yield
        except ConnectionError as exc:
            if self.error is not None:
                raise self.error from exc
            raise

    async def close(self) -> None:
        await self.channel.close()


@asynccontextmanager
async def _provided_channel(channel: AcpChannel) -> AsyncIterator[AcpChannel]:
    try:
        yield channel
    finally:
        await channel.close()


class AcpSessionRuntime:
    """A process-local ACP connection; callers own persistent identity and recovery policy."""

    def __init__(
        self,
        client: AcpEnvironmentClient,
        connection: ClientSideConnection,
        wire: _SessionWire,
        initialization: schema.InitializeResponse,
    ) -> None:
        self.client = client
        self.connection = connection
        self._wire = wire
        self.initialization = initialization
        self._turn: str | None = None
        self._stopping = False
        self._broken = False
        self._initializing = False
        self._initial_instructions: tuple[str, ...] = ()
        profile = (initialization.field_meta or {}).get("dev.a13n.harness-ui")
        self._a13n_steering = isinstance(profile, dict) and profile.get("version") == 1
        self._host_server: McpServer | None = None

    @classmethod
    @asynccontextmanager
    async def open(
        cls,
        transport: AcpTransport | AcpChannel,
        *,
        client: AcpEnvironmentClient | None = None,
        capabilities: schema.ClientCapabilities | None = None,
        host_tools: AcpHostToolBridge | None = None,
        same_network: bool | None = None,
    ) -> AsyncIterator[AcpSessionRuntime]:
        client = client or AcpEnvironmentClient()
        manager = transport.open() if isinstance(transport, AcpTransport) else _provided_channel(transport)
        async with manager as channel:
            wire = _SessionWire(channel)
            connection = connect_to_agent(cast(Client, client), wire, use_unstable_protocol=True)
            async with connection, wire.report_errors():
                capabilities = capabilities or schema.ClientCapabilities(
                    fs=schema.FileSystemCapabilities(read_text_file=True, write_text_file=True),
                    terminal=True,
                )
                initialization = await connection.initialize(protocol_version=1, client_capabilities=capabilities)
                if initialization.protocol_version != 1:
                    raise AcpError("acp_protocol_unsupported", "The agent did not negotiate ACP v1.")
                runtime = cls(client, connection, wire, initialization)
                async with AsyncExitStack() as resources:
                    if host_tools is not None:
                        await resources.enter_async_context(host_tools._connection())
                        mcp = runtime.capabilities.mcp_capabilities or schema.McpCapabilities()
                        local = isinstance(transport, LocalStdioTransport) if same_network is None else same_network
                        if local:
                            runtime._host_server = await resources.enter_async_context(
                                host_tools._loopback(http=bool(mcp.http))
                            )
                        elif mcp.acp:
                            runtime._host_server = host_tools._acp_server()
                        else:
                            raise AcpError(
                                "acp_host_tools_unsupported",
                                "A remote ACP agent requires MCP-over-ACP to use host tools.",
                            )
                    client._bridge = host_tools
                    try:
                        yield runtime
                    finally:
                        client._bridge = None

    @property
    def capabilities(self) -> schema.AgentCapabilities:
        return self.initialization.agent_capabilities or schema.AgentCapabilities()

    @property
    def session_id(self) -> str | None:
        return self.client.session_id

    @property
    def config_options(self) -> tuple[ConfigOption, ...]:
        return tuple(self.client.config_options)

    async def open_session(
        self, setup: AcpSessionSetup, *, saved_session_id: str | None = None, discovery: AcpHostToolScope | None = None
    ) -> str:
        if self.session_id is not None or self._initializing or self.client.binding is not None:
            raise AcpError("acp_session_exists", "This ACP connection already owns a session.")
        capabilities = self.capabilities
        session_capabilities = capabilities.session_capabilities
        self._wire.instructions = tuple(text for text in setup.instructions if text)
        servers = list(setup.mcp_servers)
        if self._host_server is not None:
            servers.append(self._host_server)
        additional_directories = (
            list(setup.additional_directories)
            if session_capabilities and session_capabilities.additional_directories is not None
            else None
        )
        self._initializing = True
        try:
            if self.client._bridge is not None:
                if discovery is None:
                    raise AcpError("acp_mcp_discovery_required", "Supply a prepared host tool scope for discovery.")
                await self.client._bridge._discover(discovery)
            elif discovery is not None:
                raise AcpError("acp_host_tools_unconfigured", "Open the ACP connection with a host tool bridge.")
            if saved_session_id is None:
                response = await self.connection.new_session(
                    cwd=setup.cwd, mcp_servers=servers, additional_directories=additional_directories
                )
                session_id = response.session_id
            elif session_capabilities and session_capabilities.resume is not None:
                response = await self.connection.resume_session(
                    session_id=saved_session_id,
                    cwd=setup.cwd,
                    mcp_servers=servers,
                    additional_directories=additional_directories,
                )
                session_id = saved_session_id
            elif capabilities.load_session:
                response = await self.connection.load_session(
                    session_id=saved_session_id,
                    cwd=setup.cwd,
                    mcp_servers=servers,
                    additional_directories=additional_directories,
                )
                session_id = saved_session_id
            else:
                raise AcpError("acp_resume_unsupported", "This agent cannot recover its saved ACP session.")
            self.client.session_id = session_id
            self.client.config_options = list(response.config_options or ()) if response is not None else []
            if saved_session_id is None and not self._wire.system_prompt:
                self._initial_instructions = self._wire.instructions
            return session_id
        finally:
            self._initializing = False
            self._wire.instructions = ()

    def _idle_session(self) -> str:
        if self._broken:
            raise AcpError("acp_connection_closed", "Recover the session on a new ACP connection.")
        if self.session_id is None:
            raise AcpError("acp_session_missing", "Open an ACP session before running a prompt.")
        if self._turn is not None or self.client.binding is not None or self._initializing:
            raise AcpError("acp_session_busy", "The ACP session already has an active operation.")
        return self.session_id

    async def set_config_option(self, config_id: str, value: str | bool) -> tuple[ConfigOption, ...]:
        session_id = self._idle_session()
        self._initializing = True
        try:
            response = await self.connection.set_config_option(config_id=config_id, session_id=session_id, value=value)
            self.client.config_options = response.config_options
            return self.config_options
        finally:
            self._initializing = False

    async def authenticate(self, method_id: str, *, binding: AcpRunBinding | None = None) -> None:
        if self.client.binding is not None or self._initializing or self._turn is not None:
            raise AcpError("acp_session_busy", "The ACP connection already has an active operation.")
        active = binding or AcpRunBinding()
        self.client.binding = active
        try:
            await self.connection.authenticate(method_id)
        finally:
            await self.client._unbind(active)

    async def logout(self) -> None:
        if self.client.binding is not None or self._initializing or self._turn is not None:
            raise AcpError("acp_session_busy", "The ACP connection already has an active operation.")
        if not self.capabilities.auth or self.capabilities.auth.logout is None:
            raise AcpError("acp_logout_unsupported", "The agent does not advertise logout.")
        active = AcpRunBinding()
        self.client.binding = active
        try:
            await self.connection.logout()
        finally:
            await self.client._unbind(active)

    async def prompt(
        self, content: Sequence[PromptBlock], *, binding: AcpRunBinding, context: Sequence[str] = ()
    ) -> schema.PromptResponse:
        session_id = self._idle_session()
        self.client.binding = binding
        self.client._update_error = None
        self._turn = uuid4().hex
        self._stopping = False
        blocks = [schema.TextContentBlock(text=text) for text in (*self._initial_instructions, *context)]
        self._initial_instructions = ()
        if self._a13n_steering:
            metadata: dict[str, Any] = {"dev.a13n.harness-ui": {"version": 1, "turnId": self._turn}}
        else:
            metadata = {}
        try:
            if binding.host_tools is not None:
                if self.client._bridge is None:
                    raise AcpError("acp_host_tools_unconfigured", "Open the ACP connection with a host tool bridge.")
                self.client._bridge._bind(binding.host_tools, self._turn)
            response = await self.connection.prompt(session_id=session_id, prompt=[*blocks, *content], **metadata)
            if self.client._update_error is not None:
                raise self.client._update_error
            return response
        except asyncio.CancelledError:
            self.client._revoke()
            if not self._stopping:
                with suppress(ConnectionError, TimeoutError):
                    async with asyncio.timeout(2):
                        await self.connection.cancel(session_id)
            # Join admitted callbacks before SDK shutdown can cancel their cleanup again.
            self._broken = True
            raise
        finally:
            try:
                await self.client._unbind(binding)
            finally:
                if self._broken:
                    await self.connection.close()
                self._turn = None

    async def cancel(self) -> bool:
        if self._turn is None or self._stopping or self.session_id is None:
            return False
        self._stopping = True
        # Fence approvals and host-tool dispatch immediately, but keep observation through settlement.
        self.client._revoke()
        await self.connection.cancel(self.session_id)
        return True

    @property
    def supports_steering(self) -> bool:
        return self._a13n_steering

    async def steer(self, text: str) -> SteeringOutcome:
        if self._turn is None or self._stopping or self.session_id is None:
            return "not_delivered"
        if not self._a13n_steering:
            return "unsupported"
        try:
            result = await self.connection.ext_method(
                "a13n/steer",
                {
                    "sessionId": self.session_id,
                    "turnId": self._turn,
                    "inputId": uuid4().hex,
                    "text": text,
                },
            )
        except ConnectionError:
            return "unknown"
        except RequestError as exc:
            return "unsupported" if exc.code == -32601 else "unknown"
        match result.get("delivery"):
            case "accepted":
                return "accepted"
            case "not_delivered":
                return "not_delivered"
            case _:
                return "unknown"
