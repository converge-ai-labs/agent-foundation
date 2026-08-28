from __future__ import annotations

import asyncio
import base64
from types import SimpleNamespace
from typing import Any, cast

import pytest
from a13n_envd_client.eip import v1 as eip
from a13n_envd_client.errors import EIPTransportError
from a13n_harness.environment import (
    ArgvCommand,
    CommandRequest,
    EnvironmentError,
    EnvironmentOutputPolicy,
    FileTextSearchRequest,
    ProcessIdentity,
)
from a13n_harness.environment.eip.binding import _BoundEIPProvider
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


def test_eip_bound_provider_rechecks_live_readiness_on_every_request() -> None:
    async def scenario() -> None:
        descriptor = eip.EnvironmentDescriptor(
            environment_id="env-one",
            generation=1,
            available_methods=("environment.describe", "environment.readiness", "session.close"),
            limits=eip.EIPLimits(
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
            isolation=eip.IsolationPosture(
                mode=eip.IsolationMode.DISABLED,
                backend=eip.IsolationBackend.OUTER_HOST,
                filesystem_containment=False,
                process_containment=False,
                network_containment=False,
                network_policy=eip.IsolationNetworkPolicy.HOST,
                cleanup_guarantee=eip.IsolationCleanupGuarantee.OUTER_HOST,
            ),
            execution_features=eip.ExecutionFeatures(
                process_count_limit=False,
                memory_bytes_limit=False,
                cpu_time_limit=False,
                per_command_network_deny=False,
                signal_interrupt=False,
                signal_terminate=False,
            ),
        )

        class ReadinessSession:
            def __init__(self) -> None:
                self.descriptor = descriptor
                self.calls = 0

            async def readiness(self) -> eip.EnvironmentReadinessResult:
                self.calls += 1
                return eip.EnvironmentReadinessResult(
                    ready=self.calls == 1,
                    environment_id=descriptor.environment_id,
                    generation=descriptor.generation,
                )

        session = ReadinessSession()
        provider = _BoundEIPProvider(
            session=cast(Any, session),
            environment_id="env-one",
            binding_id="binding-one",
            binding_revision=1,
        )

        await provider.ensure_ready(frozenset())
        assert session.calls == 1
        with pytest.raises(EnvironmentError) as exc_info:
            await provider.ensure_ready(frozenset())
        assert exc_info.value.code == "environment_unavailable"
        assert provider.availability.status == "unavailable"
        assert session.calls == 2

    asyncio.run(scenario())


def test_eip_file_search_pushes_down_the_complete_request_and_maps_inline_context() -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.calls: list[eip.FileSearchParams] = []

        async def file_search(self, params: eip.FileSearchParams) -> eip.FileSearchResult:
            self.calls.append(params)
            return eip.FileSearchResult(
                matches=(
                    eip.FileSearchMatch(
                        path=eip.EIPPath(mount_id="workspace", path="/src/app.py"),
                        line_number=3,
                        preview="needle",
                        preview_truncated=True,
                        context="before\nneedle\nafter\n",
                        context_start_line=2,
                    ),
                ),
                offset=4,
                has_more=True,
                omitted_unrepresentable_entries=0,
            )

    client = FakeClient()
    session = SimpleNamespace(
        client=client,
        protocol_version="1.1",
        descriptor=SimpleNamespace(
            mounts=(
                SimpleNamespace(
                    mount_id="workspace",
                    logical_root="/workspace",
                    writable=True,
                ),
            )
        ),
    )
    operator = EIPFileOperator(
        session=cast(Any, session),
        environment_id="env-one",
        binding_id="binding-one",
        binding_revision=1,
        generation="1",
    )
    request = FileTextSearchRequest(
        root="/workspace/src",
        pattern=r"need.le",
        regex=True,
        case_sensitive=False,
        include="**/*.py",
        include_hidden=True,
        ignore_mode="git",
        context_lines=1,
        offset=4,
        max_matches=7,
        max_matches_per_file=2,
        max_files=11,
        max_file_bytes=4_096,
        max_line_length=80,
    )

    result = asyncio.run(operator.search_text(request))

    assert len(client.calls) == 1
    sent = client.calls[0]
    assert sent.root == eip.EIPPath(mount_id="workspace", path="/src")
    assert sent.query == request.pattern
    assert sent.mode is eip.SearchMode.REGEX
    assert sent.case_sensitive is False
    assert sent.include_pattern == "**/*.py"
    assert sent.include_hidden is True
    assert sent.respect_git_ignore is True
    assert sent.context_lines == 1
    assert sent.offset == 4
    assert sent.max_results == 7
    assert sent.max_matches_per_file == 2
    assert sent.max_files == 11
    assert sent.max_file_bytes == 4_096
    assert sent.max_line_length == 80
    assert result.model_dump() == {
        "matches": (
            {
                "path": "/workspace/src/app.py",
                "line": 3,
                "text": "needle",
                "text_truncated": True,
                "context": "before\nneedle\nafter\n",
                "context_start_line": 2,
            },
        ),
        "offset": 4,
        "has_more": True,
    }


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
        provider_type="test.eip",
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


def test_eip_process_rebinds_portable_identity_through_a_fresh_adapter() -> None:
    process = _process_info(content=b"retained")

    class FakeClient:
        def __init__(self) -> None:
            self.handles: list[eip.ProcessHandle] = []

        async def process_inspect(self, params: eip.ProcessInspectParams) -> eip.ProcessInspectResult:
            self.handles.append(params.handle)
            return eip.ProcessInspectResult(process=process)

    session = SimpleNamespace(client=FakeClient())
    conversions = _ProcessConversions(
        session=cast(Any, session),
        files=cast(EIPFileOperator, FakeFiles()),
        outputs=cast(
            Any,
            EIPOutputRegistry(
                session=cast(Any, session),
                environment_id="env-one",
                binding_id="binding-two",
                binding_revision=2,
                generation="1",
            ),
        ),
        provider_type="test.eip",
        environment_id="env-one",
        binding_id="binding-two",
        binding_revision=2,
        generation="1",
    )
    operations = EIPProcessOperations(conversions)
    identity = ProcessIdentity(
        provider_type="test.eip",
        environment_id="env-one",
        generation="1",
        process_id="process-one",
    )

    rebound = asyncio.run(
        operations.rebind(
            identity,
            output_policy=EnvironmentOutputPolicy(
                max_inline_bytes=4,
                max_output_bytes=16,
                overflow="retain",
            ),
        )
    )

    assert session.client.handles == [eip.ProcessHandle("process-one")]
    assert rebound.handle.identity == identity
    assert rebound.handle.binding_id == "binding-two"
    assert rebound.handle.binding_revision == 2


def test_eip_process_read_materializes_terminal_inline_output_without_a_reference() -> None:
    process = _process_info(content=b"done")

    class FakeClient:
        async def process_inspect(self, params: eip.ProcessInspectParams) -> eip.ProcessInspectResult:
            assert params.handle == process.handle
            return eip.ProcessInspectResult(process=process)

    session = SimpleNamespace(client=FakeClient())
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
        provider_type="test.eip",
        environment_id="env-one",
        binding_id="binding-one",
        binding_revision=1,
        generation="1",
    )
    bound = conversions.register(
        process,
        EnvironmentOutputPolicy(max_inline_bytes=8, max_output_bytes=8, overflow="retain"),
    )

    result = asyncio.run(
        EIPProcessOperations(conversions).read_output(
            bound.handle,
            stdout_start_offset=0,
            stderr_start_offset=0,
            policy=EnvironmentOutputPolicy(max_inline_bytes=8, max_output_bytes=8, overflow="truncate"),
        )
    )

    assert result.stdout.capture.reference is None
    assert [(chunk.start_offset, chunk.data) for chunk in result.stdout.chunks] == [(0, b"done")]
    assert [(chunk.start_offset, chunk.data) for chunk in result.stderr.chunks] == [(0, b"done")]


