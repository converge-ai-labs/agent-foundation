from __future__ import annotations

import asyncio
import json

import pytest
from a13n_envd_client import (
    ControlFrame,
    EIPDeviceConnection,
    EIPMethodError,
    EIPProtocolError,
    EIPRequestTimeoutError,
    EIPSessionStateError,
    EIPTransferError,
    EIPTransportClosedError,
    EIPTransportFrame,
    RequestCoordinator,
)
from a13n_envd_client.eip.v1 import (
    DataFrame,
    DataFrameKind,
    DataResetStatus,
    DeviceDescribeParams,
    DeviceDescriptor,
    EIPCallContext,
    EIPLimits,
    EnvironmentReadinessParams,
    ExecutionFeatures,
    JsonRpcRequest,
    PathStyle,
    SessionCloseParams,
    SessionDescriptor,
    SessionKeepaliveParams,
    SessionLifecyclePolicy,
    decode_model,
)
from a13n_envd_client.eip.v1.methods import DEVICE_DESCRIBE, ENVIRONMENT_READINESS, SESSION_CLOSE, SESSION_KEEPALIVE


class FakeTransport:
    def __init__(self) -> None:
        self.sent: asyncio.Queue[EIPTransportFrame] = asyncio.Queue()
        self.responses: asyncio.Queue[EIPTransportFrame | BaseException] = asyncio.Queue()
        self.closed = False
        self.limits: dict[str, int] = {}

    async def send(self, frame: EIPTransportFrame) -> None:
        await self.sent.put(frame)

    async def receive(self) -> EIPTransportFrame:
        response = await self.responses.get()
        if isinstance(response, BaseException):
            raise response
        return response

    async def close(self) -> None:
        self.closed = True

    def set_limits(self, **limits: int) -> None:
        self.limits = limits

    async def request(self) -> JsonRpcRequest:
        frame = await asyncio.wait_for(self.sent.get(), 1)
        assert isinstance(frame, ControlFrame)
        return decode_model(frame.payload, JsonRpcRequest)

    def reply(self, request: JsonRpcRequest, result: dict) -> None:
        envelope = {"jsonrpc": "2.0", "id": request.id, "result": result}
        if request.eip_session is not None:
            envelope["eip_session"] = request.eip_session
        self.responses.put_nowait(ControlFrame(json.dumps(envelope).encode()))


def limits() -> EIPLimits:
    return EIPLimits(
        max_request_bytes=4096,
        max_response_bytes=4096,
        max_concurrent_operations=4,
        max_processes=4,
        max_operation_duration_ms=1000,
        max_output_preview_bytes=128,
        max_output_bytes_per_stream=1024,
        max_transfer_frame_bytes=1024,
        max_concurrent_file_transfers=2,
        max_file_transfer_bytes=4096,
        max_file_bytes=4096,
    )


def descriptor(session_id: str = "ses-a", *, idle_timeout_ms: int = 30_000) -> SessionDescriptor:
    return SessionDescriptor(
        device_id="device-test",
        generation=1,
        session_id=session_id,
        working_directory="/work",
        available_methods=("environment.readiness", "environment.describe", "session.keepalive", "session.close"),
        limits=limits(),
        execution_features=ExecutionFeatures(
            process_count_limit=False,
            memory_bytes_limit=False,
            cpu_time_limit=False,
            signal_interrupt=False,
            signal_terminate=False,
        ),
        lifecycle=SessionLifecyclePolicy(idle_timeout_ms=idle_timeout_ms, disconnect_grace_ms=1000),
    )


def device_descriptor() -> DeviceDescriptor:
    return DeviceDescriptor(
        device_id="device-test",
        generation=1,
        path_style=PathStyle.POSIX,
        default_working_directory="/work",
        directory_discovery=True,
        available_methods=("device.describe", "directory.list", "session.open"),
        limits=limits(),
        lifecycle=descriptor().lifecycle,
    )


def readiness(session_id: str = "ses-a") -> dict:
    return {"ready": True, "device_id": "device-test", "generation": 1, "session_id": session_id}


