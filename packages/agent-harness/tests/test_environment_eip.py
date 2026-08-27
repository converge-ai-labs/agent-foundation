from __future__ import annotations

import asyncio
import base64
from typing import Any, cast

import pytest
from a13n_envd_client.eip import v1 as eip
from a13n_envd_client.errors import EIPTransportError
from a13n_harness.environment import (
    ArgvCommand,
    CommandRequest,
    EnvironmentError,
    EnvironmentOutputPolicy,
)
from a13n_harness.environment.eip.files import EIPFileOperator
from a13n_harness.environment.eip.output import EIPOutputRegistry
from a13n_harness.environment.eip.processes import (
    EIPProcessOperations,
    _ProcessConversions,
    convert_command_request,
)


class FakeFiles:
    def to_eip_path(self, path: str) -> eip.EIPPath:
        return eip.EIPPath(mount_id="workspace", path=path)


class FakePageReader:
    def __init__(self, content: bytes, output: eip.OutputInfo, start_offset: int) -> None:
        self._content = content
        self._output = output
        self._start_offset = start_offset

    async def read_page(self, *, wait_ms: int = 0):
        del wait_ms
        data = self._content[self._start_offset :]
        return type(
            "Page",
            (),
            {
                "start_offset": self._start_offset,
                "next_offset": len(self._content),
                "data": data,
                "output": self._output,
                "eof": True,
            },
        )()


class FakeSession:
    def __init__(self, content: bytes, output: eip.OutputInfo) -> None:
        self.content = content
        self.output = output
        self.starts: list[int] = []

    def open_output(
        self,
        reference: eip.OutputReference,
        *,
        start_offset: int = 0,
        observed: eip.OutputInfo | None = None,
    ) -> FakePageReader:
        assert reference == self.output.reference
        assert observed is not None
        self.starts.append(start_offset)
        return FakePageReader(self.content, self.output, start_offset)


def encoded(value: bytes) -> eip.EncodedBytes:
    return eip.EncodedBytes(
        encoding="base64",
        data=base64.b64encode(value).decode().rstrip("="),
    )


def test_eip_command_conversion_never_serializes_harness_output_policy() -> None:
    converted: list[eip.CommandRequest] = []
    for overflow in ("fail", "truncate", "retain"):
        request = CommandRequest(
            command=ArgvCommand(executable="python", arguments=("-V",)),
            output_policy=EnvironmentOutputPolicy(
                max_inline_bytes=4,
                max_output_bytes=8,
                overflow=overflow,
            ),
        )
        converted.append(
            convert_command_request(
                request,
                files=cast(EIPFileOperator, FakeFiles()),
            )
        )

    payloads = [request.model_dump(mode="json", exclude_none=True) for request in converted]
    assert payloads[0] == payloads[1] == payloads[2]
    assert all("output_policy" not in payload for payload in payloads)


def test_eip_output_cursor_cannot_widen_its_cumulative_projection_ceiling() -> None:
    async def scenario() -> None:
        content = b"abcdefghijklmnopqrst"
        raw = eip.OutputInfo(
            reference=eip.OutputReference("output-one"),
            producer_complete=True,
            content_complete=True,
            produced_bytes=len(content),
            retained_bytes=len(content),
            preview=encoded(content[:4]),
        )
        session = FakeSession(content, raw)
        registry = EIPOutputRegistry(
            session=cast(Any, session),
            environment_id="env-one",
            binding_id="binding-one",
            binding_revision=1,
            generation="1",
        )
        origin_policy = EnvironmentOutputPolicy(
            max_inline_bytes=4,
            max_output_bytes=8,
            overflow="retain",
        )
        capture = registry.capture(raw, policy=origin_policy)
        assert capture.reference is not None
        registry.capture(
            raw,
            policy=EnvironmentOutputPolicy(
                max_inline_bytes=2,
                max_output_bytes=2,
                overflow="truncate",
            ),
        )

        first = await registry.read(capture.reference, policy=origin_policy)
        assert first.chunks[0].data == b"abcd"
        assert first.next_cursor is not None

        wider = EnvironmentOutputPolicy(
            max_inline_bytes=16,
            max_output_bytes=20,
            overflow="retain",
        )
        second = await registry.read(
            capture.reference,
            cursor=first.next_cursor,
            policy=wider,
        )
        assert second.chunks[0].data == b"efgh"
        assert second.next_cursor is None
        assert session.starts == [0, 4]
        assert second.capture.available_end == len(content)

    asyncio.run(scenario())