def test_eip_public_output_release_is_retried_during_provider_cleanup() -> None:
    output = _process_info(content=b"retained").output.stdout

    class FakeClient:
        def __init__(self) -> None:
            self.failures = 1
            self.calls = 0

        async def output_release(self, params: eip.OutputReleaseParams) -> eip.OutputReleaseResult:
            assert params.reference == output.reference
            self.calls += 1
            if self.failures:
                self.failures -= 1
                raise EIPTransportError("temporary release failure")
            return eip.OutputReleaseResult(
                released=True,
                receipt=_receipt(method="output.release"),
            )

    session = SimpleNamespace(client=FakeClient())
    outputs = EIPOutputRegistry(
        session=cast(Any, session),
        environment_id="env-one",
        binding_id="binding-one",
        binding_revision=1,
        generation="1",
    )
    capture = outputs.capture(
        output,
        policy=EnvironmentOutputPolicy(max_inline_bytes=4, max_output_bytes=16, overflow="retain"),
    )
    assert capture.reference is not None

    async def scenario() -> None:
        with pytest.raises(EnvironmentError):
            await outputs.release(reference=capture.reference)
        assert output.reference in outputs._pending_cleanup

        await outputs.cleanup_pending()

        assert output.reference not in outputs._pending_cleanup
        assert session.client.calls == 2

    asyncio.run(scenario())


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
        provider_type="test.eip",
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
        provider_type="test.eip",
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