def params(operation: str = "op-test", timeout_ms: int | None = None) -> EnvironmentReadinessParams:
    return EnvironmentReadinessParams(context=EIPCallContext(operation_id=operation, timeout_ms=timeout_ms))


async def initialized(transport: FakeTransport) -> EIPDeviceConnection:
    task = asyncio.create_task(EIPDeviceConnection.initialize(transport, expected_device_id="device-test"))
    request = await transport.request()
    assert request.method == "initialize" and request.eip_session is None
    assert request.params["supported_protocol_versions"] == ["0.1"]
    transport.reply(
        request,
        {
            "protocol_version": "0.1",
            "server": {"name": "a13n-envd", "version": "0"},
            "descriptor": device_descriptor().model_dump(mode="json", exclude_none=True),
        },
    )
    return await task


async def opened(
    device: EIPDeviceConnection, transport: FakeTransport, session_id: str = "ses-a", *, idle_timeout_ms: int = 30_000
):
    task = asyncio.create_task(device.open_session(working_directory="/work"))
    request = await transport.request()
    assert request.method == "session.open" and request.eip_session is None
    assert request.params["expected_device_id"] == "device-test"
    transport.reply(
        request, {"descriptor": descriptor(session_id, idle_timeout_ms=idle_timeout_ms).model_dump(mode="json")}
    )
    request = await transport.request()
    assert request.method == "environment.readiness" and request.eip_session == session_id
    assert not task.done()
    transport.reply(request, readiness(session_id))
    return await task


async def closed(session, transport: FakeTransport) -> None:
    task = asyncio.create_task(session.close())
    request = await transport.request()
    assert request.method == "session.close" and request.params == {}
    transport.reply(request, {"closed": True})
    await task


def test_device_handshake_opens_no_session_and_close_is_session_local():
    async def scenario():
        transport = FakeTransport()
        device = await initialized(transport)
        assert transport.sent.empty()
        a = await opened(device, transport)
        b = await opened(device, transport, "ses-b")
        await closed(a, transport)
        assert not transport.closed
        task = asyncio.create_task(b.readiness())
        request = await transport.request()
        transport.reply(request, readiness("ses-b"))
        assert (await task).ready
        c = await opened(device, transport, "ses-c")
        assert c.generation == b.generation
        await closed(b, transport)
        await closed(c, transport)
        await device.close()
        assert transport.closed

    asyncio.run(scenario())


def test_independent_scopes_and_out_of_order_replies():
    async def scenario():
        transport = FakeTransport()
        device = RequestCoordinator(transport)
        a = device.session("ses-a", limits=limits())
        b = device.session("ses-b", limits=limits())
        tasks = [asyncio.create_task(owner.request(ENVIRONMENT_READINESS, params())) for owner in (a, b)]
        requests = [await transport.request(), await transport.request()]
        assert requests[0].id != requests[1].id
        for request in reversed(requests):
            transport.reply(request, readiness(request.eip_session))
        assert [result.session_id for result in await asyncio.gather(*tasks)] == ["ses-a", "ses-b"]
        await device.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("cancel", [True, False])
def test_abandoned_request_keeps_admission_until_late_reply_without_harming_sibling(cancel: bool):
    async def scenario():
        transport = FakeTransport()
        device = RequestCoordinator(transport, request_timeout=0.05)
        a = device.session("ses-a", limits=limits(), max_in_flight=1)
        b = device.session("ses-b", limits=limits(), max_in_flight=1)
        abandoned = asyncio.create_task(a.request(ENVIRONMENT_READINESS, params()))
        first = await transport.request()
        if cancel:
            abandoned.cancel()
        with pytest.raises(asyncio.CancelledError if cancel else EIPRequestTimeoutError):
            await abandoned
        for index in range(3):
            with pytest.raises(EIPRequestTimeoutError) as caught:
                await a.request(ENVIRONMENT_READINESS, params(f"blocked-{index}", timeout_ms=1))
            assert not caught.value.dispatched
        assert len(device._pending) == 1 and a._admission.active == 1
        sibling = asyncio.create_task(b.request(ENVIRONMENT_READINESS, params()))
        request = await transport.request()
        assert request.eip_session == "ses-b"
        transport.reply(request, readiness("ses-b"))
        assert (await sibling).ready
        keepalive = asyncio.create_task(a.request(SESSION_KEEPALIVE, SessionKeepaliveParams()))
        request = await transport.request()
        transport.reply(request, {"alive": True})
        assert (await keepalive).alive
        transport.reply(first, readiness())
        later = asyncio.create_task(a.request(ENVIRONMENT_READINESS, params("later")))
        request = await transport.request()
        transport.reply(request, readiness())
        assert (await later).ready and not transport.closed
        await device.close()

    asyncio.run(scenario())


