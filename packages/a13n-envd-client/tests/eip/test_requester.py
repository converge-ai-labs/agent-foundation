from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import a13n_envd_client.requester as requester_module
import pytest
from a13n_envd_client import (
    ControlFrame,
    EIPMethodError,
    EIPProtocolError,
    EIPRequestTimeoutError,
    EIPSession,
    EIPSessionStateError,
    EIPTransportClosedError,
    EIPTransportFrame,
    RequestCoordinator,
)
from a13n_envd_client.eip.v1 import (
    DataFrame,
    DataFrameKind,
    DataResetStatus,
    DispatchStage,
    EIPCallContext,
    EIPClient,
    EIPError,
    EIPErrorData,
    EIPLimits,
    EIPServerInfo,
    EnvironmentDescribeParams,
    EnvironmentDescribeResult,
    EnvironmentDescriptor,
    EnvironmentReadinessResult,
    ErrorType,
    ExecutionFeatures,
    InitializeResult,
    IsolationBackend,
    IsolationCleanupGuarantee,
    IsolationMode,
    IsolationNetworkPolicy,
    IsolationPosture,
    JsonRpcErrorResponse,
    JsonRpcRequest,
    JsonRpcSuccessResponse,
    RetryHint,
    SessionCloseResult,
    decode_model,
    encode_model,
)


class FakeTransport:
    def __init__(self) -> None:
        self.sent: asyncio.Queue[EIPTransportFrame] = asyncio.Queue()
        self.responses: asyncio.Queue[EIPTransportFrame | bytes | BaseException] = asyncio.Queue()
        self.closed = False
        self.limits: tuple[int, int, int] | None = None

    async def send(self, frame: EIPTransportFrame) -> None:
        await self.sent.put(frame)

    async def receive(self) -> EIPTransportFrame:
        response = await self.responses.get()
        if isinstance(response, BaseException):
            raise response
        return ControlFrame(response) if isinstance(response, bytes) else response

    async def close(self) -> None:
        self.closed = True

    def set_limits(
        self,
        *,
        max_request_bytes: int,
        max_response_bytes: int,
        max_transfer_frame_bytes: int,
    ) -> None:
        self.limits = (max_request_bytes, max_response_bytes, max_transfer_frame_bytes)


class BlockingCloseTransport(FakeTransport):
    def __init__(self) -> None:
        super().__init__()
        self.close_started = asyncio.Event()
        self.allow_close = asyncio.Event()

    async def close(self) -> None:
        self.close_started.set()
        await self.allow_close.wait()
        self.closed = True


def decode_sent_request(frame: EIPTransportFrame) -> JsonRpcRequest:
    assert isinstance(frame, ControlFrame)
    return decode_model(frame.payload, JsonRpcRequest)


def descriptor(generation: int) -> EnvironmentDescriptor:
    return EnvironmentDescriptor(
        environment_id="env-test",
        generation=generation,
        available_methods=("environment.describe", "environment.readiness", "session.close"),
        limits=EIPLimits(
            max_request_bytes=1024,
            max_response_bytes=1024,
            max_concurrent_operations=4,
            max_processes=1,
            max_operation_duration_ms=1000,
            max_output_preview_bytes=1,
            max_output_bytes_per_stream=1,
            max_transfer_frame_bytes=1024,
            max_concurrent_file_transfers=1,
            max_file_transfer_bytes=1,
        ),
        isolation=IsolationPosture(
            mode=IsolationMode.DISABLED,
            backend=IsolationBackend.OUTER_HOST,
            filesystem_containment=False,
            process_containment=False,
            network_containment=False,
            network_policy=IsolationNetworkPolicy.HOST,
            cleanup_guarantee=IsolationCleanupGuarantee.OUTER_HOST,
        ),
        execution_features=ExecutionFeatures(
            process_count_limit=False,
            memory_bytes_limit=False,
            cpu_time_limit=False,
            per_command_network_deny=False,
            signal_interrupt=False,
            signal_terminate=False,
        ),
    )


def success_response(
    request_id: str | int,
    generation: int,
    descriptor_value: EnvironmentDescriptor | None = None,
) -> bytes:
    result = EnvironmentDescribeResult(descriptor=descriptor_value or descriptor(generation))
    return encode_model(
        JsonRpcSuccessResponse(
            jsonrpc="2.0",
            id=request_id,
            result=json.loads(encode_model(result)),
        )
    )