def test_eip_fail_projection_is_local_and_does_not_change_raw_output() -> None:
    content = b"0123456789"
    raw = eip.OutputInfo(
        reference=eip.OutputReference("output-two"),
        producer_complete=True,
        content_complete=True,
        produced_bytes=len(content),
        retained_bytes=len(content),
        preview=encoded(content[:4]),
    )
    registry = EIPOutputRegistry(
        session=cast(Any, FakeSession(content, raw)),
        environment_id="env-one",
        binding_id="binding-one",
        binding_revision=1,
        generation="1",
    )

    with pytest.raises(Exception, match="model projection limit"):
        registry.capture(
            raw,
            policy=EnvironmentOutputPolicy(
                max_inline_bytes=4,
                max_output_bytes=8,
                overflow="fail",
            ),
        )
    assert raw.content_complete
    assert raw.retained_bytes == len(content)


def test_eip_truncate_projection_reports_raw_counts_without_a_reference() -> None:
    content = b"0123456789"
    raw = eip.OutputInfo(
        reference=eip.OutputReference("output-three"),
        producer_complete=True,
        content_complete=True,
        produced_bytes=len(content),
        retained_bytes=len(content),
        preview=encoded(content[:4]),
    )
    registry = EIPOutputRegistry(
        session=cast(Any, FakeSession(content, raw)),
        environment_id="env-one",
        binding_id="binding-one",
        binding_revision=1,
        generation="1",
    )
    capture = registry.capture(
        raw,
        policy=EnvironmentOutputPolicy(
            max_inline_bytes=4,
            max_output_bytes=8,
            overflow="truncate",
        ),
    )
    assert capture.kind == "truncated"
    assert capture.inline == content[:4]
    assert capture.reference is None
    assert capture.captured_bytes == len(content)
    assert capture.dropped_bytes == 0
    assert capture.available_end == len(content)


def _receipt(
    *,
    environment_id: str = "env-one",
    method: str = "process.release",
) -> eip.OperationReceipt:
    return eip.OperationReceipt(
        operation_id="op-release",
        method=method,
        environment_id=environment_id,
        generation=1,
        request_digest="0" * 64,
        stage=eip.ReceiptStage.COMPLETED,
        outcome=eip.ReceiptOutcome.SUCCEEDED,
        observed_at="2026-08-24T00:00:00Z",
    )


def _process_info(
    *,
    environment_id: str = "env-one",
    content: bytes = b"",
) -> eip.ProcessInfo:
    stdout = eip.OutputInfo(
        reference=eip.OutputReference("output-stdout"),
        producer_complete=True,
        content_complete=True,
        produced_bytes=len(content),
        retained_bytes=len(content),
        preview=encoded(content[:4]),
    )
    return eip.ProcessInfo(
        handle=eip.ProcessHandle("process-one"),
        environment_id=environment_id,
        generation=1,
        status=eip.ProcessStatus(
            phase=eip.ProcessPhase.EXITED,
            cleanup=eip.CleanupOutcome.COMPLETE,
        ),
        stdin_open=False,
        output=eip.ProcessOutput(
            stdout=stdout,
            stderr=stdout.model_copy(update={"reference": eip.OutputReference("output-stderr")}),
        ),
    )


def test_eip_process_registration_validates_identity_before_local_mutation() -> None:
    conversions = _ProcessConversions(
        session=cast(Any, object()),
        files=cast(EIPFileOperator, FakeFiles()),
        outputs=cast(Any, object()),
        environment_id="env-one",
        binding_id="binding-one",
        binding_revision=1,
        generation="1",
    )

    with pytest.raises(EnvironmentError, match="identity is stale"):
        conversions.register(
            _process_info(environment_id="env-other"),
            EnvironmentOutputPolicy(max_inline_bytes=4, max_output_bytes=8, overflow="truncate"),
        )
    assert conversions._records == {}
    assert conversions._raw_tokens == {}