def test_cancel_during_send_does_not_cancel_shared_carrier_write():
    async def scenario():
        class BlockedSend(FakeTransport):
            def __init__(self):
                super().__init__()
                self.release = asyncio.Event()
                self.cancelled = False

            async def send(self, frame):
                await super().send(frame)
                try:
                    await self.release.wait()
                except asyncio.CancelledError:
                    self.cancelled = True
                    raise

        transport = BlockedSend()
        device = RequestCoordinator(transport)
        a = device.session("ses-a", limits=limits())
        task = asyncio.create_task(a.request(ENVIRONMENT_READINESS, params()))
        request = await transport.request()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not transport.cancelled and a._admission.active == 1
        transport.release.set()
        transport.reply(request, readiness())
        await asyncio.sleep(0)
        await device.close()

    asyncio.run(scenario())


def test_close_and_keepalive_have_independent_admission_when_device_full():
    async def scenario():
        transport = FakeTransport()
        device = RequestCoordinator(transport, max_in_flight=1)
        a = device.session("ses-a", limits=limits(), max_in_flight=1)
        normal = asyncio.create_task(a.request(ENVIRONMENT_READINESS, params()))
        normal_request = await transport.request()
        keepalive = asyncio.create_task(a.request(SESSION_KEEPALIVE, SessionKeepaliveParams()))
        keepalive_request = await transport.request()
        closing = asyncio.create_task(a.request(SESSION_CLOSE, SessionCloseParams()))
        close_request = await transport.request()
        for request, result in (
            (close_request, {"closed": True}),
            (keepalive_request, {"alive": True}),
            (normal_request, readiness()),
        ):
            transport.reply(request, result)
        await asyncio.gather(normal, keepalive, closing)
        await device.close()

    asyncio.run(scenario())


def test_stalled_transfer_is_bounded_and_does_not_block_sibling_control_or_data():
    async def scenario():
        transport = FakeTransport()
        device = RequestCoordinator(transport)
        a = device.session("ses-a", limits=limits())
        b = device.session("ses-b", limits=limits())
        ca = a.register_transfer("same-handle", inbound_frames=1)
        cb = b.register_transfer("same-handle", inbound_frames=1)
        for session_id in ("ses-a", "ses-a", "ses-b"):
            transport.responses.put_nowait(
                DataFrame(kind=DataFrameKind.CHUNK, session_id=session_id, handle="same-handle", payload=b"x")
            )
        keepalive = asyncio.create_task(b.request(SESSION_KEEPALIVE, SessionKeepaliveParams()))
        # A's overflow RESET may race B's request; neither waits for A's consumer.
        request = None
        reset = None
        for _ in range(2):
            frame = await asyncio.wait_for(transport.sent.get(), 1)
            if isinstance(frame, ControlFrame):
                request = decode_model(frame.payload, JsonRpcRequest)
            else:
                reset = frame
        assert reset is not None and reset.session_id == "ses-a" and reset.kind is DataFrameKind.RESET
        assert request is not None
        transport.reply(request, {"alive": True})
        assert (await keepalive).alive
        with pytest.raises(EIPTransferError):
            await ca.receive()
        assert (await cb.receive()).payload == b"x"
        transport.responses.put_nowait(
            DataFrame(
                kind=DataFrameKind.RESET, session_id="ses-a", handle="same-handle", reset_status=DataResetStatus.LIMIT
            )
        )
        await device.close()

    asyncio.run(scenario())