def initialize_response(
    request_id: str | int,
    generation: int,
    descriptor_value: EnvironmentDescriptor | None = None,
) -> bytes:
    result = InitializeResult(
        protocol_version="0.1",
        server=EIPServerInfo(name="a13n-envd", version="1.0.0"),
        descriptor=descriptor_value or descriptor(generation),
    )
    return encode_model(
        JsonRpcSuccessResponse(
            jsonrpc="2.0",
            id=request_id,
            result=json.loads(encode_model(result)),
        )
    )


def readiness_response(
    request_id: str | int,
    *,
    ready: bool,
    environment_id: str = "env-test",
    generation: int = 1,
) -> bytes:
    result = EnvironmentReadinessResult(
        ready=ready,
        environment_id=environment_id,
        generation=generation,
    )
    return encode_model(
        JsonRpcSuccessResponse(
            jsonrpc="2.0",
            id=request_id,
            result=json.loads(encode_model(result)),
        )
    )


def close_response(request_id: str | int) -> bytes:
    result = SessionCloseResult(closed=True)
    return encode_model(
        JsonRpcSuccessResponse(
            jsonrpc="2.0",
            id=request_id,
            result=json.loads(encode_model(result)),
        )
    )


def test_session_initialize_requires_and_confirms_readiness_before_returning() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        task = asyncio.create_task(
            EIPSession.initialize(
                transport,
                expected_environment_id="env-test",
                required_methods=("environment.describe",),
                initialization_timeout=1,
            )
        )

        initialize_request = decode_sent_request(await transport.sent.get())
        assert initialize_request.method == "initialize"
        assert initialize_request.params["required_methods"] == [
            "environment.describe",
            "environment.readiness",
        ]
        await transport.responses.put(initialize_response(initialize_request.id, 1))

        readiness_request = decode_sent_request(await transport.sent.get())
        assert readiness_request.method == "environment.readiness"
        assert readiness_request.params["context"]["operation_id"].startswith("op-")
        assert 1 <= readiness_request.params["context"]["timeout_ms"] <= 1000
        await transport.responses.put(readiness_response(readiness_request.id, ready=True))

        session = await task
        assert session.generation == 1
        await session.abort()

    asyncio.run(scenario())


def test_session_initialize_rejects_false_or_mismatched_readiness() -> None:
    async def scenario(*, ready: bool, environment_id: str, generation: int) -> None:
        transport = FakeTransport()
        task = asyncio.create_task(
            EIPSession.initialize(
                transport,
                expected_environment_id="env-test",
                initialization_timeout=1,
            )
        )
        initialize_request = decode_sent_request(await transport.sent.get())
        await transport.responses.put(initialize_response(initialize_request.id, 1))
        readiness_request = decode_sent_request(await transport.sent.get())
        await transport.responses.put(
            readiness_response(
                readiness_request.id,
                ready=ready,
                environment_id=environment_id,
                generation=generation,
            )
        )
        expected_error = EIPSessionStateError if not ready else EIPProtocolError
        with pytest.raises(expected_error):
            await task
        assert transport.closed

    asyncio.run(scenario(ready=False, environment_id="env-test", generation=1))
    asyncio.run(scenario(ready=True, environment_id="env-other", generation=1))
    asyncio.run(scenario(ready=True, environment_id="env-test", generation=2))


def test_session_later_readiness_uses_fresh_operations_and_fences_on_false() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        requester = RequestCoordinator(transport, request_timeout=1)
        session = EIPSession(requester, descriptor(1))

        first_task = asyncio.create_task(session.readiness(timeout=1))
        first_request = decode_sent_request(await transport.sent.get())
        await transport.responses.put(readiness_response(first_request.id, ready=True))
        assert (await first_task).ready

        second_task = asyncio.create_task(session.readiness(timeout=1))
        second_request = decode_sent_request(await transport.sent.get())
        assert second_request.params["context"]["operation_id"] != first_request.params["context"]["operation_id"]
        await transport.responses.put(readiness_response(second_request.id, ready=False))
        assert not (await second_task).ready
        assert transport.closed
        with pytest.raises(EIPSessionStateError):
            _ = session.client

    asyncio.run(scenario())


