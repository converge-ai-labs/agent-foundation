"""Run object envelopes reject corruption and bound decoded allocation."""

from dataclasses import replace

import pytest
import zstandard
from a13n_service.infra.errors import ServiceError
from a13n_service.runs.object_codec import CODECS, ObjectCodec


@pytest.mark.parametrize("kind", ["state", "display"])
def test_run_object_codec_roundtrip_has_kind_and_checksum(kind: str) -> None:
    codec = CODECS[kind]
    original = b'{"text":"' + b"large repeated content " * 1000 + b'"}'
    encoded = codec.encode(original)
    assert len(encoded) < len(original)
    assert encoded.startswith(codec.prefix)
    assert zstandard.get_frame_parameters(encoded[len(codec.prefix) :]).has_checksum
    assert codec.decode(encoded) == original
    other = CODECS["display" if kind == "state" else "state"]
    with pytest.raises(ServiceError, match="envelope"):
        other.decode(encoded)


@pytest.mark.parametrize("change", ["checksum", "truncated", "trailing", "no-checksum", "raw-json"])
def test_run_object_codec_rejects_invalid_frames(change: str) -> None:
    codec = CODECS["state"]
    encoded = codec.encode(b'{"harness":{}}')
    if change == "checksum":
        encoded = encoded[:-1] + bytes([encoded[-1] ^ 1])
    elif change == "truncated":
        encoded = encoded[:-1]
    elif change == "trailing":
        encoded += b"trailing bytes"
    elif change == "no-checksum":
        encoded = codec.prefix + zstandard.ZstdCompressor().compress(b"{}")
    else:
        encoded = b"{}"
    with pytest.raises(ServiceError, match="envelope"):
        codec.decode(encoded)


@pytest.mark.parametrize("known_size", [False, True])
def test_run_object_codec_bounds_known_and_unknown_decoded_size(known_size: bool) -> None:
    codec = ObjectCodec(b"test\n", decoded_bytes=32, encoded_bytes=1024)
    encoded = codec.prefix + zstandard.ZstdCompressor(write_checksum=True, write_content_size=known_size).compress(
        b"x" * 1000
    )
    with pytest.raises(ServiceError, match="envelope"):
        codec.decode(encoded)
    with pytest.raises(ServiceError, match="decoded byte"):
        codec.encode(b"x" * 33)
    with pytest.raises(ServiceError, match="envelope"):
        replace(codec, encoded_bytes=8).decode(encoded)
    with pytest.raises(ServiceError, match="encoded byte"):
        replace(codec, encoded_bytes=8).encode(b"x")


@pytest.mark.anyio
async def test_paired_checkpoint_publication_loads_both_envelopes(runtime, tenant) -> None:
    from a13n_harness import HarnessState
    from a13n_service.runs.attempts import Lease
    from a13n_service.runs.checkpoints import (
        DISPLAY_FORMAT,
        FORMAT,
        RunState,
        load_display,
        load_state,
        publish_checkpoint,
    )
    from a13n_service.runs.display import Display
    from a13n_stream_protocol import DisplayPosition, DisplaySnapshot, Producer

    lease = Lease(
        "run_codec", "rat_codec", "thr_codec", tenant.organization_id, tenant.workspace_id, 1, "worker", "token"
    )
    state = RunState(harness=HarnessState.new(thread_id=lease.thread_id), seq=1, attempt=1)
    display = Display(
        snapshot=DisplaySnapshot(
            position=DisplayPosition(
                producer=Producer(run_id=lease.run_id, generation="1"),
                sequence=3,
            )
        )
    )
    published = await publish_checkpoint(runtime, lease, state, display)
    assert published.state.format == FORMAT == 2
    assert published.display.format == DISPLAY_FORMAT == 2
    assert (await runtime.objects.get(published.state.key)).startswith(CODECS["state"].prefix)
    assert (await runtime.objects.get(published.display.key)).startswith(CODECS["display"].prefix)
    assert await load_state(runtime.objects, lease.run_id, published.state) == state
    assert await load_display(runtime.objects, published.display) == display
    with pytest.raises(ServiceError):
        await load_state(runtime.objects, lease.run_id, published.state.model_copy(update={"format": 1}))
    with pytest.raises(ServiceError):
        await load_display(runtime.objects, published.display.model_copy(update={"format": 1}))