def test_peer_reset_has_reserved_inbox_slot():
    async def scenario():
        transport = FakeTransport()
        device = RequestCoordinator(transport)
        owner = device.session("ses-a", limits=limits())
        channel = owner.register_transfer("reader", inbound_frames=1)
        channel.deliver(DataFrame(kind=DataFrameKind.CHUNK, session_id="ses-a", handle="reader", payload=b"x"))
        channel.deliver(
            DataFrame(
                kind=DataFrameKind.RESET, session_id="ses-a", handle="reader", reset_status=DataResetStatus.SOURCE
            )
        )
        assert (await channel.receive()).kind is DataFrameKind.CHUNK
        assert (await channel.receive()).kind is DataFrameKind.RESET
        await device.close()

    asyncio.run(scenario())


def test_bad_session_result_closes_only_that_session():
    async def scenario():
        transport = FakeTransport()
        device = RequestCoordinator(transport)
        a = device.session("ses-a", limits=limits())
        b = device.session("ses-b", limits=limits())
        bad = asyncio.create_task(a.request(ENVIRONMENT_READINESS, params()))
        request = await transport.request()
        transport.reply(request, {"ready": "not-a-boolean"})
        with pytest.raises(EIPProtocolError):
            await bad
        close_request = await transport.request()
        assert close_request.eip_session == "ses-a" and close_request.method == "session.close"
        transport.reply(close_request, {"closed": True})
        good = asyncio.create_task(b.request(ENVIRONMENT_READINESS, params()))
        request = await transport.request()
        transport.reply(request, readiness("ses-b"))
        assert (await good).ready and not transport.closed
        await device.close()

    asyncio.run(scenario())


def test_carrier_eof_wakes_pending_and_admission_waiters():
    async def scenario():
        transport = FakeTransport()
        device = RequestCoordinator(transport)
        a = device.session("ses-a", limits=limits(), max_in_flight=1)
        first = asyncio.create_task(a.request(ENVIRONMENT_READINESS, params()))
        await transport.request()
        second = asyncio.create_task(a.request(ENVIRONMENT_READINESS, params()))
        transport.responses.put_nowait(EIPTransportClosedError("EOF"))
        for task in (first, second):
            with pytest.raises((EIPTransportClosedError, EIPSessionStateError)):
                await asyncio.wait_for(task, 1)
        await device.close()
        assert not device._pending

    asyncio.run(scenario())


def test_session_close_survives_caller_cancellation_without_closing_device():
    async def scenario():
        transport = FakeTransport()
        device = await initialized(transport)
        session = await opened(device, transport)
        first = asyncio.create_task(session.close())
        request = await transport.request()
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        second = asyncio.create_task(session.close())
        transport.reply(request, {"closed": True})
        await second
        assert transport.sent.empty() and not transport.closed
        await device.close()

    asyncio.run(scenario())


def test_keepalive_runs_without_any_operations():
    async def scenario():
        transport = FakeTransport()
        device = await initialized(transport)
        session = await opened(device, transport, idle_timeout_ms=300)
        request = await transport.request()
        assert request.method == "session.keepalive" and request.eip_session == session.session_id
        transport.reply(request, {"alive": True})
        await closed(session, transport)
        await device.close()

    asyncio.run(scenario())


def test_attachment_requires_same_device_generation_before_dispatch():
    async def scenario():
        transport = FakeTransport()
        device = await initialized(transport)
        with pytest.raises(EIPProtocolError):
            await device.attach_session(descriptor().model_copy(update={"generation": 2}))
        assert transport.sent.empty()
        await device.close()

    asyncio.run(scenario())


def test_device_and_session_method_scopes_are_enforced_locally():
    async def scenario():
        transport = FakeTransport()
        device = RequestCoordinator(transport)
        owner = device.session("ses-a", limits=limits())
        with pytest.raises(EIPSessionStateError):
            await device.request(ENVIRONMENT_READINESS, params())
        with pytest.raises(EIPSessionStateError):
            await owner.request(DEVICE_DESCRIBE, DeviceDescribeParams())
        assert transport.sent.empty()
        await device.close()

    asyncio.run(scenario())