def test_session_later_readiness_enforces_local_timeout_and_fences_session() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        requester = RequestCoordinator(transport, request_timeout=None)
        session = EIPSession(requester, descriptor(1))

        task = asyncio.create_task(session.readiness(timeout=0.01))
        request = decode_sent_request(await transport.sent.get())
        assert request.params["context"]["timeout_ms"] == 10
        with pytest.raises(TimeoutError):
            await task
        assert transport.closed
        with pytest.raises(EIPSessionStateError):
            _ = session.client

    asyncio.run(scenario())


def test_session_later_readiness_cancellation_fences_ambiguous_session_state() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        requester = RequestCoordinator(transport, request_timeout=None)
        session = EIPSession(requester, descriptor(1))

        task = asyncio.create_task(session.readiness(timeout=1))
        await transport.sent.get()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert transport.closed
        with pytest.raises(EIPSessionStateError):
            _ = session.client

    asyncio.run(scenario())


def test_session_concurrent_close_shares_cleanup_and_survives_waiter_cancellation() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        requester = RequestCoordinator(transport, request_timeout=1)
        session = EIPSession(requester, descriptor(1), reuse_transport=True)

        cancelled_waiter = asyncio.create_task(session.close())
        request = decode_sent_request(await transport.sent.get())
        completing_waiter = asyncio.create_task(session.close())
        await asyncio.sleep(0)
        assert transport.sent.empty()

        cancelled_waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled_waiter
        await transport.responses.put(close_response(request.id))
        await completing_waiter

        assert not transport.closed
        await session.close()

    asyncio.run(scenario())


def test_session_abort_is_terminal_and_closes_reusable_transport() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        requester = RequestCoordinator(transport, request_timeout=1)
        session = EIPSession(requester, descriptor(1), reuse_transport=True)

        await session.abort()
        assert transport.closed
        with pytest.raises(EIPSessionStateError, match="did not close cleanly"):
            await session.close()

    asyncio.run(scenario())


def test_request_coordinator_correlates_out_of_order_responses() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        requester = RequestCoordinator(transport, max_in_flight=2, request_timeout=1)
        client = EIPClient(requester)
        first = asyncio.create_task(
            client.environment_describe(EnvironmentDescribeParams(context=EIPCallContext(operation_id="first")))
        )
        second = asyncio.create_task(
            client.environment_describe(EnvironmentDescribeParams(context=EIPCallContext(operation_id="second")))
        )

        first_request = decode_sent_request(await transport.sent.get())
        second_request = decode_sent_request(await transport.sent.get())
        await transport.responses.put(success_response(second_request.id, 2))
        await transport.responses.put(success_response(first_request.id, 1))

        first_result, second_result = await asyncio.gather(first, second)
        assert first_result.descriptor.generation == 1
        assert second_result.descriptor.generation == 2
        await requester.close()

    asyncio.run(scenario())


def test_request_coordinator_detaches_without_closing_a_clean_transport() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        first_requester = RequestCoordinator(transport, request_timeout=1)
        first_client = EIPClient(first_requester)
        first_call = asyncio.create_task(
            first_client.environment_describe(EnvironmentDescribeParams(context=EIPCallContext(operation_id="first")))
        )
        first_request = decode_sent_request(await transport.sent.get())
        await transport.responses.put(success_response(first_request.id, 1))
        assert (await first_call).descriptor.generation == 1

        await first_requester.detach()
        assert not transport.closed

        second_requester = RequestCoordinator(transport, request_timeout=1)
        second_client = EIPClient(second_requester)
        second_call = asyncio.create_task(
            second_client.environment_describe(EnvironmentDescribeParams(context=EIPCallContext(operation_id="second")))
        )
        second_request = decode_sent_request(await transport.sent.get())
        await transport.responses.put(success_response(second_request.id, 2))
        assert (await second_call).descriptor.generation == 2
        await second_requester.close()
        assert transport.closed

    asyncio.run(scenario())


def test_request_coordinator_detach_closes_ambiguous_transport() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        requester = RequestCoordinator(transport, request_timeout=None)
        client = EIPClient(requester)
        call = asyncio.create_task(
            client.environment_describe(EnvironmentDescribeParams(context=EIPCallContext(operation_id="pending")))
        )
        await transport.sent.get()

        with pytest.raises(EIPSessionStateError, match="ambiguous"):
            await requester.detach()
        assert transport.closed
        with pytest.raises(EIPSessionStateError):
            await call

    asyncio.run(scenario())


