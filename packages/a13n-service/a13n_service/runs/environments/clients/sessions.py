"""Authorized run scopes on Control-owned Devices; EIP owns native execution semantics."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast
from uuid import uuid4
from weakref import WeakValueDictionary

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
    ProcessInfo,
    ProcessInspectResult,
    ProcessStartResult,
    SessionConfiguration,
    SessionDescriptor,
)
from a13n_envd_client.eip.v1.methods import MethodSpec
from a13n_envd_client.errors import EIPSessionStateError
from a13n_envd_client.requester import TransferChannel
from a13n_envd_client.transport import TransferDirection
from a13n_harness.environment.models import EnvironmentAction, EnvironmentError
from a13n_harness.providers.environment.eip.binding import EIP_METHOD_ACTIONS
from a13n_logging import exception_details, get_logger
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from a13n_service.runs.environments.clients import authority, streaming
from a13n_service.runs.environments.clients.connections import Connection, owner_key
from a13n_service.runs.environments.clients.routing import Request
from a13n_service.runs.environments.clients.streams import StreamPair
from a13n_service.runs.tool_executions.routing import Caller

if TYPE_CHECKING:
    from a13n_service.runs.runtime import Runtime

logger = get_logger(__name__)

_AUTHORITY_CONCURRENCY = 4
_RENEWAL_BATCH_SIZE = 64

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


@dataclass(slots=True)
class Transfer:
    channel: TransferChannel
    direction: TransferDirection
    actions: frozenset[EnvironmentAction]
    caller: Caller
    attached: bool = False
    finished: bool = False
    task: asyncio.Task[None] | None = field(default=None, repr=False)


@dataclass(slots=True)
class Scope:
    request: Request
    connection: Connection
    session: EIPSession
    # A lost scope.open response must not keep an unclaimed Session alive for an entire run.
    first_use_until: float | None
    binding: str = "default"
    lifetime: AsyncExitStack = field(default_factory=AsyncExitStack, repr=False)
    transfers: dict[str, Transfer] = field(default_factory=dict)
    callers: dict[tuple[str, str, str | None], Caller] = field(default_factory=dict)
    processes: dict[str, ProcessInfo] = field(default_factory=dict)

    @property
    def active(self) -> bool:
        # The public accessor checks local Session state without issuing I/O.
        # Device connectivity alone does not establish Session continuity.
        try:
            _ = self.session.requester
        except EIPSessionStateError:
            return False
        return True

    def remember(self, caller: Caller) -> None:
        self.callers[(caller.attempt_id, caller.worker_id, caller.execution_id)] = caller


class Sessions:
    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime
        self.scopes: dict[str, Scope] = {}
        self.opening: set[str] = set()
        self.acquisitions: WeakValueDictionary[tuple[str, str], asyncio.Lock] = WeakValueDictionary()
        self.closed = False
        self.authority_slots = asyncio.Semaphore(_AUTHORITY_CONCURRENCY)

    async def serve(self) -> None:
        connections = self.runtime.client_connections
        try:
            while True:
                try:
                    if connections.recovering:
                        await connections.disconnect()
                        await self._retire_all()
                        connections.recovering = False
                    await self.renew()
                except Exception as error:
                    # Retire old carriers and scopes before admitting new work.
                    connections.recovering = True
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

    def _acquisition(self, request: Request, binding: str) -> asyncio.Lock:
        key = (request.thread_id, f"{request.environment_id}/{binding}")
        lock = self.acquisitions.get(key)
        if lock is None:
            lock = self.acquisitions[key] = asyncio.Lock()
        return lock

    async def open(self, request: Request) -> dict[str, JsonValue]:
        # Retirement and borrowing share one boundary, including concurrent
        # recovery of several executions from the same Thread.
        connection = await self.connection(request)
        mount = await self._resolve(request, connection, frozenset())
        async with self._acquisition(request, mount.session_binding):
            return await self._open(request)

    async def _open(self, request: Request) -> dict[str, JsonValue]:
        try:
            body = Open.model_validate(request.params)
        except ValidationError as error:
            raise EnvironmentError(
                "Invalid client Session request.",
                code="environment_request_invalid",
                details={"dispatch_stage": "pre_dispatch"},
            ) from error
        connection = await self.connection(request)
        mount = await self._resolve(request, connection, frozenset())
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
        lifetime = AsyncExitStack()
        try:
            assert connection.device is not None
            recovery = await self.runtime.environment_contexts.for_control(
                request.thread_id,
                request.caller.run_id,
                request.caller.worker_id,
                request.environment_id,
                request.mount_name,
                execution_id=request.caller.execution_id if request.retained_cleanup else None,
            )
            # A new Thread owner fences the old Worker's logical scopes before
            # attaching the same native Session on this still-connected carrier.
            for identity, previous in tuple(self.scopes.items()):
                if (
                    previous.request.thread_id == request.thread_id
                    and previous.request.environment_id == request.environment_id
                    and previous.binding == mount.session_binding
                    and previous.request.caller.worker_id != request.caller.worker_id
                ):
                    await self._retire(identity, previous)
            await self.connection(request)
            assert connection.device is not None
            controlled = connection.device.descriptor.boundary.egress == EgressMode.CONTROLLED
            # Control owns the native borrower; Worker scopes remain independently authorized.
            session = await lifetime.enter_async_context(
                recovery.borrow(
                    connection.device,
                    target=request.environment_id,
                    expected_device_id=connection.attachment.device_id,
                    configuration=SessionConfiguration(
                        working_directory=body.working_directory,
                        egress=EgressPolicy(destinations=AllowlistDestinations(mode="allowlist"))
                        if controlled
                        else None,
                    ),
                    required_methods=frozenset(body.required_methods),
                    expected_boundary=None,
                )
            )
            # Each view retains its own directory; Session cwd belongs to the shared binding.
            await self.connection(request)
            await self._resolve(request, connection, frozenset(), descriptor=session.descriptor)
            scope = Scope(
                request,
                connection,
                session,
                asyncio.get_running_loop().time() + self.runtime.settings.client_environments.request_seconds,
                binding=mount.session_binding,
                lifetime=lifetime,
            )
            await self.connection(request)
            if not request.retained_cleanup:
                scope.remember(request.caller.model_copy(update={"execution_id": None}))
            scope.remember(request.caller)
            self.scopes[request.scope_id] = scope
            return {
                "descriptor": session.descriptor.model_dump(mode="json"),
                "device_descriptor": connection.device.descriptor.model_dump(mode="json"),
            }
        except BaseException:
            with anyio.move_on_after(5, shield=True):
                await lifetime.aclose()
            raise
        finally:
            self.opening.discard(request.scope_id)

    async def _resolve(
        self,
        request: Request,
        connection: Connection,
        actions: frozenset[EnvironmentAction],
        *,
        descriptor: SessionDescriptor | None = None,
    ):
        scope = self.scopes.get(request.scope_id)
        async with self.authority_slots:
            return await authority.resolve(
                self.runtime,
                request,
                connection.attachment,
                actions,
                descriptor=scope.session.descriptor if scope is not None else descriptor,
                process=scope.processes.get(request.caller.execution_id or "") if scope is not None else None,
            )

    @staticmethod
    def _session_lost() -> EnvironmentError:
        return EnvironmentError(
            "Client execution Session expired. Prepare a new Session before the next operation.",
            code="environment_unavailable",
            details={"dispatch_stage": "pre_dispatch", "client_session_failed": True},
        )

    async def authorize(self, request: Request, actions: frozenset[EnvironmentAction]) -> Scope:
        scope = self.scopes.get(request.scope_id)
        connection = await self.connection(request)
        if scope is not None and (
            scope.connection is not connection
            or (request.environment_id, request.mount_name, request.caller.run_id, request.thread_id)
            != (
                scope.request.environment_id,
                scope.request.mount_name,
                scope.request.caller.run_id,
                scope.request.thread_id,
            )
        ):
            raise EnvironmentError(
                "Client Session does not belong to this execution.",
                code="environment_denied",
                details={"dispatch_stage": "pre_dispatch"},
            )
        await self._resolve(request, connection, actions)
        await self.connection(request)
        if scope is None or self.scopes.get(request.scope_id) is not scope:
            raise self._session_lost()
        if not scope.active:
            await self.retire(request.scope_id, scope)
            raise self._session_lost()
        scope.first_use_until = None
        scope.remember(request.caller)
        return scope

    async def dispatch(self, request: Request) -> dict[str, JsonValue]:
        if request.method == "scope.open":
            return await self.open(request)
        if request.method == "transfer.attach":
            return await self.attach_transfer(request)
        if request.method in {"scope.close", "scope.detach"}:
            scope = await self.authorize(request, frozenset())
            async with self._acquisition(request, scope.binding):
                if self.scopes.get(request.scope_id) is not scope:
                    raise self._session_lost()
                del self.scopes[request.scope_id]
                try:
                    await self.stop_transfers(scope)
                finally:
                    try:
                        if request.method == "scope.close":
                            await scope.session.close()
                    finally:
                        await scope.lifetime.aclose()
            return {"closed": True} if request.method == "scope.close" else {"detached": True}
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
        if request.caller.execution_id is not None and isinstance(result, (ProcessInspectResult, ProcessStartResult)):
            scope.processes[request.caller.execution_id] = result.process
        if isinstance(result, (FileReaderOpenResult, ComputerObserveResult, ComputerZoomResult)):
            self._transfer(scope, result.reader.root, "read", actions, request.caller)
        elif isinstance(result, FileWriterOpenResult):
            self._transfer(scope, result.writer.root, "write", actions, request.caller)
        elif handle is not None:
            completed = scope.transfers.pop(handle, None)
            if completed is not None:
                await streaming.stop(completed)
                scope.session.requester.unregister_transfer(completed.channel)
        return result.model_dump(mode="json", exclude_none=True)

    async def attach_transfer(self, request: Request) -> dict[str, JsonValue]:
        try:
            body = streaming.Attach.model_validate(request.params)
        except ValidationError as error:
            raise EnvironmentError(
                "Invalid transfer selector",
                code="environment_request_invalid",
                details={"dispatch_stage": "pre_dispatch"},
            ) from error
        scope = self.scopes.get(request.scope_id)
        transfer = scope.transfers.get(body.handle) if scope is not None else None
        if (
            scope is None
            or transfer is None
            or transfer.direction != body.direction
            or scope.session.session_id != body.session_id
            or transfer.caller != request.caller
        ):
            raise EnvironmentError(
                "Transfer does not belong to this scope",
                code="environment_denied",
                details={"dispatch_stage": "pre_dispatch"},
            )
        await self.authorize(request, transfer.actions)
        if transfer.attached or scope.transfers.get(body.handle) is not transfer:
            raise EnvironmentError(
                "Transfer already attached or ended",
                code="environment_conflict",
                details={"dispatch_stage": "pre_dispatch"},
            )
        transfer.attached = True
        attached = streaming.Attached(
            transfer_id=uuid4().hex, seconds=self.runtime.settings.client_environments.transfer_seconds
        )
        stream = StreamPair(
            self.runtime.client_routing.redis,
            request.environment_id,
            request.epoch,
            attached.transfer_id,
            side="control",
            limit=scope.session.descriptor.limits.max_transfer_frame_bytes,
            seconds=attached.seconds,
        )
        try:
            await stream.create()
            await self.authorize(request, transfer.actions)
            if scope.transfers.get(body.handle) is not transfer:
                raise EnvironmentError("Transfer ended during attachment", code="environment_unavailable")
            transfer.task = asyncio.create_task(
                streaming.relay(self.runtime, scope, transfer, stream), name="client-binary-transfer"
            )
        except BaseException:
            await stream.close()
            raise
        return attached.model_dump(mode="json")

    @staticmethod
    async def stop_transfers(scope: Scope) -> None:
        transfers = tuple(scope.transfers.values())
        try:
            await asyncio.gather(*(streaming.stop(transfer) for transfer in transfers))
            for transfer in transfers:
                streaming.retire(scope, transfer)
            if transfers and not scope.active:
                raise Sessions._session_lost()
            await asyncio.gather(
                *(
                    scope.session.requester.wait_transfer_retired(transfer.channel)
                    for transfer in transfers
                    if not transfer.channel.peer_reset_received
                )
            )
        except BaseException:
            # Unacknowledged frames must not reach a successor on this carrier.
            # Fence admission before yielding to the carrier's bounded cleanup.
            with anyio.move_on_after(5, shield=True):
                await scope.connection.disconnect()
            raise
        finally:
            scope.transfers.clear()

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
        async with self._acquisition(scope.request, scope.binding):
            await self._retire(identity, scope)

    async def _retire(self, identity: str, scope: Scope) -> None:
        if self.scopes.get(identity) is not scope:
            return
        del self.scopes[identity]
        try:
            with anyio.move_on_after(5, shield=True):
                try:
                    await self.stop_transfers(scope)
                finally:
                    await scope.lifetime.aclose()
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
        """One bounded database sweep serves Sessions and their active transfers."""
        while True:
            await asyncio.sleep(self.runtime.settings.client_environments.heartbeat_seconds)
            scopes = list(self.scopes.items())
            for offset in range(0, len(scopes), _RENEWAL_BATCH_SIZE):
                await self._renew_batch(scopes[offset : offset + _RENEWAL_BATCH_SIZE])

    async def _renew_scope(self, identity: str, scope: Scope) -> None:
        await self._renew_batch([(identity, scope)])

    async def _renew_batch(self, scopes: list[tuple[str, Scope]]) -> None:
        examined = {identity: dict(scope.callers) for identity, scope in scopes}
        checks: list[authority.Check] = []
        owners: list[tuple[str, Caller, Transfer | None]] = []
        live: dict[str, dict[tuple[str, str, str | None], Caller]] = {identity: {} for identity, _ in scopes}
        admitted: set[str] = set()
        try:
            with anyio.fail_after(self.runtime.settings.client_environments.heartbeat_seconds):
                for identity, scope in scopes:
                    if not scope.active:
                        continue
                    if scope.first_use_until is not None and asyncio.get_running_loop().time() >= scope.first_use_until:
                        continue
                    try:
                        await self.connection(scope.request)
                    except EnvironmentError:
                        continue
                    admitted.add(identity)
                    for caller in examined[identity].values():
                        checks.append(
                            authority.Check(
                                scope.request.model_copy(
                                    update={
                                        "caller": caller,
                                        **(
                                            {"method": "scope.detach", "cleanup": True}
                                            if caller.execution_id in scope.processes
                                            else {}
                                        ),
                                    }
                                ),
                                scope.connection.attachment,
                                frozenset(),
                                True,
                                scope.session.descriptor,
                                scope.processes.get(caller.execution_id or ""),
                            )
                        )
                        owners.append((identity, caller, None))
                    for transfer in tuple(scope.transfers.values()):
                        if transfer.task is not None and not transfer.task.done():
                            checks.append(
                                authority.Check(
                                    scope.request.model_copy(update={"caller": transfer.caller}),
                                    scope.connection.attachment,
                                    transfer.actions,
                                )
                            )
                            owners.append((identity, transfer.caller, transfer))
                results = []
                for offset in range(0, len(checks), _RENEWAL_BATCH_SIZE):
                    async with self.authority_slots:
                        results.extend(
                            await authority.resolve_many(self.runtime, checks[offset : offset + _RENEWAL_BATCH_SIZE])
                        )
                for (identity, caller, transfer), result in zip(owners, results, strict=True):
                    if transfer is not None:
                        if isinstance(result, EnvironmentError):
                            await streaming.stop(transfer)
                    elif not isinstance(result, EnvironmentError):
                        live[identity][(caller.attempt_id, caller.worker_id, caller.execution_id)] = caller
                for identity, scope in scopes:
                    if self.scopes.get(identity) is not scope:
                        continue
                    if not scope.active:
                        live[identity] = {}
                        continue
                    if identity in admitted:
                        # Concurrently admitted callers have already passed their own check.
                        live[identity].update(
                            {key: caller for key, caller in scope.callers.items() if key not in examined[identity]}
                        )
        except Exception as error:
            # Fail closed on unavailable evidence without labeling it a revocation.
            live = {identity: {} for identity, _ in scopes}
            logger.warning(
                "Client Session authority unavailable", extra={"exception_details": exception_details(error)}
            )
        for identity, scope in scopes:
            if self.scopes.get(identity) is scope:
                scope.callers = live[identity]
                if not scope.callers:
                    await self.retire(identity, scope)

    async def close(self) -> None:
        self.closed = True
        await self._retire_all()

    async def _retire_all(self) -> None:
        await asyncio.gather(*(self.retire(identity, scope) for identity, scope in tuple(self.scopes.items())))