def test_typed_error_is_not_connection_terminal():
    async def scenario():
        transport = FakeTransport()
        device = RequestCoordinator(transport)
        owner = device.session("ses-a", limits=limits())
        task = asyncio.create_task(owner.request(ENVIRONMENT_READINESS, params()))
        request = await transport.request()
        transport.responses.put_nowait(
            ControlFrame(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": request.id,
                        "eip_session": "ses-a",
                        "error": {
                            "code": -32602,
                            "message": "not ready",
                            "data": {
                                "error_type": "invalid_params",
                                "retry_hint": "never",
                                "dispatch_stage": "pre_dispatch",
                            },
                        },
                    }
                ).encode()
            )
        )
        with pytest.raises(EIPMethodError):
            await task
        assert not transport.closed
        await device.close()

    asyncio.run(scenario())


def test_closed_sessions_keep_bounded_correlation_ownership_until_late_reply():
    async def scenario():
        transport = FakeTransport()
        device = RequestCoordinator(transport, max_sessions=1)
        owner = device.session("ses-a", limits=limits())
        task = asyncio.create_task(owner.request(ENVIRONMENT_READINESS, params()))
        request = await transport.request()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        owner.finish(EIPSessionStateError("local abort"))
        with pytest.raises(EIPSessionStateError, match="capacity"):
            device.session("ses-b", limits=limits())
        transport.reply(request, readiness())
        for _ in range(10):
            if not device._pending:
                break
            await asyncio.sleep(0)
        assert not device._pending
        device.session("ses-b", limits=limits())
        await device.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("completion", ["ack", "immediate-ack", "session-close", "device-loss"])
def test_reader_retirement_waits_for_evidence_without_blocking_siblings(completion):
    async def scenario():
        transport = FakeTransport()
        device = RequestCoordinator(transport)
        owner = device.session("ses-a", limits=limits().model_copy(update={"max_concurrent_file_transfers": 1}))
        sibling = device.session("ses-b", limits=limits())
        channel = owner.register_transfer("reader")
        reset = DataFrame(
            kind=DataFrameKind.RESET, session_id="ses-a", handle="reader", reset_status=DataResetStatus.CANCELLED
        )
        if completion == "immediate-ack":
            original_send = transport.send

            async def immediate_send(frame):
                await original_send(frame)
                if isinstance(frame, DataFrame):
                    owner.deliver(frame)

            transport.send = immediate_send
        await owner.reset_transfer(channel, reset)
        assert await transport.sent.get() == reset
        waiter = asyncio.create_task(owner.wait_transfer_retired(channel))
        ready = asyncio.create_task(sibling.request(ENVIRONMENT_READINESS, params()))
        request = await transport.request()
        transport.reply(request, readiness())
        await ready  # A deterministic checkpoint after the retirement waiter ran.
        if completion != "immediate-ack":
            assert not waiter.done()
            with pytest.raises(EIPSessionStateError, match="capacity"):
                owner.register_transfer("replacement")
        if completion == "ack":
            owner.deliver(reset)
        elif completion == "session-close":
            owner.finish(EIPSessionStateError("closed during reset"))
        elif completion == "device-loss":
            await device.close()
        if completion in {"ack", "immediate-ack"}:
            await waiter
            owner.register_transfer("replacement")
        else:
            with pytest.raises((EIPSessionStateError, EIPTransportClosedError)):
                await waiter
        assert not owner._retired
        await device.close()

    asyncio.run(scenario())


def test_stalled_reset_ack_closes_affected_session_not_shared_carrier(monkeypatch):
    import a13n_envd_client.requester as runtime

    monkeypatch.setattr(runtime, "_TRANSFER_TEARDOWN_TIMEOUT", 0.02)

    async def scenario():
        transport = FakeTransport()
        device = RequestCoordinator(transport)
        owner = device.session("ses-a", limits=limits())
        channel = owner.register_transfer("reader")
        await owner.reset_transfer(
            channel,
            DataFrame(
                kind=DataFrameKind.RESET,
                session_id="ses-a",
                handle="reader",
                reset_status=DataResetStatus.CANCELLED,
            ),
        )
        assert isinstance(await transport.sent.get(), DataFrame)
        request = await transport.request()
        assert request.method == "session.close" and request.eip_session == "ses-a"
        transport.reply(request, {"closed": True})
        await asyncio.sleep(0)
        assert not transport.closed
        await device.close()

    asyncio.run(scenario())


