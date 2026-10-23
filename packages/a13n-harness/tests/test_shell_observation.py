"""Portable output retains original byte offsets across encoding and bounded pages."""

import pytest
from a13n_harness.environment.commands import ProcessIdentity, ProcessStatus, ProcessStreamRead
from a13n_harness.environment.retention import EnvironmentOutputCapture, EnvironmentOutputSegment
from a13n_harness.toolsets.output import tool_output_size
from a13n_harness.toolsets.shell_control import ShellControl, ShellTarget
from a13n_harness.toolsets.shell_observation import ShellObservation, ShellStream


def _stream(data, start=0, **evidence):
    return ShellStream.from_read(
        ProcessStreamRead(
            chunks=(EnvironmentOutputSegment(start_offset=start, data=data),),
            next_cursor=None,
            capture=EnvironmentOutputCapture(
                kind="inline",
                captured_bytes=len(data),
                available_start=start,
                available_end=start + len(data),
                producer_complete=False,
                content_complete=False,
                **evidence,
            ),
        )
    )


def _observation(stdout, stderr=b""):
    return ShellObservation(
        target=ShellTarget(
            resource_id="process-test",
            backing_identity="backing",
            identity=ProcessIdentity(
                provider_type="local",
                environment_id="env-test",
                generation="one",
                process_id="native",
            ),
        ),
        status=ProcessStatus(phase="running"),
        stdin_open=True,
        stdout=stdout,
        stderr=_stream(stderr),
    )


@pytest.mark.parametrize("data", [b"ascii" * 20000, "你好世界".encode() * 10000, b"a\xffb\x00" * 20000])
def test_observation_roundtrip_and_independent_nonconsuming_pages(data):
    original = _observation(_stream(data), b"stderr" * 20000)
    observed = ShellObservation.model_validate_json(original.model_dump_json())
    assert observed == original
    assert observed.stdout.data == data
    offsets = {"stdout": 0, "stderr": 0}
    pages = {"stdout": [], "stderr": []}
    while offsets["stdout"] < len(data) or offsets["stderr"] < 120000:
        control = ShellControl(operation="wait", stdout_offset=offsets["stdout"], stderr_offset=offsets["stderr"])
        page = observed.read("exec-test", control)
        assert page == observed.read("exec-test", control)
        assert tool_output_size(page) <= 10500
        for name, raw in (("stdout", data), ("stderr", b"stderr" * 20000)):
            value = page[name]
            assert value["requested_offset"] == offsets[name]
            assert value["text"] == raw[value["start_offset"] : value["next_offset"]].decode("utf-8", errors="replace")
            assert not value["content_complete"]
            pages[name].append(value["text"])
            offsets[name] = value["next_offset"]
    assert "".join(pages["stdout"]) == data.decode("utf-8", errors="replace")
    assert "".join(pages["stderr"]) == "stderr" * 20000
    assert offsets == {"stdout": len(data), "stderr": 120000}


def test_observation_preserves_provider_gaps_and_unknown_counts():
    stream = _stream(b"tail", start=400, origin="sdk_text", coverage="partial", reason="reattached")
    observation = _observation(stream)
    page = observation.read("exec-test", ShellControl(operation="wait"))["stdout"]
    assert page["requested_offset"] == 0
    assert page["start_offset"] == 400 and page["next_offset"] == 404
    assert page["omitted_before_bytes"] == 400
    assert page["available_start"] == 400 and page["available_end"] == 404
    assert page["origin"] == "sdk_text" and page["coverage"] == "partial"
    assert page["produced_bytes"] is None and page["reason"] == "reattached"
    assert observation.read("exec-test", ShellControl(operation="wait", stdout_offset=405)) is None


def test_observation_prefix_miss_is_not_a_new_retention_boundary():
    stream = _stream(b"prefix")
    stream = stream.model_copy(update={"capture": stream.capture.model_copy(update={"available_end": 100})})
    observation = _observation(stream)
    first = observation.read("exec-test", ShellControl(operation="wait"))["stdout"]
    assert first["available_end"] == 100 and first["next_offset"] == 6
    assert observation.read("exec-test", ShellControl(operation="wait", stdout_offset=6)) is None
    assert observation.read("exec-test", ShellControl(operation="wait", stdout_offset=90)) is None


@pytest.mark.parametrize(
    "control",
    [
        ShellControl(operation="input", data="x"),
        ShellControl(operation="signal"),
        ShellControl(operation="wait", timeout_seconds=1),
    ],
)
def test_observation_does_not_execute_controls_or_long_waits(control):
    assert _observation(_stream(b"ready")).read("exec-test", control) is None


def test_empty_delta_preserves_requested_offset_with_unknown_producer_count():
    stream = ShellStream.from_read(
        ProcessStreamRead(
            chunks=(),
            next_cursor=None,
            capture=EnvironmentOutputCapture(
                kind="inline", available_end=42, captured_bytes=42, producer_complete=False, content_complete=False
            ),
        ),
        requested_offset=42,
    )
    assert stream.start_offset == 42 and stream.data == b""
    assert stream.capture.produced_bytes is None


def test_direct_local_silent_observations_reuse_immutable_preview():
    from a13n_harness.environment.retention import EnvironmentOutputPolicy
    from a13n_harness.providers.environment.direct_local.processes import _OutputCollector

    collector = _OutputCollector(
        policy=EnvironmentOutputPolicy(max_inline_bytes=1024 * 1024, max_output_bytes=1024 * 1024, overflow="truncate"),
        buffer_limit=1024 * 1024,
        writer=None,
    )
    collector.preview.extend(b"large" * 10000)
    collector.produced = len(collector.preview)
    first = collector.snapshot()
    assert collector.snapshot().inline is first.inline
    collector.preview.extend(b"new")
    collector.produced += 3
    second = collector.snapshot()
    assert second.inline == first.inline + b"new"
    assert collector.snapshot().inline is second.inline
