"""Harness UI ownership of resident outbound ACP sessions."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Mapping
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field

from a13n_harness.acp import AcpChannel, AcpError, AcpHostToolBridge, AcpSessionRuntime, LocalStdioTransport
from acp import schema

from a13n_harness_ui.configuration.models import AcpAgentResource

# Start the executable with the user's ordinary path, home, and OS support. Host
# credential variables are not a provider authentication mechanism.
_LOCAL_ENVIRONMENT = frozenset(
    {
        "APPDATA",
        "COMSPEC",
        "HOME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "LOCALAPPDATA",
        "PATH",
        "PATHEXT",
        "SHELL",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "TMPDIR",
        "USER",
        "USERPROFILE",
        "WINDIR",
        "XDG_CACHE_HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_RUNTIME_DIR",
        "XDG_STATE_HOME",
    }
)


def local_transport(
    resource: AcpAgentResource, cwd: str, *, environ: Mapping[str, str] | None = None
) -> LocalStdioTransport:
    executable, *arguments = resource.argv()
    inherited = os.environ if environ is None else environ
    return LocalStdioTransport(
        executable,
        cwd,
        tuple(arguments),
        environment={key: value for key, value in inherited.items() if key.upper() in _LOCAL_ENVIRONMENT},
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