def test_request_coordinator_raises_typed_method_error() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        requester = RequestCoordinator(transport, request_timeout=1)
        client = EIPClient(requester)
        call = asyncio.create_task(
            client.environment_describe(EnvironmentDescribeParams(context=EIPCallContext(operation_id="describe")))
        )
        request = decode_sent_request(await transport.sent.get())
        error = EIPError(
            code=-32012,
            message="unsupported",
            data=EIPErrorData(
                error_type=ErrorType.UNSUPPORTED,
                retry_hint=RetryHint.NEVER,
                dispatch_stage=DispatchStage.PRE_DISPATCH,
            ),
        )
        await transport.responses.put(encode_model(JsonRpcErrorResponse(jsonrpc="2.0", id=request.id, error=error)))

        with pytest.raises(EIPMethodError) as captured:
            await call
        assert captured.value.error is error or captured.value.error == error
        await requester.close()

    asyncio.run(scenario())


def test_unknown_response_id_terminates_requester() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        requester = RequestCoordinator(transport, request_timeout=1)
        client = EIPClient(requester)
        call = asyncio.create_task(
            client.environment_describe(EnvironmentDescribeParams(context=EIPCallContext(operation_id="describe")))
        )
        await transport.sent.get()
        await transport.responses.put(success_response(999, 1))

        with pytest.raises(EIPProtocolError, match="unknown or duplicate"):
            await call
        assert transport.closed
        await requester.close()

    asyncio.run(scenario())


def test_cancelled_wait_releases_admission_and_discards_late_response() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        requester = RequestCoordinator(transport, max_in_flight=1, request_timeout=None)
        client = EIPClient(requester)
        cancelled = asyncio.create_task(
            client.environment_describe(EnvironmentDescribeParams(context=EIPCallContext(operation_id="cancelled")))
        )
        first_request = decode_sent_request(await transport.sent.get())
        cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled

        next_call = asyncio.create_task(
            client.environment_describe(EnvironmentDescribeParams(context=EIPCallContext(operation_id="next")))
        )
        second_request = decode_sent_request(await transport.sent.get())
        await transport.responses.put(success_response(second_request.id, 2))
        assert (await next_call).descriptor.generation == 2

        await transport.responses.put(success_response(first_request.id, 1))
        await asyncio.sleep(0)
        assert requester._abandoned_ids == set()
        assert requester._terminal_error is None
        await requester.close()

    asyncio.run(scenario())


def test_repeated_abandonment_closes_before_correlation_can_grow() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        requester = RequestCoordinator(transport, max_in_flight=1, request_timeout=None)
        client = EIPClient(requester)

        first = asyncio.create_task(
            client.environment_describe(EnvironmentDescribeParams(context=EIPCallContext(operation_id="first")))
        )
        await transport.sent.get()
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert len(requester._abandoned_ids) == 1

        second = asyncio.create_task(
            client.environment_describe(EnvironmentDescribeParams(context=EIPCallContext(operation_id="second")))
        )
        await transport.sent.get()
        second.cancel()
        with pytest.raises(asyncio.CancelledError):
            await second
        await requester.close()

        assert transport.closed
        assert isinstance(requester._terminal_error, EIPProtocolError)
        assert requester._abandoned_ids == set()

    asyncio.run(scenario())


def test_relative_timeout_covers_waiting_for_admission() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        requester = RequestCoordinator(transport, max_in_flight=1, request_timeout=None)
        client = EIPClient(requester)
        first = asyncio.create_task(
            client.environment_describe(EnvironmentDescribeParams(context=EIPCallContext(operation_id="first")))
        )
        first_request = decode_sent_request(await transport.sent.get())

        with pytest.raises(EIPRequestTimeoutError) as captured:
            await client.environment_describe(
                EnvironmentDescribeParams(context=EIPCallContext(operation_id="timed", timeout_ms=20))
            )
        assert captured.value.dispatched is False
        assert transport.sent.empty()

        await transport.responses.put(success_response(first_request.id, 1))
        assert (await first).descriptor.generation == 1
        await requester.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("delayed_encoding", [1, 2])
