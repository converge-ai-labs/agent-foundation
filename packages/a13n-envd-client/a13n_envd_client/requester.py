from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Any, cast

from pydantic import BaseModel

from a13n_envd_client._timeouts import response_timeout
from a13n_envd_client.eip.v1 import (
    DataFrame,
    DataFrameKind,
    EIPRequester,
    JsonRpcErrorResponse,
    JsonRpcRequest,
    JsonRpcSuccessResponse,
    MethodSpec,
    decode_model,
    encode_model,
)
from a13n_envd_client.errors import (
    EIPClientError,
    EIPMethodError,
    EIPProtocolError,
    EIPRequestTimeoutError,
    EIPSessionStateError,
    EIPTransportClosedError,
    EIPTransportError,
)
from a13n_envd_client.transport import (
    ControlFrame,
    EIPTransport,
    HttpTransferLifecycle,
    TransferDirection,
)

_MAX_JSONRPC_ID = 2**63 - 1
_TRANSFER_TEARDOWN_TIMEOUT = 5.0
_RESPONSE_TYPE = cast(
    type[JsonRpcSuccessResponse | JsonRpcErrorResponse],
    JsonRpcSuccessResponse | JsonRpcErrorResponse,
)


@dataclass(slots=True)
class _PendingRequest:
    method: MethodSpec[Any, Any]
    future: asyncio.Future[object]


