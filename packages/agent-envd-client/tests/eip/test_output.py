from __future__ import annotations

import asyncio
import base64
from typing import Any

import pytest
from a13n_envd_client import EIPOutputReader
from a13n_envd_client.eip.v1 import (
    EIPClient,
    EncodedBytes,
    MethodSpec,
    OutputInfo,
    OutputReadParams,
    OutputReadResult,
    OutputReference,
)
from a13n_envd_client.errors import EIPProtocolError, EIPTransportClosedError


class FakeRequester:
    def __init__(self, results: list[OutputReadResult | BaseException]) -> None:
        self.results = results
        self.params: list[OutputReadParams] = []
        self.protocol_errors: list[EIPProtocolError] = []

    async def request[P, R](self, method: MethodSpec[P, R], params: P) -> R:
        assert method.name == "output.read"
        assert isinstance(params, OutputReadParams)
        self.params.append(params)
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result  # type: ignore[return-value]

    async def close_for_protocol_error(self, error: EIPProtocolError) -> None:
        self.protocol_errors.append(error)


def encoded(value: bytes) -> EncodedBytes:
    return EncodedBytes(
        encoding="base64",
        data=base64.b64encode(value).decode().rstrip("="),
    )


def output_info(
    reference: OutputReference,
    content: bytes,
    *,
    producer_complete: bool,
    content_complete: bool,
    produced_bytes: int | None = None,
) -> OutputInfo:
    produced = len(content) if produced_bytes is None else produced_bytes
    return OutputInfo(
        reference=reference,
        producer_complete=producer_complete,
        content_complete=content_complete,
        produced_bytes=produced,
        retained_bytes=len(content),
        preview=encoded(content[:4]),
    )


def result(
    reference: OutputReference,
    *,
    start_offset: int,
    data: bytes,
    retained: bytes,
    producer_complete: bool,
    content_complete: bool,
) -> OutputReadResult:
    return OutputReadResult(
        start_offset=start_offset,
        next_offset=start_offset + len(data),
        data=encoded(data),
        output=output_info(
            reference,
            retained,
            producer_complete=producer_complete,
            content_complete=content_complete,
        ),
    )


def reader(fake: FakeRequester, reference: OutputReference) -> EIPOutputReader:
    return EIPOutputReader(fake, EIPClient(fake), reference)


def test_output_reader_pages_contiguously_with_fresh_operation_ids() -> None:
    async def scenario() -> None:
        reference = OutputReference("output-one")
        fake = FakeRequester(
            [
                result(
                    reference,
                    start_offset=0,
                    data=b"ab",
                    retained=b"ab",
                    producer_complete=False,
                    content_complete=False,
                ),
                result(
                    reference,
                    start_offset=2,
                    data=b"cd",
                    retained=b"abcd",
                    producer_complete=True,
                    content_complete=True,
                ),
            ]
        )
        output = reader(fake, reference)

        first = await output.read_page(wait_ms=7)
        second = await output.read_page(wait_ms=9)
        terminal = await output.read_page()

        assert first.data == b"ab" and not first.eof
        assert second.data == b"cd" and second.eof
        assert terminal.data == b"" and terminal.eof
        assert output.offset == 4
        assert [params.start_offset for params in fake.params] == [0, 2]
        assert [params.wait_ms for params in fake.params] == [7, 9]
        operation_ids = [params.context.operation_id for params in fake.params]
        assert operation_ids[0] != operation_ids[1]
        assert all(operation_id.startswith("op-") for operation_id in operation_ids)

    asyncio.run(scenario())


def test_output_reader_iterator_waits_through_live_empty_page() -> None:
    async def scenario() -> None:
        reference = OutputReference("output-two")
        fake = FakeRequester(
            [
                result(
                    reference,
                    start_offset=0,
                    data=b"",
                    retained=b"",
                    producer_complete=False,
                    content_complete=False,
                ),
                result(
                    reference,
                    start_offset=0,
                    data=b"done",
                    retained=b"done",
                    producer_complete=True,
                    content_complete=True,
                ),
            ]
        )

        assert b"".join([chunk async for chunk in reader(fake, reference)]) == b"done"
        assert len(fake.params) == 2
        assert all(params.wait_ms == 1_000 for params in fake.params)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("malformed", "message"),
    [
        (
            lambda reference: OutputReadResult(
                start_offset=1,
                next_offset=2,
                data=encoded(b"x"),
                output=output_info(
                    reference,
                    b"xx",
                    producer_complete=False,
                    content_complete=False,
                ),
            ),
            "non-contiguous start offset",
        ),
        (
            lambda reference: OutputReadResult(
                start_offset=0,
                next_offset=2,
                data=encoded(b"x"),
                output=output_info(
                    reference,
                    b"xx",
                    producer_complete=False,
                    content_complete=False,
                ),
            ),
            "next offset",
        ),
        (
            lambda reference: OutputReadResult(
                start_offset=0,
                next_offset=0,
                data=encoded(b""),
                output=output_info(
                    reference,
                    b"x",
                    producer_complete=True,
                    content_complete=True,
                ),
            ),
            "made no progress",
        ),
    ],
)
def test_output_reader_closes_requester_for_protocol_violation(
    malformed: Any,
    message: str,
) -> None:
    async def scenario() -> None:
        reference = OutputReference("output-three")
        fake = FakeRequester([malformed(reference)])

        with pytest.raises(EIPProtocolError, match=message):
            await reader(fake, reference).read_page()
        assert len(fake.protocol_errors) == 1

    asyncio.run(scenario())


def test_output_reader_propagates_carrier_loss_without_eof() -> None:
    async def scenario() -> None:
        reference = OutputReference("output-four")
        transport_error = EIPTransportClosedError("carrier lost")
        fake = FakeRequester([transport_error])
        output = reader(fake, reference)

        with pytest.raises(EIPTransportClosedError, match="carrier lost"):
            await output.read_page(wait_ms=10)
        assert not output.eof
        assert output.offset == 0
        assert fake.protocol_errors == []

    asyncio.run(scenario())


def test_output_reader_validates_monotonic_terminal_evidence() -> None:
    async def scenario() -> None:
        reference = OutputReference("output-five")
        initial = output_info(
            reference,
            b"prefix",
            producer_complete=True,
            content_complete=True,
        )
        changed = output_info(
            reference,
            b"prefix-more",
            producer_complete=True,
            content_complete=True,
        )
        fake = FakeRequester(
            [
                OutputReadResult(
                    start_offset=0,
                    next_offset=1,
                    data=encoded(b"p"),
                    output=changed,
                )
            ]
        )
        output = EIPOutputReader(fake, EIPClient(fake), reference, observed=initial)

        with pytest.raises(EIPProtocolError, match="terminal output counters changed"):
            await output.read_page()
        assert len(fake.protocol_errors) == 1

    asyncio.run(scenario())
