from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from contextvars import Context, copy_context
from dataclasses import dataclass
from typing import Any, cast

from pydantic import BaseModel

from a13n_envd_client._timeouts import response_timeout
from a13n_envd_client.eip.v1 import (
    DataFrame,
    DataFrameKind,
    DataResetStatus,
    EIPLimits,
    EIPRequester,
    JsonRpcErrorResponse,
    JsonRpcRequest,
    JsonRpcSuccessResponse,
    MethodSpec,
    SessionCloseParams,
    SessionDescriptor,
    SessionOpenParams,
    SessionOpenResult,
    decode_model,
    encode_model,
)
from a13n_envd_client.eip.v1.methods import SESSION_CLOSE, SESSION_OPEN
from a13n_envd_client.errors import (
    EIPClientError,
    EIPMethodError,
    EIPProtocolError,
    EIPRequestTimeoutError,
    EIPSessionStateError,
    EIPTransferError,
    EIPTransferTransportError,
    EIPTransportClosedError,
    EIPTransportError,
)
from a13n_envd_client.transport import ControlFrame, EIPTransport, HttpTransferLifecycle, TransferDirection

_MAX_JSONRPC_ID = 2**63 - 1
_TRANSFER_TEARDOWN_TIMEOUT = 5.0
_SEND_TIMEOUT = 30.0
_RESPONSE_TYPE = cast(
    type[JsonRpcSuccessResponse | JsonRpcErrorResponse], JsonRpcSuccessResponse | JsonRpcErrorResponse
)
# These calls must progress even when ordinary work has filled its allowance.
_RELEASE_METHODS = frozenset(
    {"operation.cancel", "output.release", "process.kill", "process.release", "file.close_reader", "file.abort_writer"}
)


class _AdmissionLimiter:
    def __init__(self, limit: int) -> None:
        _validate_positive_integer("admission limit", limit)
        self.limit = limit
        self.active = 0
        self._changed = asyncio.Event()
        self.error: BaseException | None = None

    async def acquire(self) -> None:
        while self.active >= self.limit and self.error is None:
            self._changed.clear()
            await self._changed.wait()
        if self.error is not None:
            raise self.error
        self.active += 1

    def release(self) -> None:
        self.active -= 1
        self._changed.set()

    def fail(self, error: BaseException) -> None:
        self.error = error
        self._changed.set()


@dataclass(slots=True)
class _PendingRequest:
    method: MethodSpec[Any, Any]
    future: asyncio.Future[object]
    owner: SessionRequester | None
    admissions: tuple[_AdmissionLimiter, ...]
    context: Context
    send_task: asyncio.Task[None] | None = None
    open_params: SessionOpenParams | None = None
    open_result: SessionOpenResult | None = None


class TransferChannel:
    """Bounded, nonblocking inbox: a slow transfer never stalls the Device reader."""

    def __init__(self, session_id: str, handle: str, *, inbound_frames: int) -> None:
        _validate_positive_integer("inbound_frames", inbound_frames)
        self.session_id = session_id
        self.handle = handle
        self._capacity = inbound_frames
        self._queue: asyncio.Queue[DataFrame | BaseException] = asyncio.Queue(inbound_frames + 1)
        self._failed = False
        self._discarding = False
        # The requester settles this result; cancellation of a waiter never owns cleanup.
        self._retirement: asyncio.Future[BaseException | None] = asyncio.get_running_loop().create_future()
        self.peer_reset_received = False

    async def receive(self) -> DataFrame:
        item = await self._queue.get()
        if isinstance(item, BaseException):
            raise item
        return item

    def deliver(self, frame: DataFrame) -> bool:
        if self._failed or self._discarding:
            return True
        if frame.kind is DataFrameKind.RESET:
            if self.peer_reset_received:
                raise EIPProtocolError("duplicate transfer RESET")
            self.peer_reset_received = True
        elif self._queue.qsize() >= self._capacity:
            self.fail(EIPTransferError("transfer consumer exceeded its buffered frame allowance"))
            return False
        self._queue.put_nowait(frame)
        return True

    def discard(self) -> None:
        self._discarding = True

    def fail(self, error: BaseException) -> None:
        if self._failed:
            return
        self._failed = True
        # Discard buffered content rather than present partial data after failure.
        while not self._queue.empty():
            self._queue.get_nowait()
        self._queue.put_nowait(error)