def test_request_timeout_expired_during_encoding_is_not_dispatched(
    monkeypatch: pytest.MonkeyPatch,
    delayed_encoding: int,
) -> None:
    original_encode_model = requester_module.encode_model
    encode_count = 0

    def delayed_encode_model(model: Any) -> bytes:
        nonlocal encode_count
        encode_count += 1
        if encode_count == delayed_encoding:
            time.sleep(0.01)
        return original_encode_model(model)

    monkeypatch.setattr(requester_module, "encode_model", delayed_encode_model)

    async def scenario() -> None:
        transport = FakeTransport()
        requester = RequestCoordinator(transport, request_timeout=0.001)
        client = EIPClient(requester)

        with pytest.raises(EIPRequestTimeoutError) as captured:
            await client.environment_describe(EnvironmentDescribeParams(context=EIPCallContext(operation_id="expired")))
        assert captured.value.dispatched is False
        assert transport.sent.empty()
        await requester.close()

    asyncio.run(scenario())


def test_requester_close_finishes_cleanup_before_propagating_repeated_cancellation() -> None:
    async def scenario() -> None:
        transport = BlockingCloseTransport()
        requester = RequestCoordinator(transport)
        close = asyncio.create_task(requester.close())
        await transport.close_started.wait()
        close.cancel()
        await asyncio.sleep(0)
        close.cancel()
        await asyncio.sleep(0)
        assert not close.done()

        transport.allow_close.set()
        with pytest.raises(asyncio.CancelledError):
            await close
        assert transport.closed
        await requester.close()

    asyncio.run(scenario())


def test_descriptor_refresh_narrows_limits_and_identity_violation_is_terminal() -> None:
    async def narrowing() -> None:
        transport = FakeTransport()
        requester = RequestCoordinator(transport, max_in_flight=4, request_timeout=1)
        session = EIPSession(requester, descriptor(1))
        narrowed_limits = descriptor(1).limits.model_copy(
            update={
                "max_request_bytes": 512,
                "max_response_bytes": 512,
                "max_concurrent_operations": 1,
            }
        )
        narrowed = descriptor(1).model_copy(update={"limits": narrowed_limits})
        refresh = asyncio.create_task(session.describe())
        request = decode_sent_request(await transport.sent.get())
        await transport.responses.put(success_response(request.id, 1, narrowed))
        assert (await refresh).limits.max_concurrent_operations == 1
        assert transport.limits == (512, 512, 1024)

        first = asyncio.create_task(
            session.client.environment_describe(
                EnvironmentDescribeParams(context=EIPCallContext(operation_id="after-narrow-1"))
            )
        )
        second = asyncio.create_task(
            session.client.environment_describe(
                EnvironmentDescribeParams(context=EIPCallContext(operation_id="after-narrow-2"))
            )
        )
        first_request = decode_sent_request(await transport.sent.get())
        await asyncio.sleep(0)
        assert transport.sent.empty()
        await transport.responses.put(success_response(first_request.id, 1, narrowed))
        await first
        second_request = decode_sent_request(await transport.sent.get())
        await transport.responses.put(success_response(second_request.id, 1, narrowed))
        await second
        await session.abort()

    async def identity_violation() -> None:
        transport = FakeTransport()
        requester = RequestCoordinator(transport, request_timeout=1)
        session = EIPSession(requester, descriptor(1))
        refresh = asyncio.create_task(session.describe())
        request = decode_sent_request(await transport.sent.get())
        await transport.responses.put(success_response(request.id, 2))
        with pytest.raises(EIPProtocolError, match="generation changed"):
            await refresh
        assert transport.closed
        with pytest.raises(EIPSessionStateError):
            _ = session.client

    async def limit_widening_violation() -> None:
        transport = FakeTransport()
        requester = RequestCoordinator(transport, request_timeout=1)
        session = EIPSession(requester, descriptor(1))
        narrowed_limits = descriptor(1).limits.model_copy(update={"max_request_bytes": 512})
        narrowed = descriptor(1).model_copy(update={"limits": narrowed_limits})
        first_refresh = asyncio.create_task(session.describe())
        first_request = decode_sent_request(await transport.sent.get())
        await transport.responses.put(success_response(first_request.id, 1, narrowed))
        assert (await first_refresh).limits.max_request_bytes == 512

        second_refresh = asyncio.create_task(session.describe())
        second_request = decode_sent_request(await transport.sent.get())
        await transport.responses.put(success_response(second_request.id, 1, descriptor(1)))
        with pytest.raises(EIPProtocolError, match="limits widened"):
            await second_refresh
        assert session.descriptor.limits.max_request_bytes == 512
        assert transport.closed

    async def method_widening_violation() -> None:
        transport = FakeTransport()
        requester = RequestCoordinator(transport, request_timeout=1)
        initial = descriptor(1).model_copy(update={"available_methods": ("environment.describe",)})
        session = EIPSession(requester, initial)
        refresh = asyncio.create_task(session.describe())
        request = decode_sent_request(await transport.sent.get())
        await transport.responses.put(success_response(request.id, 1, descriptor(1)))
        with pytest.raises(EIPProtocolError, match="available methods widened"):
            await refresh
        assert transport.closed

    async def posture_change_violation() -> None:
        transport = FakeTransport()
        requester = RequestCoordinator(transport, request_timeout=1)
        session = EIPSession(requester, descriptor(1))
        changed_isolation = descriptor(1).isolation.model_copy(update={"network_policy": IsolationNetworkPolicy.DENY})
        changed = descriptor(1).model_copy(update={"isolation": changed_isolation})
        refresh = asyncio.create_task(session.describe())
        request = decode_sent_request(await transport.sent.get())
        await transport.responses.put(success_response(request.id, 1, changed))
        with pytest.raises(EIPProtocolError, match="isolation posture changed"):
            await refresh
        assert transport.closed

    asyncio.run(narrowing())
    asyncio.run(identity_violation())
    asyncio.run(limit_widening_violation())
    asyncio.run(method_widening_violation())
    asyncio.run(posture_change_violation())