def test_encoding_deadline_expiry_does_not_dispatch(monkeypatch):
    import time

    import a13n_envd_client.requester as runtime

    original = runtime.encode_model

    def delayed(model):
        encoded = original(model)
        time.sleep(0.01)
        return encoded

    monkeypatch.setattr(runtime, "encode_model", delayed)

    async def scenario():
        transport = FakeTransport()
        device = RequestCoordinator(transport)
        owner = device.session("ses-a", limits=limits())
        with pytest.raises(EIPRequestTimeoutError) as caught:
            await owner.request(ENVIRONMENT_READINESS, params(timeout_ms=1))
        assert not caught.value.dispatched and transport.sent.empty()
        assert owner._admission.active == 0
        await device.close()

    asyncio.run(scenario())


def test_exact_session_reattachment_does_not_initialize_or_replay_operations():
    async def scenario():
        transport = FakeTransport()
        device = await initialized(transport)
        task = asyncio.create_task(device.attach_session(descriptor()))
        request = await transport.request()
        assert request.method == "session.attach" and request.eip_session == "ses-a" and request.params == {}
        transport.reply(request, {"descriptor": descriptor().model_dump(mode="json")})
        ready = await transport.request()
        assert ready.method == "environment.readiness"
        transport.reply(ready, readiness())
        session = await task
        assert session.session_id == "ses-a"
        await closed(session, transport)
        await device.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("abandon", ["cancel", "timeout", "response-race"])
def test_abandoned_session_open_keeps_capacity_through_exact_cleanup(abandon: str):
    async def scenario():
        transport = FakeTransport()
        device = await initialized(transport)
        sibling = await opened(device, transport, "ses-sibling")
        coordinator = device._requester
        coordinator._request_timeout = 0.05
        coordinator._device_admission.limit = 1
        task = asyncio.create_task(device.open_session())
        request = await transport.request()
        result = {"descriptor": descriptor("ses-abandoned").model_dump(mode="json")}
        if abandon == "response-race":
            # Deliver synchronously, then cancel before the waiting task resumes.
            coordinator._handle_control(
                ControlFrame(json.dumps({"jsonrpc": "2.0", "id": request.id, "result": result}).encode())
            )
        if abandon != "timeout":
            task.cancel()
        with pytest.raises(EIPRequestTimeoutError if abandon == "timeout" else asyncio.CancelledError):
            await task
        if abandon != "response-race":
            transport.reply(request, result)
        cleanup = await transport.request()
        assert cleanup.method == "session.close" and cleanup.eip_session == "ses-abandoned"
        assert len(coordinator._pending) == coordinator._device_admission.active == 1
        assert set(device._sessions) == {"ses-sibling"}
        assert set(coordinator._sessions) == {"ses-sibling"}
        with pytest.raises(EIPRequestTimeoutError) as caught:
            await device.open_session()
        assert not caught.value.dispatched and transport.sent.empty()
        alive = asyncio.create_task(sibling.readiness())
        keepalive = await transport.request()
        transport.reply(keepalive, readiness("ses-sibling"))
        assert (await alive).ready and not transport.closed
        transport.reply(cleanup, {"closed": True})
        replacement = await opened(device, transport, "ses-replacement")
        assert coordinator._device_admission.active == 0
        await closed(replacement, transport)
        await closed(sibling, transport)
        await device.close()

    asyncio.run(scenario())


def test_session_open_binding_failure_closes_unclaimed_session_at_local_capacity():
    async def scenario():
        transport = FakeTransport()
        device = await initialized(transport)
        sibling = await opened(device, transport, "ses-sibling")
        device._requester._max_sessions = 1
        task = asyncio.create_task(device.open_session())
        request = await transport.request()
        transport.reply(request, {"descriptor": descriptor("ses-overflow").model_dump(mode="json")})
        with pytest.raises(EIPSessionStateError, match="capacity"):
            await task
        cleanup = await transport.request()
        assert cleanup.method == "session.close" and cleanup.eip_session == "ses-overflow"
        assert device._requester._device_admission.active == 1
        transport.reply(cleanup, {"closed": True})
        await closed(sibling, transport)
        await device.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("cancel", [False, True])