class RequestCoordinator(EIPRequester):
    """One Device connection, physical reader and bounded correlation owner.

    Caller cancellation abandons the wait, not the send or its capacity. A sent
    request remains owned until its reply, Session teardown, or carrier failure.
    """

    def __init__(
        self,
        transport: EIPTransport,
        *,
        max_in_flight: int = 256,
        max_sessions: int = 128,
        request_timeout: float | None = None,
    ) -> None:
        _validate_positive_integer("max_sessions", max_sessions)
        if request_timeout is not None and request_timeout <= 0:
            raise ValueError("request_timeout must be positive or None")
        self._transport = transport
        self._admission = _AdmissionLimiter(max_in_flight)
        self._device_admission = _AdmissionLimiter(8)
        self._max_sessions = max_sessions
        self._request_timeout = request_timeout
        self._pending: dict[str | int, _PendingRequest] = {}
        self._pending_changed = asyncio.Event()
        self._send_tasks: set[asyncio.Task[None]] = set()
        self._sessions: dict[str, SessionRequester] = {}
        self._next_id = 1
        self._reader_task: asyncio.Task[None] | None = None
        self._close_task: asyncio.Task[None] | None = None
        self._terminal_error: BaseException | None = None

    async def request[P, R](self, method: MethodSpec[P, R], params: P) -> R:
        if not method.device_scoped:
            raise EIPSessionStateError("Session methods require a scoped requester")
        return cast(R, await self._request(method, params, None))

    async def open_session[T](self, params: SessionOpenParams, bind: Callable[[SessionDescriptor], T]) -> T:
        """Transfer the open result to its owner before releasing admission."""
        return cast(T, await self._request(SESSION_OPEN, params, None, consume=lambda result: bind(result.descriptor)))

    def session(self, session_id: str, *, limits: EIPLimits, max_in_flight: int = 32) -> SessionRequester:
        self._ensure_open()
        if not session_id or session_id in self._sessions:
            raise EIPSessionStateError("Session is empty or already attached locally")
        if len(self._sessions) >= self._max_sessions:
            raise EIPSessionStateError("local Device Session capacity exhausted")
        requester = SessionRequester(self, session_id, limits, max_in_flight)
        self._sessions[session_id] = requester
        return requester

    def configure_limits(self, limits: EIPLimits) -> None:
        self._transport.set_limits(
            max_request_bytes=limits.max_request_bytes,
            max_response_bytes=limits.max_response_bytes,
            max_transfer_frame_bytes=limits.max_transfer_frame_bytes,
        )

    async def _request[P, R](
        self,
        method: MethodSpec[P, R],
        params: P,
        owner: SessionRequester | None,
        *,
        consume: Callable[[R], object] | None = None,
    ) -> object:
        self._ensure_open()
        if not isinstance(params, method.params_type):
            raise TypeError(f"{method.name} params must be {method.params_type.__name__}")
        started = asyncio.get_running_loop().time()
        payload = json.loads(encode_model(cast(BaseModel, params)))
        timeout = _effective_timeout(payload, self._request_timeout)
        deadline = None if timeout is None else started + timeout
        admissions = (self._device_admission,) if owner is None else owner.admissions(method)
        acquired: list[_AdmissionLimiter] = []
        try:
            async with asyncio.timeout_at(deadline):
                for admission in admissions:
                    await admission.acquire()
                    acquired.append(admission)
                self._ensure_open()
                if deadline is not None and asyncio.get_running_loop().time() >= deadline:
                    raise TimeoutError
                if owner is not None:
                    owner.ensure_method(method)
        except BaseException as error:
            for admission in acquired:
                admission.release()
            if isinstance(error, TimeoutError):
                raise EIPRequestTimeoutError("timed out waiting for EIP admission", dispatched=False) from error
            raise
        request_id = self._allocate_id()
        future: asyncio.Future[object] = asyncio.get_running_loop().create_future()
        pending = _PendingRequest(
            cast(MethodSpec[Any, Any], method),
            future,
            owner,
            admissions,
            copy_context(),
            open_params=params if isinstance(params, SessionOpenParams) else None,
        )
        self._pending[request_id] = pending
        envelope = JsonRpcRequest(
            jsonrpc="2.0",
            id=request_id,
            method=method.name,
            params=payload,
            eip_session=None if owner is None else owner.session_id,
        )
        self._ensure_reader()
        send_timeout = (
            response_timeout(payload, self._request_timeout)
            if isinstance(self._transport, HttpTransferLifecycle)
            else _SEND_TIMEOUT
        )
        pending.send_task = asyncio.create_task(
            self._send(request_id, pending, ControlFrame(encode_model(envelope)), send_timeout)
        )
        self._send_tasks.add(pending.send_task)
        pending.send_task.add_done_callback(self._send_tasks.discard)
        try:
            async with asyncio.timeout(response_timeout(payload, self._request_timeout)):
                result = cast(R, await asyncio.shield(future))
            claimed = result if consume is None else consume(result)
            if pending.open_result is not None:
                self._release_pending(request_id)
            return claimed
        except BaseException as error:
            # Keep the pending entry and all admissions. Cancellation must never
            # create an unbounded tombstone cache or close an unrelated Session.
            if future.done() and not future.cancelled():
                future.exception()
            future.cancel()
            if pending.open_result is not None:
                self._discard_open(request_id, pending)
            if isinstance(error, TimeoutError):
                raise EIPRequestTimeoutError(
                    "timed out waiting for EIP response; outcome unknown", dispatched=True
                ) from error
            raise

    async def _send(
        self, request_id: str | int, pending: _PendingRequest, frame: ControlFrame, timeout: float | None
    ) -> None:
        try:
            async with asyncio.timeout(timeout):
                await self._transport.send(frame)
        except asyncio.CancelledError:
            return
        except EIPSessionStateError as error:
            # A Host may reject this scope before dispatch (for example an expired
            # use lease). That is not physical carrier failure or sibling failure.
            self._settle(request_id, error=error)
            if pending.owner is not None:
                pending.owner.fail(error)
        except Exception as error:
            failure = error if isinstance(error, EIPClientError) else EIPTransportError("failed to send EIP request")
            # HTTP exchanges are independent; losing one response does not make
            # any sibling exchange or the Device client terminal.
            if isinstance(self._transport, HttpTransferLifecycle):
                if self._pending.get(request_id) is pending:
                    self._settle(request_id, error=failure)
            else:
                self._terminate(failure)
                self._start_close()

    def _allocate_id(self) -> int:
        for _ in range(len(self._pending) + 1):
            request_id = self._next_id
            self._next_id = 1 if request_id == _MAX_JSONRPC_ID else request_id + 1
            if request_id not in self._pending:
                return request_id
        raise RuntimeError("no JSON-RPC request ID is available")

    def _drop_finished_session(self, owner: SessionRequester) -> None:
        if owner._finished and not any(pending.owner is owner for pending in self._pending.values()):
            self._sessions.pop(owner.session_id, None)

    @property
    def is_closed(self) -> bool:
        return self._terminal_error is not None or self._close_task is not None

    def _ensure_open(self) -> None:
        if self.is_closed:
            raise EIPTransportClosedError("Device connection is closed") from self._terminal_error

    def _ensure_reader(self) -> None:
        if self._reader_task is None:
            self._reader_task = asyncio.create_task(self._reader_loop(), name="eip-device-reader")

    async def _reader_loop(self) -> None:
        try:
            while True:
                try:
                    frame = await self._transport.receive()
                except EIPTransferTransportError as error:
                    owner = self._sessions.get(error.session_id)
                    if owner is not None:
                        channel = owner._transfers.get(error.handle)
                        if channel is not None:
                            channel.fail(error)
                    continue
                if isinstance(frame, ControlFrame):
                    self._handle_control(frame)
                elif isinstance(frame, DataFrame):
                    owner = self._sessions.get(frame.session_id)
                    if owner is not None:
                        owner.deliver(frame)
                    # Yield to consumers even when the carrier already buffered
                    # a burst. This is scheduling, never a consumer-owned wait.
                    await asyncio.sleep(0)
                    # Closed/unknown selectors cannot affect a live sibling.
                else:
                    raise EIPProtocolError("transport returned an unsupported EIP frame")
        except asyncio.CancelledError:
            return
        except Exception as error:
            failure = error if isinstance(error, EIPClientError) else EIPProtocolError("invalid EIP response")
            self._terminate(failure)
            self._start_close()

    def _handle_control(self, frame: ControlFrame) -> None:
        response = decode_model(frame.payload, _RESPONSE_TYPE)
        pending = self._pending.get(response.id) if response.id is not None else None
        if pending is None:
            if response.eip_session is not None:
                owner = self._sessions.get(response.eip_session)
                if owner is not None:
                    owner.fail(EIPProtocolError("unknown or duplicate Session response ID"))
                return
            raise EIPProtocolError("uncorrelated Device response")
        assert response.id is not None
        expected = None if pending.owner is None else pending.owner.session_id
        if response.eip_session != expected:
            error = EIPProtocolError("response changed its request's Session selector")
            if pending.owner is None:
                raise error
            self._settle(response.id, error=error)
            pending.owner.fail(error)
            return
        if isinstance(response, JsonRpcErrorResponse):
            self._settle(response.id, error=EIPMethodError(response.error))
            return
        try:
            result = decode_model(json.dumps(response.result).encode(), pending.method.result_type)
        except Exception as error:
            failure = EIPProtocolError(f"invalid {pending.method.name} result")
            self._settle(response.id, error=failure)
            if pending.owner is None:
                raise failure from error
            pending.owner.fail(failure)
            return
        self._settle(response.id, result=result)

    def _release_pending(self, request_id: str | int) -> _PendingRequest:
        pending = self._pending.pop(request_id)
        for admission in pending.admissions:
            admission.release()
        if pending.owner is not None:
            self._drop_finished_session(pending.owner)
        self._pending_changed.set()
        return pending

    def _settle(self, request_id: str | int, *, result: object = None, error: BaseException | None = None) -> None:
        pending = self._pending[request_id]
        if isinstance(result, SessionOpenResult) and error is None:
            # Delivery is not ownership: cancellation may win after set_result
            # but before the caller resumes. Hold admission through that race.
            pending.open_result = result
            if pending.future.cancelled():
                self._discard_open(request_id, pending)
                return
        else:
            self._release_pending(request_id)
        if not pending.future.done():
            if error is not None:
                pending.future.set_exception(error)
            else:
                pending.future.set_result(result)

    def _discard_open(self, request_id: str | int, pending: _PendingRequest) -> None:
        assert pending.open_result is not None and pending.open_params is not None
        descriptor = pending.open_result.descriptor
        expected = pending.open_params
        if self._pending.get(request_id) is not pending or self._terminal_error is not None:
            return
        if (descriptor.device_id, descriptor.generation) != (
            expected.expected_device_id,
            expected.expected_generation,
        ) or descriptor.session_id in self._sessions:
            # An invalid/duplicate descriptor must not close a live sibling.
            self._release_pending(request_id)
            return
        # Replace the open correlation with its exact Session close. It keeps
        # the original Device admission until the cleanup response or loss;
        # no unbounded background tasks, Session slots, or tombstones are needed.
        del self._pending[request_id]
        pending.method = SESSION_CLOSE
        pending.owner = SessionRequester(self, descriptor.session_id, descriptor.limits, 1)
        pending.open_result = None
        pending.future.cancel()
        cleanup_id = self._allocate_id()
        self._pending[cleanup_id] = pending
        envelope = JsonRpcRequest(
            jsonrpc="2.0", id=cleanup_id, method="session.close", params={}, eip_session=descriptor.session_id
        )
        pending.send_task = asyncio.create_task(
            self._send(cleanup_id, pending, ControlFrame(encode_model(envelope)), _SEND_TIMEOUT),
            context=pending.context.copy(),
        )
        self._send_tasks.add(pending.send_task)
        pending.send_task.add_done_callback(self._send_tasks.discard)

    def _terminate(self, error: BaseException) -> None:
        if self._terminal_error is None:
            self._terminal_error = error
        self._admission.fail(error)
        self._device_admission.fail(error)
        for owner in tuple(self._sessions.values()):
            owner.finish(error)
        for request_id in tuple(self._pending):
            self._settle(request_id, error=error)

    def _start_close(self) -> None:
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close(), name="eip-device-close")

    async def close(self) -> None:
        self._start_close()
        assert self._close_task is not None
        await asyncio.shield(self._close_task)

    async def _close(self) -> None:
        # Keep the reader alive briefly for in-flight opens and their exact
        # cleanup. In particular HTTP carrier closure does not destroy a Device.
        if self._terminal_error is None:
            try:
                async with asyncio.timeout(_TRANSFER_TEARDOWN_TIMEOUT):
                    while any(pending.open_params is not None for pending in self._pending.values()):
                        self._pending_changed.clear()
                        await self._pending_changed.wait()
            except TimeoutError:
                pass
        sends = list(self._send_tasks)
        self._terminate(EIPTransportClosedError("Device connection closed"))
        tasks = sends + ([self._reader_task] if self._reader_task is not None else [])
        for task in tasks:
            task.cancel()
        try:
            await self._transport.close()
        finally:
            await asyncio.gather(*tasks, return_exceptions=True)


