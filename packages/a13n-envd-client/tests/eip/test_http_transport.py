from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

import pytest
from a13n_envd_client import (
    ControlFrame,
    EIPConnectionError,
    EIPMethodError,
    EIPRequestTimeoutError,
    EIPTransportError,
    HttpTransport,
    RequestCoordinator,
)
from a13n_envd_client.eip.v1 import EIPCallContext, EIPClient, EIPLimits, ErrorType, ProcessWaitParams


def session_requester(device):
    limits = EIPLimits(
        max_request_bytes=65536,
        max_response_bytes=65536,
        max_concurrent_operations=4,
        max_processes=1,
        max_operation_duration_ms=1000,
        max_output_preview_bytes=1,
        max_output_bytes_per_stream=1,
        max_transfer_frame_bytes=65536,
        max_concurrent_file_transfers=2,
        max_file_transfer_bytes=65536,
        max_file_bytes=65536,
    )
    return device.session("ses-test", limits=limits)


@asynccontextmanager
async def control_server(*, delay=0, status=200, disconnect=False):
    tasks = set()
    received = []

    async def handle(reader, writer):
        tasks.add(asyncio.current_task())
        try:
            headers = await reader.readuntil(b"\r\n\r\n")
            length = next(
                int(line.split(b":", 1)[1])
                for line in headers.split(b"\r\n")
                if line.lower().startswith(b"content-length:")
            )
            request = json.loads(await reader.readexactly(length))
            received.append(request)
            if disconnect:
                return
            await asyncio.sleep(delay)
            body = json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": request["id"],
                    "eip_session": request.get("eip_session"),
                    "error": {
                        "code": -32040,
                        "message": "process wait expired",
                        "data": {
                            "error_type": "timeout",
                            "retry_hint": "never",
                            "dispatch_stage": "pre_dispatch",
                        },
                    },
                }
            ).encode()
            writer.write(
                f"HTTP/1.1 {status} Test\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n".encode()
                + body
            )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
            tasks.discard(asyncio.current_task())

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    try:
        yield f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}", received
    finally:
        server.close()
        await server.wait_closed()
        if tasks:
            await asyncio.gather(*tasks)


@pytest.mark.parametrize(
    ("operation_ms", "allowance", "delay"),
    [(50, 0.3, 0.1), (300, 0.2, 0.35)],
)
def test_http_process_wait_receives_remote_timeout_with_response_allowance(operation_ms, allowance, delay):
    async def scenario():
        async with control_server(delay=delay) as (endpoint, received):
            device = RequestCoordinator(
                HttpTransport(endpoint, "test-token", request_timeout=allowance), request_timeout=allowance
            )
            requester = session_requester(device)
            try:
                with pytest.raises(EIPMethodError) as captured:
                    await EIPClient(requester).process_wait(
                        ProcessWaitParams(
                            context=EIPCallContext(operation_id="op-wait", timeout_ms=operation_ms),
                            handle="proc-test",
                            condition="initial_terminal",
                        )
                    )
                assert captured.value.error.data.error_type == ErrorType.TIMEOUT
                assert received[0]["params"]["context"]["timeout_ms"] == operation_ms
                assert len(received) == 1
            finally:
                await device.close()

    asyncio.run(scenario())


def test_http_unresponsive_wait_still_has_a_finite_client_deadline():
    async def scenario():
        async with control_server(delay=0.3) as (endpoint, received):
            device = RequestCoordinator(
                HttpTransport(endpoint, "test-token", request_timeout=0.05), request_timeout=0.05
            )
            requester = session_requester(device)
            try:
                with pytest.raises(EIPRequestTimeoutError) as captured:
                    await EIPClient(requester).process_wait(
                        ProcessWaitParams(
                            context=EIPCallContext(operation_id="op-wait", timeout_ms=50),
                            handle="proc-test",
                            condition="initial_terminal",
                        )
                    )
                assert captured.value.dispatched
                assert len(received) == 1
            finally:
                await device.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("status,disconnect", [(401, False), (200, True)])
def test_http_distinguishes_connection_loss_from_authentication_failure(status, disconnect):
    async def scenario():
        async with control_server(status=status, disconnect=disconnect) as (endpoint, received):
            transport = HttpTransport(endpoint, "test-token")
            try:
                with pytest.raises(EIPTransportError) as captured:
                    await transport.send(ControlFrame(b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}'))
                assert isinstance(captured.value, EIPConnectionError) is disconnect
                assert len(received) == 1
            finally:
                await transport.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("direction", ["read", "write"])
@pytest.mark.parametrize("failure", ["response", "connection"])
def test_failed_http_transfer_preserves_control_and_other_transfers(direction, failure):
    import httpx2
    from a13n_envd_client.eip.v1 import DataFrame, DataFrameKind
    from a13n_envd_client.errors import EIPTransferError

    async def scenario():
        async def respond(request):
            if request.url.path == "/eip/transfer":
                if failure == "connection":
                    raise httpx2.ReadError("Injected transfer connection loss")
                return httpx2.Response(500, headers={"Content-Type": "application/octet-stream"})
            message = json.loads(request.content)
            return httpx2.Response(
                200,
                headers={"Content-Type": "application/json"},
                json={
                    "jsonrpc": "2.0",
                    "id": message["id"],
                    "eip_session": message.get("eip_session"),
                    "error": {
                        "code": -32040,
                        "message": "process wait expired",
                        "data": {"error_type": "timeout", "retry_hint": "never", "dispatch_stage": "pre_dispatch"},
                    },
                },
            )

        transport = HttpTransport("http://127.0.0.1", "test-token")
        await transport._client.aclose()
        transport._client = httpx2.AsyncClient(transport=httpx2.MockTransport(respond))
        device = RequestCoordinator(transport)
        requester = session_requester(device)
        failed = requester.register_transfer("writer-failed", direction=direction)
        unrelated = requester.register_transfer("reader-unrelated")
        try:
            await requester.send_data_frame(
                failed, DataFrame(session_id="ses-test", kind=DataFrameKind.ATTACH, handle=failed.handle)
            )
            if direction == "write":
                assert (await failed.receive()).kind is DataFrameKind.ATTACHED
                try:
                    await requester.send_data_frame(
                        failed, DataFrame(session_id="ses-test", kind=DataFrameKind.END, handle=failed.handle)
                    )
                except EIPTransferError:
                    # The HTTP response may win the race with accepting END.
                    pass
            with pytest.raises(EIPTransferError) as captured:
                await asyncio.wait_for(failed.receive(), 1)
            assert captured.value.offset is None, "HTTP failure must not fabricate an acknowledged offset"
            if direction == "write":
                with pytest.raises(EIPTransferError):
                    await requester.send_data_frame(
                        failed, DataFrame(session_id="ses-test", kind=DataFrameKind.END, handle=failed.handle)
                    )
            with pytest.raises(EIPMethodError) as captured:
                await EIPClient(requester).process_wait(
                    ProcessWaitParams(
                        context=EIPCallContext(operation_id="op-after-transfer"),
                        handle="proc-test",
                        condition="initial_terminal",
                    )
                )
            assert captured.value.error.data.error_type == ErrorType.TIMEOUT
            await transport._received.put(
                DataFrame(session_id="ses-test", kind=DataFrameKind.ATTACHED, handle=unrelated.handle)
            )
            assert (await asyncio.wait_for(unrelated.receive(), 1)).kind is DataFrameKind.ATTACHED
        finally:
            await device.close()

    asyncio.run(scenario())