def test_device_shutdown_drains_open_response_and_cleanup_before_carrier_close(cancel: bool):
    async def scenario():
        transport = FakeTransport()
        device = await initialized(transport)
        task = asyncio.create_task(device.open_session())
        request = await transport.request()
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        shutdown = asyncio.create_task(device.close())
        await asyncio.sleep(0)
        transport.reply(request, {"descriptor": descriptor("ses-late").model_dump(mode="json")})
        if not cancel:
            with pytest.raises(EIPSessionStateError, match="closed"):
                await task
        cleanup = await transport.request()
        assert cleanup.method == "session.close" and cleanup.eip_session == "ses-late"
        assert not transport.closed and not shutdown.done()
        transport.reply(cleanup, {"closed": True})
        await asyncio.wait_for(shutdown, 1)
        assert transport.closed and not device._requester._pending
        assert not device._sessions

    asyncio.run(scenario())


def test_abandoned_open_cannot_close_a_duplicate_live_session_descriptor():
    async def scenario():
        transport = FakeTransport()
        device = await initialized(transport)
        sibling = await opened(device, transport)
        task = asyncio.create_task(device.open_session())
        request = await transport.request()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        transport.reply(request, {"descriptor": sibling.descriptor.model_dump(mode="json")})
        alive = asyncio.create_task(sibling.readiness())
        request = await transport.request()
        assert request.method == "environment.readiness"
        transport.reply(request, readiness())
        assert (await alive).ready
        await closed(sibling, transport)
        await device.close()

    asyncio.run(scenario())


def test_host_scope_rejection_does_not_close_device_or_sibling(monkeypatch):
    async def scenario():
        transport = FakeTransport()
        device = await initialized(transport)
        first = await opened(device, transport)
        second = await opened(device, transport, "ses-b")
        original_send = transport.send

        async def send(frame):
            if isinstance(frame, ControlFrame):
                request = decode_model(frame.payload, JsonRpcRequest)
                if request.eip_session == first.session_id or request.method == "session.open":
                    raise EIPSessionStateError("Host use authority expired before dispatch")
            await original_send(frame)

        monkeypatch.setattr(transport, "send", send)
        with pytest.raises(EIPSessionStateError):
            await first.readiness()
        with pytest.raises(EIPSessionStateError):
            await device.open_session(working_directory="/work")
        assert not transport.closed
        request_task = asyncio.create_task(second.readiness())
        request = await transport.request()
        assert request.eip_session == "ses-b"
        transport.reply(request, readiness("ses-b"))
        assert (await request_task).ready
        describe_task = asyncio.create_task(device.describe())
        request = await transport.request()
        transport.reply(request, {"descriptor": device_descriptor().model_dump(mode="json")})
        assert (await describe_task).device_id == "device-test"
        await closed(second, transport)
        await device.close()

    asyncio.run(scenario())


def test_abandoned_session_open_cleanup_preserves_its_host_dispatch_context(monkeypatch):
    from contextvars import ContextVar

    async def scenario():
        authority = ContextVar("test_use", default=None)
        transport = FakeTransport()
        device = await initialized(transport)
        original_send = transport.send
        observed = []

        async def send(frame):
            if isinstance(frame, ControlFrame):
                request = decode_model(frame.payload, JsonRpcRequest)
                observed.append((request.method, authority.get()))
            await original_send(frame)

        monkeypatch.setattr(transport, "send", send)
        token = authority.set("binding-a")
        opening = asyncio.create_task(device.open_session(working_directory="/work"))
        authority.reset(token)
        request = await transport.request()
        opening.cancel()
        with pytest.raises(asyncio.CancelledError):
            await opening
        transport.reply(request, {"descriptor": descriptor().model_dump(mode="json")})
        cleanup = await transport.request()
        assert cleanup.method == "session.close" and cleanup.eip_session == "ses-a"
        transport.reply(cleanup, {"closed": True})
        await asyncio.sleep(0)
        assert observed == [("session.open", "binding-a"), ("session.close", "binding-a")]
        await device.close()

    asyncio.run(scenario())