class SessionRequester(EIPRequester):
    """Session-local admission and transfers over a Device-owned connection."""

    def __init__(self, device: RequestCoordinator, session_id: str, limits: EIPLimits, max_in_flight: int) -> None:
        _validate_positive_integer("max_in_flight", max_in_flight)
        self._device = device
        self.session_id = session_id
        self._admission = _AdmissionLimiter(min(max_in_flight, limits.max_concurrent_operations))
        self._release_admission = _AdmissionLimiter(4)
        self._lifecycle = {
            name: _AdmissionLimiter(1) for name in ("session.attach", "session.keepalive", "session.close")
        }
        self._max_transfers = limits.max_concurrent_file_transfers
        self._transfers: dict[str, TransferChannel] = {}
        self._retired: dict[str, tuple[TransferChannel, asyncio.Task[None]]] = {}
        self._error: BaseException | None = None
        self._failure_task: asyncio.Task[None] | None = None
        self._closing = False
        self._finished = False
        self._finished_event = asyncio.Event()

    def admissions(self, method: MethodSpec[Any, Any]) -> tuple[_AdmissionLimiter, ...]:
        self.ensure_method(method)
        if method.name in self._lifecycle:
            return (self._lifecycle[method.name],)
        if method.name in _RELEASE_METHODS:
            return (self._release_admission,)
        return self._admission, self._device._admission

    def ensure_method(self, method: MethodSpec[Any, Any]) -> None:
        if method.device_scoped:
            raise EIPSessionStateError("Device methods require the Device requester")
        if (self._error is not None or self._closing) and method.name != "session.close":
            raise EIPSessionStateError("Session requester is closed") from self._error

    async def request[P, R](self, method: MethodSpec[P, R], params: P) -> R:
        return cast(R, await self._device._request(method, params, self))

    def narrow_limits(self, limits: EIPLimits) -> None:
        self._admission.limit = min(self._admission.limit, limits.max_concurrent_operations)
        self._max_transfers = min(self._max_transfers, limits.max_concurrent_file_transfers)

    def begin_close(self) -> None:
        self._closing = True
        error = EIPSessionStateError("Session is closing")
        self._admission.fail(error)
        self._release_admission.fail(error)
        for name, admission in self._lifecycle.items():
            if name != "session.close":
                admission.fail(error)

    async def wait_finished(self) -> BaseException | None:
        await self._finished_event.wait()
        return self._error

    def finish(self, error: BaseException | None = None) -> None:
        if self._finished:
            return
        self._error = error or EIPSessionStateError("Session closed")
        self.begin_close()
        self._lifecycle["session.close"].fail(self._error)
        self._finished = True
        self._finished_event.set()
        self._device._drop_finished_session(self)
        # Correlations remain owned until their individual replies or Device loss.
        # Wake callers but do not recycle pending IDs/admission after a local abort.
        for pending in self._device._pending.values():
            if pending.owner is self and not pending.future.done():
                pending.future.set_exception(self._error)
        for channel in tuple(self._transfers.values()):
            channel.fail(self._error)
            self.unregister_transfer(channel)
        for handle in tuple(self._retired):
            self.complete_retired_transfer(handle, error=self._error)

    def fail(self, error: BaseException) -> None:
        if self._error is not None:
            return
        self._error = error
        self.begin_close()
        for channel in self._transfers.values():
            channel.fail(error)
        self._failure_task = asyncio.create_task(self._close_failed(), name="eip-session-failure-close")

    async def _close_failed(self) -> None:
        try:
            async with asyncio.timeout(_TRANSFER_TEARDOWN_TIMEOUT):
                await self.request(SESSION_CLOSE, SessionCloseParams())
        except (EIPClientError, TimeoutError):
            pass
        finally:
            self.finish(self._error)

    async def close_for_protocol_error(self, error: EIPProtocolError) -> None:
        self.fail(error)
        if self._failure_task is not None:
            await asyncio.shield(self._failure_task)

    def register_transfer(
        self, handle: str, *, direction: TransferDirection = "read", inbound_frames: int = 8
    ) -> TransferChannel:
        if self._error is not None or self._closing:
            raise EIPSessionStateError("Session is closed")
        if not handle or handle in self._transfers or handle in self._retired:
            raise EIPSessionStateError("transfer handle is empty or already attached")
        if len(self._transfers) + len(self._retired) >= self._max_transfers:
            raise EIPSessionStateError("Session transfer capacity exhausted")
        channel = TransferChannel(self.session_id, handle, inbound_frames=inbound_frames)
        transport = self._device._transport
        if isinstance(transport, HttpTransferLifecycle):
            transport.register_transfer(self.session_id, handle, direction)
        self._transfers[handle] = channel
        self._device._ensure_reader()
        return channel

    def unregister_transfer(self, channel: TransferChannel) -> None:
        if self._transfers.get(channel.handle) is not channel:
            return
        del self._transfers[channel.handle]
        transport = self._device._transport
        if isinstance(transport, HttpTransferLifecycle):
            transport.unregister_transfer(self.session_id, channel.handle)

    def deliver(self, frame: DataFrame) -> None:
        channel = self._transfers.get(frame.handle)
        if channel is not None:
            try:
                accepted = channel.deliver(frame)
            except EIPProtocolError as error:
                self.fail(error)
                return
            if not accepted:
                self.retire_transfer(
                    channel,
                    reset=DataFrame(
                        kind=DataFrameKind.RESET,
                        session_id=self.session_id,
                        handle=frame.handle,
                        reset_status=DataResetStatus.LIMIT,
                    ),
                )
        elif frame.handle in self._retired:
            if frame.kind is DataFrameKind.RESET:
                self.complete_retired_transfer(frame.handle)
        else:
            self.fail(EIPProtocolError("unknown Session transfer handle"))

    def retire_transfer(self, channel: TransferChannel, *, reset: DataFrame | None = None) -> None:
        if channel._discarding:
            return
        channel.discard()
        self._transfers.pop(channel.handle, None)
        if self._finished:
            channel._retirement.set_result(self._error)
            return
        if channel.handle not in self._retired:
            self._retired[channel.handle] = (channel, asyncio.create_task(self._retire(channel.handle, reset)))

    async def _retire(self, handle: str, reset: DataFrame | None) -> None:
        try:
            if reset is not None:
                async with asyncio.timeout(_TRANSFER_TEARDOWN_TIMEOUT):
                    await self._device._transport.send(reset)
                if isinstance(self._device._transport, HttpTransferLifecycle):
                    self.complete_retired_transfer(handle)
                    return
            await asyncio.sleep(_TRANSFER_TEARDOWN_TIMEOUT)
            self.fail(EIPProtocolError("Session transfer RESET acknowledgement timed out"))
        except (EIPClientError, TimeoutError) as error:
            self.fail(error)

    def transfer_is_retired(self, channel: TransferChannel) -> bool:
        return channel._discarding

    async def wait_transfer_retired(self, channel: TransferChannel) -> None:
        error = await asyncio.shield(channel._retirement)
        if error is not None:
            raise error

    def complete_retired_transfer(self, handle: str, *, error: BaseException | None = None) -> None:
        retired = self._retired.pop(handle, None)
        transport = self._device._transport
        if isinstance(transport, HttpTransferLifecycle):
            transport.unregister_transfer(self.session_id, handle)
        if retired is not None:
            channel, task = retired
            channel._retirement.set_result(error)
            if task is not asyncio.current_task():
                task.cancel()

    async def reset_transfer(self, channel: TransferChannel, frame: DataFrame) -> None:
        self._validate_frame(channel, frame)
        if frame.kind is not DataFrameKind.RESET:
            raise ValueError("reset requires a RESET frame")
        if channel._discarding:
            return
        # Retire before sending so an immediate peer RESET cannot race its owner.
        # HTTP registration remains until retirement completes.
        self.retire_transfer(channel)
        if self._finished:
            return
        try:
            async with asyncio.timeout(_TRANSFER_TEARDOWN_TIMEOUT):
                await self._device._transport.send(frame)
        except (EIPClientError, TimeoutError) as error:
            self.fail(error)
            raise
        if isinstance(self._device._transport, HttpTransferLifecycle):
            self.complete_retired_transfer(channel.handle)

    async def send_data_frame(self, channel: TransferChannel, frame: DataFrame) -> None:
        self._validate_frame(channel, frame)
        if self._transfers.get(channel.handle) is not channel:
            raise EIPSessionStateError("transfer channel is not registered")
        await self._device._transport.send(frame)

    def _validate_frame(self, channel: TransferChannel, frame: DataFrame) -> None:
        if frame.session_id != self.session_id or frame.handle != channel.handle:
            raise ValueError("data frame does not match its Session and transfer")


def _validate_positive_integer(name: str, value: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{name} must be a positive integer")


def _effective_timeout(params: dict[str, Any], configured_timeout: float | None) -> float | None:
    context = params.get("context")
    if not isinstance(context, dict):
        return configured_timeout
    timeout_ms = context.get("timeout_ms")
    if not isinstance(timeout_ms, int) or isinstance(timeout_ms, bool):
        return configured_timeout
    requested_timeout = timeout_ms / 1000
    return requested_timeout if configured_timeout is None else min(configured_timeout, requested_timeout)