def test_session_rejects_invalid_local_admission_before_initialize() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        with pytest.raises(ValueError, match="max_in_flight"):
            await EIPSession.initialize(
                transport,
                expected_environment_id="env-test",
                max_in_flight=0,
            )
        assert transport.sent.empty()
        assert not transport.closed

    asyncio.run(scenario())


def test_request_coordinator_demultiplexes_data_without_blocking_control() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        requester = RequestCoordinator(transport, max_in_flight=1, request_timeout=1)
        requester.configure_limits(
            max_in_flight=1,
            max_request_bytes=1024,
            max_response_bytes=1024,
            max_transfer_frame_bytes=1024,
            max_concurrent_file_transfers=1,
        )
        channel = requester.register_transfer("reader-test", inbound_frames=2)
        call = asyncio.create_task(
            EIPClient(requester).environment_describe(
                EnvironmentDescribeParams(context=EIPCallContext(operation_id="interleaved"))
            )
        )
        request = decode_sent_request(await transport.sent.get())

        attached = DataFrame(kind=DataFrameKind.ATTACHED, handle=channel.handle)
        await transport.responses.put(attached)
        await transport.responses.put(success_response(request.id, 7))

        assert await channel.receive() == attached
        assert (await call).descriptor.generation == 7
        requester.unregister_transfer(channel)
        await requester.close()

    asyncio.run(scenario())


def test_slow_transfer_consumer_applies_backpressure_without_reset() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        requester = RequestCoordinator(transport)
        requester.configure_limits(
            max_in_flight=1,
            max_request_bytes=1024,
            max_response_bytes=1024,
            max_transfer_frame_bytes=1024,
            max_concurrent_file_transfers=1,
        )
        channel = requester.register_transfer("reader-full", inbound_frames=1)
        first = DataFrame(kind=DataFrameKind.CHUNK, handle=channel.handle, payload=b"a")
        second = DataFrame(kind=DataFrameKind.CHUNK, handle=channel.handle, offset=1, payload=b"b")
        await transport.responses.put(first)
        await transport.responses.put(second)
        await asyncio.sleep(0)

        assert transport.sent.empty()
        assert await channel.receive() == first
        assert await channel.receive() == second
        assert transport.sent.empty()

        requester.unregister_transfer(channel)
        await requester.close()

    asyncio.run(scenario())


