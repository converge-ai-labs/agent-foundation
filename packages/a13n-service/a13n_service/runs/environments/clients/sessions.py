"""Authorized run scopes on Control-owned Devices; EIP owns native execution semantics."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

import anyio
from a13n_envd_client import EIPSession
from a13n_envd_client.eip.v1 import (
    METHODS,
    AllowlistDestinations,
    ComputerObserveResult,
    ComputerZoomResult,
    EgressMode,
    EgressPolicy,
    FileReaderCloseParams,
    FileReaderOpenResult,
    FileWriterAbortParams,
    FileWriterCommitParams,
    FileWriterOpenResult,
)
from a13n_envd_client.eip.v1.methods import MethodSpec
from a13n_envd_client.requester import TransferChannel
from a13n_envd_client.transport import TransferDirection
from a13n_harness.environment.models import EnvironmentAction, EnvironmentError
from a13n_harness.environment.session_recovery import EnvdSessionRecovery, EnvdSessionState
from a13n_harness.providers.environment.eip.binding import EIP_METHOD_ACTIONS
from a13n_logging import exception_details, get_logger
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from a13n_service.runs.environments.clients import authority
from a13n_service.runs.environments.clients.connections import Connection, owner_key
from a13n_service.runs.environments.clients.routing import Request
from a13n_service.runs.environments.clients.streaming import endpoint
from a13n_service.runs.tool_executions.routing import Caller
from a13n_service.tenancy.credentials import new_secret

if TYPE_CHECKING:
    from a13n_service.runs.runtime import Runtime

logger = get_logger(__name__)

# These operate only on resources already owned by the selected Session. Device
# methods, Session open/attach and transfer setup have explicit admission below.
_SCOPE_METHODS = frozenset(
    {
        "environment.describe",
        "environment.readiness",
        "session.keepalive",
        "receipt.get",
        "operation.cancel",
        "file.close_reader",
        "file.abort_writer",
        "file.commit_writer",
        "computer.close_observation",
    }
)


class Open(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    working_directory: str
    required_methods: tuple[str, ...] = Field(default=(), max_length=128)
    recovery: EnvdSessionState | None = None


@dataclass(slots=True)
class Transfer:
    channel: TransferChannel
    direction: TransferDirection
    actions: frozenset[EnvironmentAction]
    caller: Caller
    attached: bool = False
    finished: bool = False


@dataclass(slots=True)
class Scope:
    request: Request
    connection: Connection
    session: EIPSession
    transfer_token: str = field(repr=False)
    # A lost scope.open response must not keep an unclaimed Session alive for an entire run.
    first_use_until: float | None
    transfers: dict[str, Transfer] = field(default_factory=dict)
    callers: dict[tuple[str, str, str | None], Caller] = field(default_factory=dict)

    def remember(self, caller: Caller) -> None:
        self.callers[(caller.attempt_id, caller.worker_id, caller.execution_id)] = caller


def binding_key(environment: str, generation: int, session: str) -> str:
    return f"a13n:client-session:{environment}:{generation}:{session}"


_REMEMBER = """
local current = redis.call('GET', KEYS[1])
if current and current ~= ARGV[1] then return 0 end
redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[2])
return 1
"""


class Sessions:
    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime
        self.scopes: dict[str, Scope] = {}
        self.opening: set[str] = set()
        self.closed = False

    async def serve(self) -> None:
        connections = self.runtime.client_connections
        try:
            while True:
                try:
                    if connections.recovering:
                        await connections.disconnect()
                        await self._retire_all()
                        connections.recovering = False
                    async with asyncio.TaskGroup() as group:
                        group.create_task(
                            self.runtime.client_routing.serve(
                                connections.node,
                                self.dispatch,
                                on_subscription_lost=connections.disconnect,
                            )
                        )
                        group.create_task(self.renew())
                except Exception as error:
                    # The TaskGroup has joined all old dispatches. Retire their
                    # carriers before admitting work on a fresh subscription.
                    connections.recovering = True
                    self.runtime.client_routing.ready.clear()
                    logger.error(
                        "Client environment service restarting",
                        extra={"exception_details": exception_details(error)},
                    )
                    await asyncio.sleep(1)
        finally:
            try:
                await self.close()
            finally:
                await self.runtime.client_connections.close()

    async def connection(self, request: Request) -> Connection:
        connection = self.runtime.client_connections.entries.get(request.environment_id)
        if (
            self.closed
            or self.runtime.client_connections.recovering
            or connection is None
            or connection.device is None
            or connection.device.is_closed
            or connection.epoch != request.epoch
            or await self.runtime.redis.get(owner_key(request.environment_id)) != request.epoch
        ):
            raise EnvironmentError(
                "Client environment is offline or its connection changed.",
                code="environment_unavailable",
                details={"dispatch_stage": "pre_dispatch", "client_connection_failed": True},
            )
        return connection

    async def open(self, request: Request) -> dict[str, JsonValue]:
        try:
            body = Open.model_validate(request.params)
        except ValidationError as error:
            raise EnvironmentError(
                "Invalid client Session request.",
                code="environment_request_invalid",
                details={"dispatch_stage": "pre_dispatch"},
            ) from error
        stream_url = endpoint(self.runtime)
        connection = await self.connection(request)
        mount = await authority.resolve(self.runtime, request, connection.attachment, frozenset())
        if body.working_directory != mount.working_directory:
            raise EnvironmentError(
                "Client directory differs from the frozen mount.",
                code="environment_denied",
                details={"dispatch_stage": "pre_dispatch"},
            )
        if request.scope_id in self.scopes or request.scope_id in self.opening:
            raise EnvironmentError(
                "Client execution scope already exists.",
                code="environment_conflict",
                details={"dispatch_stage": "pre_dispatch"},
            )
        if len(self.scopes) + len(self.opening) >= self.runtime.settings.client_environments.max_sessions:
            raise EnvironmentError(
                "Client Session capacity is exhausted.",
                code="environment_unavailable",
                details={"dispatch_stage": "pre_dispatch"},
            )
        self.opening.add(request.scope_id)
        session = None
        try:
            assert connection.device is not None
            hint = body.recovery
            controlled = connection.device.descriptor.boundary.egress == EgressMode.CONTROLLED
            if hint is not None and not controlled:
                key = binding_key(request.environment_id, hint.descriptor.generation, hint.descriptor.session_id)
                occupied = any(
                    scope.connection is connection and scope.session.session_id == hint.descriptor.session_id
                    for scope in self.scopes.values()
                )
                if (
                    not occupied
                    and await self.runtime.redis.get(key)
                    == f"{request.caller.run_id}:{connection.attachment.credential_version}"
                ):
                    session = await EnvdSessionRecovery(hint).attach(
                        connection.device,
                        target=request.environment_id,
                        expected_device_id=connection.attachment.device_id,
                        working_directory=body.working_directory,
                        required_methods=frozenset(body.required_methods),
                        expected_boundary=None,
                    )
            if session is None:
                session = await connection.device.open_session(
                    working_directory=body.working_directory,
                    # Registration does not grant network destinations or inject secrets.
                    egress=EgressPolicy(destinations=AllowlistDestinations(mode="allowlist")) if controlled else None,
                    required_methods=body.required_methods,
                    readiness_timeout=self.runtime.settings.client_environments.initialization_seconds,
                )
            # Recheck after native preparation, before publishing access to it.
            if session.descriptor.working_directory != mount.working_directory:
                raise EnvironmentError(
                    "Client Session did not open the frozen working directory.", code="environment_stale_mount"
                )
            await self.connection(request)
            await authority.resolve(self.runtime, request, connection.attachment, frozenset())
            scope = Scope(
                request,
                connection,
                session,
                new_secret(),
                asyncio.get_running_loop().time() + self.runtime.settings.client_environments.request_seconds,
            )
            await self.remember_binding(scope)
            await self.connection(request)
            scope.remember(request.caller.model_copy(update={"execution_id": None}))
            scope.remember(request.caller)
            self.scopes[request.scope_id] = scope
            return {
                "descriptor": session.descriptor.model_dump(mode="json"),
                "device_descriptor": connection.device.descriptor.model_dump(mode="json"),
                "transfer_token": scope.transfer_token,
                "transfer_url": stream_url,
            }
        except BaseException:
            if session is not None:
                with anyio.move_on_after(5, shield=True):
                    await session.abort()
            raise
        finally:
            self.opening.discard(request.scope_id)

    async def authorize(self, request: Request, actions: frozenset[EnvironmentAction]) -> Scope:
        scope = self.scopes.get(request.scope_id)
        connection = await self.connection(request)
        if (
            scope is None
            or scope.connection is not connection
            or (request.environment_id, request.caller.run_id, request.thread_id)
            != (scope.request.environment_id, scope.request.caller.run_id, scope.request.thread_id)
        ):
            raise EnvironmentError(
                "Client Session does not belong to this execution.",
                code="environment_denied",
                details={"dispatch_stage": "pre_dispatch"},
            )
        await authority.resolve(self.runtime, request, connection.attachment, actions)
        await self.connection(request)
        if self.scopes.get(request.scope_id) is not scope:
            raise EnvironmentError(
                "Client Session retired during authorization.",
                code="environment_unavailable",
                details={"dispatch_stage": "pre_dispatch"},
            )
        scope.first_use_until = None
        scope.remember(request.caller)
        return scope

    async def dispatch(self, request: Request) -> dict[str, JsonValue]:
        if request.method == "scope.open":
            return await self.open(request)
        if request.method == "scope.close":
            scope = await self.authorize(request, frozenset())
            del self.scopes[request.scope_id]
            await scope.session.close()
            return {"closed": True}
        if request.method not in EIP_METHOD_ACTIONS and request.method not in _SCOPE_METHODS:
            raise EnvironmentError(
                "Unsupported client operation.",
                code="environment_unsupported",
                details={"dispatch_stage": "pre_dispatch"},
            )
        # The wire discriminator selects both Pydantic models together.
        method = cast(MethodSpec[BaseModel, BaseModel], METHODS[request.method])
        actions = frozenset(EIP_METHOD_ACTIONS.get(request.method, ()))
        try:
            params = method.params_type.model_validate(request.params)
        except ValidationError as error:
            raise EnvironmentError(
                "Invalid client operation parameters.",
                code="environment_request_invalid",
                details={"dispatch_stage": "pre_dispatch"},
            ) from error
        handle = None
        if isinstance(params, FileReaderCloseParams):
            handle = params.reader.root
        elif isinstance(params, (FileWriterAbortParams, FileWriterCommitParams)):
            handle = params.writer.root
        if handle is not None:
            existing = self.scopes.get(request.scope_id)
            transfer = existing.transfers.get(handle) if existing is not None else None
            if transfer is None:
                raise EnvironmentError(
                    "Client transfer does not belong to this scope.",
                    code="environment_denied",
                    details={"dispatch_stage": "pre_dispatch"},
                )
            if isinstance(params, FileWriterCommitParams):
                actions = transfer.actions
        scope = await self.authorize(request, actions)
        if request.method not in scope.session.descriptor.available_methods:
            raise EnvironmentError(
                "Client does not advertise this operation.",
                code="environment_unsupported",
                details={"dispatch_stage": "pre_dispatch"},
            )
        values = params.model_dump(mode="json", exclude_none=True)
        context = values.get("context")
        if isinstance(context, dict):
            margin = self.runtime.settings.client_environments.response_margin_seconds
            remaining = (request.expires_at - datetime.now(UTC)).total_seconds() - 2 * margin
            if remaining <= 0:
                raise EnvironmentError(
                    "Client operation expired before dispatch.",
                    code="environment_unavailable",
                    details={"dispatch_stage": "pre_dispatch"},
                )
            native_limit = scope.session.descriptor.limits.max_operation_duration_ms
            context["timeout_ms"] = min(
                context.get("timeout_ms", native_limit), native_limit, max(1, int(remaining * 1000))
            )
            params = method.params_type.model_validate(values)
        result = await scope.session.requester.request(method, params)
        if isinstance(result, (FileReaderOpenResult, ComputerObserveResult, ComputerZoomResult)):
            self._transfer(scope, result.reader.root, "read", actions, request.caller)
        elif isinstance(result, FileWriterOpenResult):
            self._transfer(scope, result.writer.root, "write", actions, request.caller)
        elif handle is not None:
            completed = scope.transfers.pop(handle, None)
            if completed is not None:
                scope.session.requester.unregister_transfer(completed.channel)
        return result.model_dump(mode="json", exclude_none=True)

    def _transfer(
        self,
        scope: Scope,
        handle: str,
        direction: TransferDirection,
        actions: frozenset[EnvironmentAction],
        caller: Caller,
    ) -> None:
        channel = scope.session.requester.register_transfer(handle, direction=direction, inbound_frames=9)
        scope.transfers[handle] = Transfer(channel, direction, actions, caller)

    async def retire(self, identity: str, scope: Scope) -> None:
        if self.scopes.get(identity) is not scope:
            return
        del self.scopes[identity]
        try:
            with anyio.move_on_after(5, shield=True):
                await scope.session.abort()
        except Exception as error:
            logger.warning(
                "Client Session cleanup failed",
                extra={"scope_id": identity, "exception_details": exception_details(error)},
            )

    async def drop_connection(self, connection: Connection) -> None:
        await asyncio.gather(
            *(
                self.retire(identity, scope)
                for identity, scope in tuple(self.scopes.items())
                if scope.connection is connection
            )
        )

    async def renew(self) -> None:
        """A valid attempt or retained call must continue owning each native Session."""
        while True:
            await asyncio.sleep(self.runtime.settings.client_environments.heartbeat_seconds)
            async with asyncio.TaskGroup() as group:
                for identity, scope in tuple(self.scopes.items()):
                    group.create_task(self._renew_scope(identity, scope))

    async def _renew_scope(self, identity: str, scope: Scope) -> None:
        if scope.first_use_until is not None and asyncio.get_running_loop().time() >= scope.first_use_until:
            await self.retire(identity, scope)
            return
        examined = dict(scope.callers)
        live = {}
        try:
            with anyio.fail_after(self.runtime.settings.client_environments.heartbeat_seconds):
                await self.connection(scope.request)
                for key, caller in examined.items():
                    request = scope.request.model_copy(update={"caller": caller})
                    try:
                        await authority.resolve(
                            self.runtime, request, scope.connection.attachment, frozenset(), renewing=True
                        )
                    except EnvironmentError:
                        continue
                    live[key] = caller
                if live:
                    await self.remember_binding(scope)
            # New callers completed their own authority check while this snapshot
            # was examined. Do not erase their independent retained ownership.
            live.update({key: caller for key, caller in scope.callers.items() if key not in examined})
        except Exception as error:
            live.clear()
            logger.warning(
                "Client Session authority unavailable", extra={"exception_details": exception_details(error)}
            )
        scope.callers = live
        if not live:
            await self.retire(identity, scope)

    async def remember_binding(self, scope: Scope) -> None:
        # Discovery and Session reconnect have separate lifetimes. Losing this
        # non-secret proof disables reattachment, never authorizes another run.
        descriptor = scope.session.descriptor
        ttl = min(
            86400, max(1, (descriptor.lifecycle.idle_timeout_ms + descriptor.lifecycle.disconnect_grace_ms) // 1000 + 1)
        )
        if not await self.runtime.redis.eval(
            _REMEMBER,
            1,
            binding_key(scope.request.environment_id, descriptor.generation, descriptor.session_id),
            f"{scope.request.caller.run_id}:{scope.connection.attachment.credential_version}",
            ttl,
        ):
            raise EnvironmentError("Client Session binding belongs to another run.", code="environment_denied")

    async def close(self) -> None:
        self.closed = True
        await self._retire_all()

    async def _retire_all(self) -> None:
        await asyncio.gather(*(self.retire(identity, scope) for identity, scope in tuple(self.scopes.items())))
