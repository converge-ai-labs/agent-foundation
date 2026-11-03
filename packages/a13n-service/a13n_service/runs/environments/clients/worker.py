"""Worker connector for a fixed client mount. The native Session lives on Control."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from a13n_envd_client import (
    ControlFrame,
    EIPDeviceConnection,
    EIPMethodError,
    EIPTransportClosedError,
    RequestCoordinator,
)
from a13n_envd_client.eip import v1 as eip
from a13n_envd_client.transport import EIPTransportFrame, TransferDirection
from a13n_harness.environment.attachments import EIPSessionBinding
from a13n_harness.environment.envd_policy import EnvdBoundaryRequirement, EnvdEgressConfiguration
from a13n_harness.environment.execution import EnvironmentConnector
from a13n_harness.environment.models import EnvironmentError, EnvironmentState
from a13n_harness.environment.session_recovery import EnvdSessionRecovery
from a13n_harness.providers.environment.remote_envd.websocket import WEBSOCKET_ENVD, WebSocketEnvdProviderRuntime
from a13n_harness.tool_execution import current_execution, execution_cleanup_active
from pydantic import BaseModel, Field, JsonValue, SecretStr, TypeAdapter
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from a13n_service.runs.attempts import AuthorityRevoked, Lease, LeaseLost
from a13n_service.runs.environments.clients.connections import Presence, owner_key
from a13n_service.runs.environments.clients.observations import Incarnation
from a13n_service.runs.environments.clients.observations import current as tool_observation
from a13n_service.runs.environments.clients.routing import Failure, NativeFailure, Request
from a13n_service.runs.environments.clients.streaming import WorkerStreams
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.tool_executions.authority import prove
from a13n_service.runs.tool_executions.routing import Caller

_PARAMS = TypeAdapter(dict[str, JsonValue])
_RECEIPT_SECONDS = 2


class Opened(BaseModel):
    descriptor: eip.SessionDescriptor
    device_descriptor: eip.DeviceDescriptor
    transfer_token: SecretStr = Field(repr=False)
    transfer_url: str


@dataclass(frozen=True, slots=True)
class Target:
    environment_id: str
    device_id: str
    directory: str
    mount_name: str

    @property
    def state(self) -> EnvironmentState:
        return EnvironmentState(
            provider_key=WEBSOCKET_ENVD.type, state_version="1", state={"device_id": self.device_id}
        )

    def connector(
        self,
        runtime: Runtime,
        lease: Lease,
        recovery: EnvdSessionRecovery | None,
        incarnation: Incarnation | None = None,
    ) -> EnvironmentConnector:
        return WEBSOCKET_ENVD.execution_connector(
            {"working_directory": self.directory},
            environment_id=self.environment_id,
            state=self.state,
            configuration={},
            session_recovery=recovery,
            runtime=WebSocketEnvdProviderRuntime(
                Client(runtime, lease, self, incarnation or Incarnation(self.device_id))
            ),
        )


@dataclass(frozen=True, slots=True)
class Calls:
    runtime: Runtime
    lease: Lease
    target: Target
    online: Presence
    scope_id: str

    async def call(self, method: str, params: Mapping[str, object]) -> dict[str, JsonValue]:
        execution = current_execution()
        request = Request(
            environment_id=self.target.environment_id,
            epoch=self.online.epoch,
            scope_id=self.scope_id,
            thread_id=self.lease.thread_id,
            caller=Caller(
                run_id=self.lease.run_id,
                attempt_id=self.lease.attempt_id,
                worker_id=self.lease.worker_id,
                execution_id=execution.execution_id if execution is not None else None,
            ),
            method=method,
            cleanup=execution_cleanup_active(),
            params=_PARAMS.validate_python(params),
            expires_at=datetime.now(UTC) + timedelta(seconds=self.runtime.settings.client_environments.request_seconds),
        )
        await prove(self.runtime, self.lease, cleanup=request.allows_cancelled_owner)
        observed = tool_observation.get()
        try:
            reply = await self.runtime.client_routing.call(self.online.node, request)
        except EnvironmentError as error:
            if observed is not None and error.details.get("client_connection_failed") is True:
                observed.disconnected[self.target.mount_name] = self.online.epoch
            raise
        match reply.outcome:
            case NativeFailure(error=error):
                raise EIPMethodError(error)
            case Failure(message=message, code=code, pre_dispatch=pre_dispatch, connection_failed=lost):
                if observed is not None and lost:
                    observed.disconnected[self.target.mount_name] = self.online.epoch
                details: dict[str, JsonValue] = {"dispatch_stage": "pre_dispatch"} if pre_dispatch else {}
                if lost:
                    details["client_connection_failed"] = True
                raise EnvironmentError(message, code=code, details=details)
            case success:
                if observed is not None and method.split(".", 1)[0] in {
                    "file",
                    "shell",
                    "process",
                    "computer",
                    "port",
                    "output",
                }:
                    observed.successes.add(self.target.mount_name)
                return success.value


class Client:
    def __init__(self, runtime: Runtime, lease: Lease, target: Target, incarnation: Incarnation) -> None:
        self.runtime, self.lease, self.target = runtime, lease, target
        self.incarnation = incarnation

    def unavailable(self) -> EnvironmentError:
        return EnvironmentError(
            "Client is not connected. This tool was not dispatched.",
            code="environment_unavailable",
            details={
                "dispatch_stage": "pre_dispatch",
                "reason": "client_offline",
                "mount": self.target.mount_name,
                "hint": f"Client mount {self.target.mount_name} is unavailable. This tool did not execute.",
            },
        )

    @asynccontextmanager
    async def open_session(
        self,
        *,
        expected_device_id: str,
        required_methods: frozenset[str],
        working_directory: str | None = None,
        timeout: float = 10,
        egress: EnvdEgressConfiguration | None = None,
        expected_boundary: EnvdBoundaryRequirement | None = None,
        session_recovery: EnvdSessionRecovery | None = None,
    ) -> AsyncIterator[EIPSessionBinding]:
        if expected_device_id != self.target.device_id or working_directory != self.target.directory:
            raise EnvironmentError("Client mount selection changed.", code="environment_stale_mount")
        try:
            async with asyncio.timeout(timeout):
                online = await self.runtime.client_connections.presence(self.target.environment_id)
        except (RedisError, TimeoutError) as error:
            raise EnvironmentError(
                "Client connection state could not be checked.",
                code="environment_unavailable",
                details={
                    "dispatch_stage": "pre_dispatch",
                    "reason": "client_discovery_unavailable",
                    "mount": self.target.mount_name,
                    "hint": "Connection state is unknown. This tool did not execute.",
                },
            ) from error
        if online is None:
            raise self.unavailable()
        if online.descriptor.device_id != expected_device_id:
            raise EnvironmentError(
                "Client identity differs from the registered device.", code="environment_stale_mount"
            )
        if expected_boundary is not None:
            expected_boundary.check(online.descriptor.boundary)
        if egress is not None:
            raise EnvironmentError("Client mounts do not grant an egress policy.", code="environment_denied")
        calls = Calls(self.runtime, self.lease, self.target, online, uuid4().hex)
        try:
            result = Opened.model_validate(
                await calls.call(
                    "scope.open",
                    {
                        "working_directory": working_directory,
                        "required_methods": sorted(required_methods),
                        "recovery": session_recovery.state.model_dump(mode="json")
                        if session_recovery is not None and session_recovery.state
                        else None,
                    },
                )
            )
        except EnvironmentError as error:
            if error.details.get("client_connection_failed") is True:
                self.incarnation.epoch = online.epoch
                raise self.unavailable() from error
            raise
        if (result.device_descriptor.device_id, result.device_descriptor.generation) != (
            expected_device_id,
            online.descriptor.generation,
        ):
            raise EnvironmentError("Client changed during Session preparation.", code="environment_stale_mount")
        transport = SessionTransport(calls, result)
        coordinator = RequestCoordinator(
            transport, request_timeout=self.runtime.settings.client_environments.request_seconds + _RECEIPT_SECONDS + 1
        )
        coordinator.configure_limits(result.device_descriptor.limits)
        device = EIPDeviceConnection(coordinator, result.device_descriptor, max_in_flight=32)

        async def owner_current() -> bool:
            try:
                await prove(self.runtime, self.lease, cleanup=execution_cleanup_active())
                return await self.runtime.redis.get(owner_key(self.target.environment_id)) == online.epoch
            except (AuthorityRevoked, LeaseLost, RedisError):
                # No proof of ownership permits dispatch on this logical binding.
                return False

        async with device:
            session = await device.bind_open_session(
                result.descriptor, readiness_timeout=timeout, owner_current=owner_current
            )
            async with session:
                self.incarnation.epoch = online.epoch
                self.incarnation.session = session
                if session_recovery is not None:
                    session_recovery.observe(self.target.environment_id, working_directory, session)
                try:
                    yield EIPSessionBinding(session, result.device_descriptor)
                finally:
                    self.incarnation.session = None
                    if session_recovery is not None:
                        session_recovery.closed(session)


class SessionTransport:
    """One logical Session carrier; SDK operation IDs and EIP responses survive forwarding."""

    def __init__(self, calls: Calls, opened: Opened) -> None:
        self.calls = calls
        self.session_id = opened.descriptor.session_id
        self.incoming: asyncio.Queue[EIPTransportFrame | Exception] = asyncio.Queue(128)
        self.pending: set[asyncio.Task[None]] = set()
        self.closed = False
        self.request_bytes = calls.runtime.settings.client_environments.message_bytes
        self.response_bytes = self.request_bytes
        self.streams = WorkerStreams(
            opened.transfer_url,
            calls.scope_id,
            opened.transfer_token.get_secret_value(),
            opened.descriptor.limits.max_transfer_frame_bytes,
            self.incoming.put,
        )

    def set_limits(self, *, max_request_bytes: int, max_response_bytes: int, max_transfer_frame_bytes: int) -> None:
        self.request_bytes = min(self.request_bytes, max_request_bytes)
        self.response_bytes = min(self.response_bytes, max_response_bytes)
        self.streams.limit = min(self.streams.limit, max_transfer_frame_bytes)

    def register_transfer(self, session_id: str, handle: str, direction: TransferDirection) -> None:
        if session_id != self.session_id:
            raise ValueError("Transfer belongs to a different client Session")
        self.streams.register(session_id, handle, direction)

    def unregister_transfer(self, session_id: str, handle: str) -> None:
        self.streams.unregister(session_id, handle)

    async def send(self, frame: EIPTransportFrame) -> None:
        if self.closed:
            raise EIPTransportClosedError("Client Session is closed")
        if isinstance(frame, eip.DataFrame):
            await self.streams.send(frame)
            return
        if len(frame.payload) > self.request_bytes:
            raise ValueError("Client request exceeds the negotiated limit")
        request = eip.decode_model(frame.payload, eip.JsonRpcRequest)
        if request.eip_session != self.session_id:
            raise ValueError("Request belongs to a different client Session")
        task = asyncio.create_task(self._exchange(request))
        self.pending.add(task)
        task.add_done_callback(self.pending.discard)

    async def _exchange(self, request: eip.JsonRpcRequest) -> None:
        method = "scope.close" if request.method == "session.close" else request.method
        try:
            value = await self.calls.call(method, request.params)
            response = eip.JsonRpcSuccessResponse(
                jsonrpc="2.0", id=request.id, eip_session=self.session_id, result=dict(value)
            )
        except EIPMethodError as error:
            response = eip.JsonRpcErrorResponse(
                jsonrpc="2.0", id=request.id, eip_session=self.session_id, error=error.error
            )
        except (EnvironmentError, LeaseLost, AuthorityRevoked) as error:
            pre = not isinstance(error, EnvironmentError) or error.details.get("dispatch_stage") == "pre_dispatch"
            if not pre:
                kind = eip.ErrorType.UNKNOWN_OUTCOME
            elif not isinstance(error, EnvironmentError):
                kind = eip.ErrorType.DENIED
            else:
                kind = {
                    "environment_denied": eip.ErrorType.DENIED,
                    "environment_request_invalid": eip.ErrorType.INVALID_PARAMS,
                    "environment_unsupported": eip.ErrorType.UNSUPPORTED,
                    "environment_busy": eip.ErrorType.BUSY,
                    "environment_stale_mount": eip.ErrorType.STALE_GENERATION,
                }.get(error.code, eip.ErrorType.PROVIDER_UNAVAILABLE)
            ctx = request.params.get("context")
            operation = ctx.get("operation_id") if isinstance(ctx, dict) else None
            receipt = None
            if not pre and method in {"process.start", "shell.exec"} and isinstance(operation, str):
                receipt = await self._receipt(method, operation)
            response = eip.JsonRpcErrorResponse(
                jsonrpc="2.0",
                id=request.id,
                eip_session=self.session_id,
                error=eip.EIPError(
                    code=eip.EIP_ERROR_CODES[kind],
                    message=str(error),
                    data=eip.EIPErrorData(
                        error_type=kind,
                        operation_id=operation if isinstance(operation, str) else None,
                        receipt=receipt,
                        dispatch_stage=eip.DispatchStage.PRE_DISPATCH if pre else eip.DispatchStage.UNKNOWN,
                        retry_hint=eip.RetryHint.AFTER_REFRESH if pre else eip.RetryHint.RECONCILE_FIRST,
                    ),
                ),
            )
        except Exception as error:
            await self.incoming.put(error)
            return
        payload = eip.encode_model(response)
        if len(payload) > self.response_bytes:
            await self.incoming.put(EIPTransportClosedError("Client response exceeds the negotiated limit"))
        else:
            await self.incoming.put(ControlFrame(payload))

    async def _receipt(self, method: str, operation: str) -> eip.OperationReceipt | None:
        """Look up the original operation once. A missing receipt proves nothing."""
        try:
            async with asyncio.timeout(_RECEIPT_SECONDS):
                result = eip.ReceiptGetResult.model_validate(
                    await self.calls.call(
                        "receipt.get",
                        {
                            "operation_id": operation,
                            "context": {"operation_id": "op-" + uuid4().hex, "timeout_ms": 1000},
                        },
                    )
                )
        except (
            EIPMethodError,
            EnvironmentError,
            AuthorityRevoked,
            LeaseLost,
            RedisError,
            SQLAlchemyError,
            TimeoutError,
            ValueError,
        ):
            return None
        receipt = result.receipt
        if (receipt.device_id, receipt.generation, receipt.session_id, receipt.operation_id, receipt.method) != (
            self.calls.online.descriptor.device_id,
            self.calls.online.descriptor.generation,
            self.session_id,
            operation,
            method,
        ):
            return None
        return receipt

    async def receive(self) -> EIPTransportFrame:
        value = await self.incoming.get()
        if isinstance(value, Exception):
            raise value
        return value

    async def close(self) -> None:
        self.closed = True
        tasks = tuple(self.pending)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.streams.close()