def test_peer_reset_bypasses_a_full_transfer_inbox() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        requester = RequestCoordinator(transport)
        requester.configure_limits(
            max_in_flight=1,
            max_request_bytes=1024,
            max_response_bytes=1024,
            max_transfer_frame_bytes=1024,
            max_concurrent_file_transfers=1,
        )
        channel = requester.register_transfer("reader-reset", inbound_frames=1)
        chunk = DataFrame(kind=DataFrameKind.CHUNK, handle=channel.handle, payload=b"queued")
        reset = DataFrame(
            kind=DataFrameKind.RESET,
            handle=channel.handle,
            reset_status=DataResetStatus.SOURCE,
        )
        await channel.deliver(chunk)
        await asyncio.wait_for(channel.deliver(reset), timeout=1)
        assert channel.peer_reset_received
        channel.fail(RuntimeError("carrier also failed"))
        assert await channel.receive() == chunk
        assert await channel.receive() == reset

        requester.unregister_transfer(channel)
        await requester.close()

    asyncio.run(scenario())


def test_missing_reset_acknowledgement_closes_the_carrier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(requester_module, "_TRANSFER_TEARDOWN_TIMEOUT", 0.01)

    async def scenario() -> None:
        transport = FakeTransport()
        requester = RequestCoordinator(transport)
        requester.configure_limits(
            max_in_flight=1,
            max_request_bytes=1024,
            max_response_bytes=1024,
            max_transfer_frame_bytes=1024,
            max_concurrent_file_transfers=1,
        )
        channel = requester.register_transfer("reader-silent")
        await requester.reset_transfer(
            channel,
            DataFrame(
                kind=DataFrameKind.RESET,
                handle=channel.handle,
                reset_status=DataResetStatus.CANCELLED,
            ),
        )

        await asyncio.sleep(0.02)
        await requester.close()
        assert transport.closed
        assert isinstance(requester._terminal_error, EIPProtocolError)

    asyncio.run(scenario())


def test_retired_transfer_capacity_exhaustion_closes_the_carrier() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        requester = RequestCoordinator(transport)
        requester.configure_limits(
            max_in_flight=1,
            max_request_bytes=1024,
            max_response_bytes=1024,
            max_transfer_frame_bytes=1024,
            max_concurrent_file_transfers=1,
        )
        first = requester.register_transfer("reader-first")
        requester.retire_transfer(first)
        second = requester.register_transfer("reader-second")

        with pytest.raises(EIPTransportClosedError, match="teardown stalled"):
            requester.retire_transfer(second)
        await requester.close()
        assert transport.closed
        assert isinstance(requester._terminal_error, EIPProtocolError)

    asyncio.run(scenario())


def test_retired_transfer_ignores_already_queued_terminal_frames() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        requester = RequestCoordinator(transport)
        requester.configure_limits(
            max_in_flight=1,
            max_request_bytes=1024,
            max_response_bytes=1024,
            max_transfer_frame_bytes=1024,
            max_concurrent_file_transfers=1,
        )
        channel = requester.register_transfer("reader-retired", inbound_frames=1)
        await channel.deliver(DataFrame(kind=DataFrameKind.CHUNK, handle=channel.handle, payload=b"queued"))
        blocked_delivery = asyncio.create_task(
            channel.deliver(
                DataFrame(
                    kind=DataFrameKind.CHUNK,
                    handle=channel.handle,
                    offset=6,
                    payload=b"blocked",
                )
            )
        )
        await asyncio.sleep(0)
        assert not blocked_delivery.done()
        requester.retire_transfer(channel)
        await asyncio.wait_for(blocked_delivery, timeout=1)
        await transport.responses.put(DataFrame(kind=DataFrameKind.CHUNK, handle=channel.handle, payload=b"late"))
        await transport.responses.put(DataFrame(kind=DataFrameKind.END, handle=channel.handle, offset=6))
        await transport.responses.put(DataFrame(kind=DataFrameKind.END_ACK, handle=channel.handle, offset=6))
        await asyncio.sleep(0)
        assert channel.handle in requester._retired_transfers
        assert requester._terminal_error is None
        await transport.responses.put(
            DataFrame(
                kind=DataFrameKind.RESET,
                handle=channel.handle,
                reset_status=DataResetStatus.CANCELLED,
            )
        )
        await asyncio.sleep(0)
        assert channel.handle not in requester._retired_transfers
        assert requester._terminal_error is None
        await requester.close()

    asyncio.run(scenario())