def test_eip_process_release_retries_only_remaining_hidden_output_cleanup() -> None:
    class FakeProcessClient:
        def __init__(self) -> None:
            self.process_release_calls = 0
            self.output_release_calls: list[eip.OutputReference] = []
            self.output_failures = 1

        async def process_release(self, params: eip.ProcessReleaseParams) -> eip.ProcessReleaseResult:
            del params
            self.process_release_calls += 1
            return eip.ProcessReleaseResult(released=True, receipt=_receipt())

        async def output_release(self, params: eip.OutputReleaseParams) -> eip.OutputReleaseResult:
            self.output_release_calls.append(params.reference)
            if self.output_failures:
                self.output_failures -= 1
                raise EIPTransportError("cleanup failed")
            return eip.OutputReleaseResult(
                released=True,
                receipt=_receipt(method="output.release"),
            )

    class FakeProcessSession:
        def __init__(self) -> None:
            self.client = FakeProcessClient()

    session = FakeProcessSession()
    outputs = EIPOutputRegistry(
        session=cast(Any, session),
        environment_id="env-one",
        binding_id="binding-one",
        binding_revision=1,
        generation="1",
    )
    conversions = _ProcessConversions(
        session=cast(Any, session),
        files=cast(EIPFileOperator, FakeFiles()),
        outputs=outputs,
        environment_id="env-one",
        binding_id="binding-one",
        binding_revision=1,
        generation="1",
    )
    process = conversions.register(
        _process_info(),
        EnvironmentOutputPolicy(max_inline_bytes=4, max_output_bytes=8, overflow="truncate"),
    )
    operations = EIPProcessOperations(conversions)

    async def scenario() -> None:
        with pytest.raises(EnvironmentError, match="unavailable"):
            await operations.release(process.handle)
        assert session.client.process_release_calls == 1
        assert len(conversions._records) == 1

        receipt = await operations.release(process.handle)
        assert receipt.operation_id == "op-release"
        assert session.client.process_release_calls == 1
        assert session.client.output_release_calls == [
            eip.OutputReference("output-stdout"),
            eip.OutputReference("output-stdout"),
            eip.OutputReference("output-stderr"),
        ]
        assert conversions._records == {}

    asyncio.run(scenario())


def test_eip_process_start_cleans_up_when_local_projection_fails() -> None:
    process = _process_info(content=b"0123456789")

    class FakeClient:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def process_start(self, params: eip.ProcessStartParams) -> eip.ProcessStartResult:
            del params
            self.calls.append("process.start")
            return eip.ProcessStartResult(process=process, receipt=_receipt(method="process.start"))

        async def process_kill(self, params: eip.ProcessKillParams) -> eip.ProcessKillResult:
            del params
            self.calls.append("process.kill")
            return eip.ProcessKillResult(process=process, receipt=_receipt(method="process.kill"))

        async def process_release(self, params: eip.ProcessReleaseParams) -> eip.ProcessReleaseResult:
            del params
            self.calls.append("process.release")
            return eip.ProcessReleaseResult(released=True, receipt=_receipt())

        async def output_release(self, params: eip.OutputReleaseParams) -> eip.OutputReleaseResult:
            self.calls.append(f"output.release:{params.reference.root}")
            return eip.OutputReleaseResult(
                released=True,
                receipt=_receipt(method="output.release"),
            )

    class FakeProcessSession:
        def __init__(self) -> None:
            self.client = FakeClient()

    session = FakeProcessSession()
    outputs = EIPOutputRegistry(
        session=cast(Any, session),
        environment_id="env-one",
        binding_id="binding-one",
        binding_revision=1,
        generation="1",
    )
    conversions = _ProcessConversions(
        session=cast(Any, session),
        files=cast(EIPFileOperator, FakeFiles()),
        outputs=outputs,
        environment_id="env-one",
        binding_id="binding-one",
        binding_revision=1,
        generation="1",
    )
    operations = EIPProcessOperations(conversions)
    request = CommandRequest(
        command=ArgvCommand(executable="python", arguments=("-V",)),
        output_policy=EnvironmentOutputPolicy(
            max_inline_bytes=4,
            max_output_bytes=8,
            overflow="fail",
        ),
    )

    async def scenario() -> None:
        with pytest.raises(EnvironmentError, match="projection limit"):
            await operations.start(request)
        assert session.client.calls == [
            "process.start",
            "process.kill",
            "process.release",
            "output.release:output-stdout",
            "output.release:output-stderr",
        ]
        assert conversions._records == {}
        assert outputs._records == {}

    asyncio.run(scenario())
