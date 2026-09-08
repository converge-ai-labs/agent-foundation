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
from a13n_envd_client.eip.v1 import EIPCallContext, EIPClient, ErrorType, ProcessWaitParams


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
            requester = RequestCoordinator(
                HttpTransport(endpoint, "test-token", request_timeout=allowance), request_timeout=allowance
            )
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
                await requester.close()

    asyncio.run(scenario())


def test_http_unresponsive_wait_still_has_a_finite_client_deadline():
    async def scenario():
        async with control_server(delay=0.3) as (endpoint, received):
            requester = RequestCoordinator(
                HttpTransport(endpoint, "test-token", request_timeout=0.05), request_timeout=0.05
            )
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
                await requester.close()

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