class TransferChannel:
    """One bounded logical transfer inbox owned by the coordinator's physical reader."""

    def __init__(self, handle: str, *, inbound_frames: int) -> None:
        if not isinstance(handle, str) or not handle:
            raise ValueError("transfer handle must be a non-empty string")
        if not isinstance(inbound_frames, int) or isinstance(inbound_frames, bool) or inbound_frames < 1:
            raise ValueError("inbound_frames must be a positive integer")
        self.handle = handle
        # One extra slot is reserved for a terminal peer RESET or local error.
        # The semaphore keeps nonterminal frames at the negotiated allowance
        # while letting the physical receive loop apply transport backpressure.
        self._queue: asyncio.Queue[DataFrame | BaseException] = asyncio.Queue(inbound_frames + 1)
        self._data_slots = asyncio.Semaphore(inbound_frames)
        self._failed = False
        self._discarding = False
        self._discarded = asyncio.Event()
        self._peer_reset_received = False

    @property
    def peer_reset_received(self) -> bool:
        return self._peer_reset_received

    async def receive(self) -> DataFrame:
        item = await self._queue.get()
        if isinstance(item, BaseException):
            raise item
        if item.kind is not DataFrameKind.RESET:
            self._data_slots.release()
        return item

    async def deliver(self, frame: DataFrame) -> None:
        if self._failed or self._discarding:
            return
        if frame.kind is DataFrameKind.RESET:
            if self._peer_reset_received:
                raise EIPProtocolError("received duplicate RESET for one EIP transfer")
            self._peer_reset_received = True
            self._queue.put_nowait(frame)
            return
        acquired = asyncio.create_task(self._data_slots.acquire())
        discarded = asyncio.create_task(self._discarded.wait())
        try:
            done, _ = await asyncio.wait(
                {acquired, discarded},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if discarded in done or self._failed or self._discarding:
                if acquired.done() and not acquired.cancelled() and acquired.exception() is None:
                    self._data_slots.release()
                return
            await acquired
            self._queue.put_nowait(frame)
        finally:
            for task in (acquired, discarded):
                if not task.done():
                    task.cancel()
            await asyncio.gather(acquired, discarded, return_exceptions=True)

    def discard(self) -> None:
        self._discarding = True
        self._discarded.set()

    def fail(self, error: BaseException) -> None:
        if self._failed:
            return
        self._failed = True
        self._discarded.set()
        try:
            self._queue.put_nowait(error)
        except asyncio.QueueFull:
            # A queued peer RESET already provides a terminal channel event.
            pass


class _AdmissionLimiter:
    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._active = 0
        self._waiters: set[asyncio.Future[None]] = set()

    async def acquire(self) -> None:
        while self._active >= self._limit:
            waiter: asyncio.Future[None] = asyncio.get_running_loop().create_future()
            self._waiters.add(waiter)
            try:
                await waiter
            finally:
                self._waiters.discard(waiter)
        self._active += 1

    def release(self) -> None:
        if self._active < 1:
            raise RuntimeError("EIP admission release without an active request")
        self._active -= 1
        self._wake_waiters()

    def set_limit(self, limit: int) -> None:
        self._limit = limit
        self._wake_waiters()

    def narrow_limit(self, limit: int) -> None:
        self._limit = min(self._limit, limit)

    def _wake_waiters(self) -> None:
        for waiter in tuple(self._waiters):
            if not waiter.done():
                waiter.set_result(None)


class RequestCoordinator(EIPRequester):
    """Correlates bounded concurrent generated EIP requests without retrying them."""

    def __init__(
        self,
        transport: EIPTransport,
        *,
        max_in_flight: int = 32,
        request_timeout: float | None = None,
    ) -> None:
        if not isinstance(max_in_flight, int) or isinstance(max_in_flight, bool) or max_in_flight < 1:
            raise ValueError("max_in_flight must be a positive integer")
        if request_timeout is not None and request_timeout <= 0:
            raise ValueError("request_timeout must be positive or None")
        self._transport = transport
        self._admission = _AdmissionLimiter(max_in_flight)
        self._request_timeout = request_timeout
        self._pending: dict[str | int, _PendingRequest] = {}
        self._abandoned_ids: set[str | int] = set()
        self._max_abandoned_ids = max_in_flight
        self._transfers: dict[str, TransferChannel] = {}
        self._retired_transfers: dict[str, asyncio.Task[None]] = {}
        self._max_transfer_channels = 1
        self._next_id = 1
        self._reader_task: asyncio.Task[None] | None = None
        self._close_task: asyncio.Task[None] | None = None
        self._detach_task: asyncio.Task[None] | None = None
        self._closed = False
        self._terminal_error: BaseException | None = None

    async def request[P, R](self, method: MethodSpec[P, R], params: P) -> R:
        if self._closed or self._terminal_error is not None:
            raise EIPTransportClosedError("EIP requester is closed") from self._terminal_error
        if not isinstance(params, method.params_type):
            raise TypeError(f"{method.name} params must be {method.params_type.__name__}")

        loop = asyncio.get_running_loop()
        started_at = loop.time()
        params_payload = json.loads(encode_model(cast(BaseModel, params)))
        if not isinstance(params_payload, dict):
            raise EIPProtocolError("generated EIP params did not encode as an object")

        request_timeout = _effective_timeout(params_payload, self._request_timeout)
        if request_timeout is not None and request_timeout <= 0:
            raise EIPRequestTimeoutError("EIP request timeout expired before dispatch", dispatched=False)
        timeout_at = started_at + request_timeout if request_timeout is not None else None

        try:
            await _wait_until(self._admission.acquire(), timeout_at)
        except TimeoutError as error:
            raise EIPRequestTimeoutError(
                "timed out waiting for EIP admission before dispatch",
                dispatched=False,
            ) from error
        if _deadline_expired(timeout_at):
            self._admission.release()
            raise EIPRequestTimeoutError(
                "EIP request deadline expired before dispatch",
                dispatched=False,
            )
        if self._closed or self._terminal_error is not None:
            self._admission.release()
            raise EIPTransportClosedError("EIP requester is closed") from self._terminal_error

        request_id = self._allocate_id()
        envelope = JsonRpcRequest(jsonrpc="2.0", id=request_id, method=method.name, params=params_payload)
        payload = encode_model(envelope)
        if _deadline_expired(timeout_at):
            self._admission.release()
            raise EIPRequestTimeoutError(
                "EIP request deadline expired before dispatch",
                dispatched=False,
            )

        # Admission is bounded separately. The daemon starts its operation budget
        # after dispatch, so reserve additional time to receive its typed result.
        response_budget = response_timeout(params_payload, self._request_timeout)
        timeout_at = loop.time() + response_budget if response_budget is not None else None
        future: asyncio.Future[object] = asyncio.get_running_loop().create_future()
        pending = _PendingRequest(cast(MethodSpec[Any, Any], method), future)
        self._pending[request_id] = pending
        self._ensure_reader()

        try:
            await _wait_until(self._transport.send(ControlFrame(payload)), timeout_at)
        except TimeoutError as error:
            self._abandon_request(request_id, pending)
            raise EIPRequestTimeoutError(
                "timed out sending an EIP request; operation outcome is not implied",
                dispatched=True,
            ) from error
        except asyncio.CancelledError:
            self._abandon_request(request_id, pending)
            raise
        except Exception as error:
            transport_error = (
                error if isinstance(error, EIPClientError) else EIPTransportError("failed to send EIP request")
            )
            self._remove_pending(request_id, pending)
            self._terminate(transport_error)
            await self._transport.close()
            raise transport_error from error

        try:
            return cast(R, await _wait_until(asyncio.shield(future), timeout_at))
        except TimeoutError as error:
            self._abandon_request(request_id, pending)
            raise EIPRequestTimeoutError(
                "timed out waiting for an EIP response; operation outcome is not implied",
                dispatched=True,
            ) from error
        except asyncio.CancelledError:
            self._abandon_request(request_id, pending)
            raise

    def configure_limits(
        self,
        *,
        max_in_flight: int,
        max_request_bytes: int,
        max_response_bytes: int,
        max_transfer_frame_bytes: int,
        max_concurrent_file_transfers: int,
    ) -> None:
        if self._pending or self._abandoned_ids or self._transfers or self._retired_transfers:
            raise RuntimeError("cannot reconfigure requester limits with active work")
        _validate_positive_integer("max_in_flight", max_in_flight)
        _validate_positive_integer("max_concurrent_file_transfers", max_concurrent_file_transfers)
        self._admission.set_limit(max_in_flight)
        self._max_abandoned_ids = max_in_flight
        self._max_transfer_channels = max_concurrent_file_transfers
        self._transport.set_limits(
            max_request_bytes=max_request_bytes,
            max_response_bytes=max_response_bytes,
            max_transfer_frame_bytes=max_transfer_frame_bytes,
        )

    def narrow_limits(
        self,
        *,
        max_in_flight: int,
        max_request_bytes: int,
        max_response_bytes: int,
        max_transfer_frame_bytes: int,
        max_concurrent_file_transfers: int,
    ) -> None:
        _validate_positive_integer("max_in_flight", max_in_flight)
        _validate_positive_integer("max_concurrent_file_transfers", max_concurrent_file_transfers)
        self._admission.narrow_limit(max_in_flight)
        self._max_abandoned_ids = min(self._max_abandoned_ids, max_in_flight)
        self._max_transfer_channels = min(
            self._max_transfer_channels,
            max_concurrent_file_transfers,
        )
        self._transport.set_limits(
            max_request_bytes=max_request_bytes,
            max_response_bytes=max_response_bytes,
            max_transfer_frame_bytes=max_transfer_frame_bytes,
        )

    def register_transfer(
        self,
        handle: str,
        *,
        direction: TransferDirection = "read",
        inbound_frames: int = 8,
    ) -> TransferChannel:
        if self._closed or self._terminal_error is not None:
            raise EIPTransportClosedError("EIP requester is closed") from self._terminal_error
        if handle in self._transfers:
            raise EIPSessionStateError("transfer handle is already attached locally")
        if handle in self._retired_transfers:
            error = EIPProtocolError("peer reused a transfer handle before RESET acknowledgement")
            self._terminate(error)
            self._start_close()
            raise EIPTransportClosedError("EIP carrier closed after ambiguous transfer handle reuse") from error
        if len(self._transfers) >= self._max_transfer_channels:
            raise EIPSessionStateError("negotiated concurrent transfer limit is exhausted")
        channel = TransferChannel(handle, inbound_frames=inbound_frames)
        if isinstance(self._transport, HttpTransferLifecycle):
            self._transport.register_transfer(handle, direction)
        self._transfers[handle] = channel
        self._ensure_reader()
        return channel

    def unregister_transfer(self, channel: TransferChannel) -> None:
        if self._transfers.get(channel.handle) is not channel:
            raise EIPSessionStateError("transfer channel is not registered")
        del self._transfers[channel.handle]
        if isinstance(self._transport, HttpTransferLifecycle):
            self._transport.unregister_transfer(channel.handle)

    def retire_transfer(self, channel: TransferChannel) -> None:
        self._retire_transfer(channel)

    def transfer_is_retired(self, channel: TransferChannel) -> bool:
        return channel.handle in self._retired_transfers and channel.handle not in self._transfers

    def complete_retired_transfer(self, handle: str) -> None:
        self._complete_retired_transfer(handle)

    async def reset_transfer(self, channel: TransferChannel, frame: DataFrame) -> None:
        if frame.handle != channel.handle or frame.kind is not DataFrameKind.RESET:
            raise ValueError("reset frame does not match its transfer channel")
        self._retire_transfer(channel)
        try:
            async with asyncio.timeout(_TRANSFER_TEARDOWN_TIMEOUT):
                await self._transport.send(frame)
            if isinstance(self._transport, HttpTransferLifecycle):
                self._complete_retired_transfer(channel.handle)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            transport_error = (
                error if isinstance(error, EIPClientError) else EIPTransportError("failed to reset EIP transfer")
            )
            self._terminate(transport_error)
            self._start_close()
            raise transport_error from error

    async def send_data_frame(self, channel: TransferChannel, frame: DataFrame) -> None:
        if self._transfers.get(channel.handle) is not channel:
            raise EIPSessionStateError("transfer channel is not registered")
        if frame.handle != channel.handle:
            raise ValueError("data frame handle does not match its transfer channel")
        try:
            await self._transport.send(frame)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            transport_error = (
                error if isinstance(error, EIPClientError) else EIPTransportError("failed to send EIP data frame")
            )
            self._terminate(transport_error)
            await self._transport.close()
            raise transport_error from error

    async def close_for_protocol_error(self, error: EIPProtocolError) -> None:
        self._terminate(error)
        await self.close()

    async def close(self) -> None:
        self._start_close()
        close_task = self._close_task
        assert close_task is not None
        await _await_shared_close(close_task)

    async def detach(self) -> None:
        """Stop this requester while preserving an unambiguous reusable transport."""
        if self._close_task is not None:
            await self.close()
            raise EIPSessionStateError("cannot detach a requester whose transport is closing")
        if self._detach_task is None:
            self._closed = True
            self._detach_task = asyncio.create_task(self._detach(), name="eip-requester-detach")
        await _await_shared_close(self._detach_task)

    async def _detach(self) -> None:
        reader_task = self._reader_task
        if reader_task is not None:
            reader_task.cancel()
            await asyncio.gather(reader_task, return_exceptions=True)
        if (
            self._terminal_error is not None
            or self._pending
            or self._abandoned_ids
            or self._transfers
            or self._retired_transfers
        ):
            error = EIPSessionStateError("EIP requester cannot detach with ambiguous session state")
            try:
                await self._transport.close()
            finally:
                self._terminate(error)
                self._abandoned_ids.clear()
                self._clear_retired_transfers()
            raise error from self._terminal_error

    async def _close(self) -> None:
        reader_task = self._reader_task
        if reader_task is not None:
            reader_task.cancel()
        try:
            await self._transport.close()
        finally:
            if reader_task is not None:
                await asyncio.gather(reader_task, return_exceptions=True)
            closed = EIPTransportClosedError("EIP requester closed")
            self._fail_pending(closed)
            self._abandoned_ids.clear()
            self._fail_transfers(closed)
            self._clear_retired_transfers()

    def _ensure_reader(self) -> None:
        if self._reader_task is None:
            self._reader_task = asyncio.create_task(self._reader_loop(), name="eip-response-reader")

    def _allocate_id(self) -> int:
        for _ in range(len(self._pending) + len(self._abandoned_ids) + 1):
            request_id = self._next_id
            self._next_id = 1 if request_id == _MAX_JSONRPC_ID else request_id + 1
            if request_id not in self._pending and request_id not in self._abandoned_ids:
                return request_id
        raise RuntimeError("no JSON-RPC request ID is available")

    async def _reader_loop(self) -> None:
        try:
            while not self._closed:
                frame = await self._transport.receive()
                if isinstance(frame, ControlFrame):
                    self._handle_control_frame(frame)
                    continue
                if isinstance(frame, DataFrame):
                    await self._handle_data_frame(frame)
                    continue
                raise EIPProtocolError("transport returned an unsupported EIP frame")
        except asyncio.CancelledError:
            return
        except Exception as error:
            terminal_error = error if isinstance(error, EIPClientError) else EIPProtocolError("invalid EIP response")
            self._terminate(terminal_error)
            await self._transport.close()

    def _handle_control_frame(self, frame: ControlFrame) -> None:
        response = decode_model(frame.payload, _RESPONSE_TYPE)
        if response.id is None:
            raise EIPProtocolError("received an uncorrelated JSON-RPC error response")
        if response.id in self._abandoned_ids:
            self._abandoned_ids.remove(response.id)
            return
        pending = self._pending.pop(response.id, None)
        if pending is None:
            raise EIPProtocolError("received an unknown or duplicate JSON-RPC response ID")
        self._admission.release()

        if isinstance(response, JsonRpcErrorResponse):
            pending.future.set_exception(EIPMethodError(response.error))
            return

        try:
            result_payload = json.dumps(
                response.result,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            result = decode_model(result_payload, pending.method.result_type)
        except Exception as error:
            protocol_error = EIPProtocolError(f"invalid {pending.method.name} result from EIP peer")
            pending.future.set_exception(protocol_error)
            raise protocol_error from error
        pending.future.set_result(result)

    async def _handle_data_frame(self, frame: DataFrame) -> None:
        channel = self._transfers.get(frame.handle)
        if channel is not None:
            await channel.deliver(frame)
            return
        if frame.handle in self._retired_transfers:
            if frame.kind is DataFrameKind.RESET:
                self._complete_retired_transfer(frame.handle)
            return
        raise EIPProtocolError("received an EIP data frame for an unknown transfer handle")

    def _retire_transfer(self, channel: TransferChannel) -> None:
        if self._transfers.get(channel.handle) is not channel:
            raise EIPSessionStateError("transfer channel is not registered")
        if len(self._retired_transfers) >= self._max_transfer_channels:
            error = EIPProtocolError("retired transfer capacity exhausted before RESET acknowledgement")
            self._terminate(error)
            self._start_close()
            raise EIPTransportClosedError("EIP carrier closed after transfer teardown stalled") from error
        channel.discard()
        del self._transfers[channel.handle]
        self._retired_transfers[channel.handle] = asyncio.create_task(
            self._expire_retired_transfer(channel.handle),
            name=f"eip-transfer-teardown-{channel.handle}",
        )

    async def _expire_retired_transfer(self, handle: str) -> None:
        await asyncio.sleep(_TRANSFER_TEARDOWN_TIMEOUT)
        if self._retired_transfers.get(handle) is not asyncio.current_task():
            return
        error = EIPProtocolError("transfer RESET acknowledgement timed out")
        self._terminate(error)
        self._start_close()

    def _complete_retired_transfer(self, handle: str) -> None:
        task = self._retired_transfers.pop(handle, None)
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    def _clear_retired_transfers(self) -> None:
        current = asyncio.current_task()
        tasks = tuple(self._retired_transfers.values())
        self._retired_transfers.clear()
        for task in tasks:
            if task is not current:
                task.cancel()

    def _remove_pending(self, request_id: str | int, pending: _PendingRequest) -> bool:
        if self._pending.get(request_id) is not pending:
            return False
        del self._pending[request_id]
        self._admission.release()
        return True

    def _abandon_request(self, request_id: str | int, pending: _PendingRequest) -> None:
        if not self._remove_pending(request_id, pending):
            return
        if len(self._abandoned_ids) >= self._max_abandoned_ids:
            error = EIPProtocolError("abandoned request correlation capacity is exhausted")
            self._terminate(error)
            self._start_close()
            return
        self._abandoned_ids.add(request_id)

    def _start_close(self) -> None:
        if self._close_task is None:
            self._closed = True
            self._close_task = asyncio.create_task(self._close(), name="eip-requester-close")

    def _terminate(self, error: BaseException) -> None:
        if self._terminal_error is None:
            self._terminal_error = error
        self._fail_pending(error)
        self._fail_transfers(error)

    def _fail_pending(self, error: BaseException) -> None:
        pending_requests = tuple(self._pending.values())
        self._pending.clear()
        for pending in pending_requests:
            self._admission.release()
            if not pending.future.done():
                pending.future.set_exception(error)

    def _fail_transfers(self, error: BaseException) -> None:
        for channel in self._transfers.values():
            channel.fail(error)


async def _await_shared_close(close_task: asyncio.Task[None]) -> None:
    cancelled = False
    while True:
        try:
            await asyncio.shield(close_task)
        except asyncio.CancelledError:
            if close_task.done():
                raise
            cancelled = True
            continue
        except BaseException:
            if cancelled:
                raise asyncio.CancelledError from None
            raise
        break
    if cancelled:
        raise asyncio.CancelledError


async def _wait_until[T](awaitable: Awaitable[T], timeout_at: float | None) -> T:
    if timeout_at is None:
        return await awaitable
    async with asyncio.timeout_at(timeout_at):
        return await awaitable


def _validate_positive_integer(name: str, value: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{name} must be a positive integer")


def _deadline_expired(timeout_at: float | None) -> bool:
    return timeout_at is not None and asyncio.get_running_loop().time() >= timeout_at


def _effective_timeout(params: dict[str, Any], configured_timeout: float | None) -> float | None:
    context = params.get("context")
    if not isinstance(context, dict):
        return configured_timeout
    timeout_ms = context.get("timeout_ms")
    if not isinstance(timeout_ms, int) or isinstance(timeout_ms, bool):
        return configured_timeout
    requested_timeout = timeout_ms / 1000
    return requested_timeout if configured_timeout is None else min(configured_timeout, requested_timeout)
