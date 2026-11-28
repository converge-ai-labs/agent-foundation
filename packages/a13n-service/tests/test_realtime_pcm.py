import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, call

import pytest
from a13n_service.runs import realtime, realtime_transport
from a13n_service.runs.realtime import Channel
from a13n_service.runs.realtime_transport import Bridge, Pipe, TransportError
from a13n_stream_protocol.realtime import SpeechTranscript
from redis.asyncio import Redis
from redis.exceptions import ResponseError
from starlette.websockets import WebSocketState

pytestmark = pytest.mark.anyio


def pair():
    redis = AsyncMock()
    batch = MagicMock()
    batch.execute = AsyncMock(return_value=[(None, None), []])
    redis.pipeline = MagicMock()
    redis.pipeline.return_value.__aenter__.return_value = batch
    return Pipe(Bridge(redis), "run", side="control"), Pipe(Bridge(redis), "run", side="worker")


def redis_batch(pipe):
    return pipe.redis.pipeline.return_value.__aenter__.return_value


def controls(receiver, *frames):
    arrived = asyncio.get_running_loop().time()
    receiver.pending.extend((frame, arrived) for frame in frames)
    receiver.wake.set()


async def deliver_audio(sender, receiver, data):
    await sender.send(b"b" + data)
    _, payload = sender.redis.publish.await_args.args
    receiver.audio.append(payload.split(b" ", 2)[2])
    receiver.wake.set()


async def test_saturated_output_preserves_signaling_and_recent_audio():
    control, worker = pair()
    channel = Channel("actor", "workspace")
    for i in range(65):
        channel.emit(i.to_bytes(2))
    assert not channel.closed.is_set()
    while not channel.pcm.empty():
        epoch, frame = channel.pcm.get_nowait()
        assert epoch == worker.epoch
        await deliver_audio(worker, control, frame)
    channel.emit((65).to_bytes(2))
    epoch, frame = channel.pcm.get_nowait()
    assert epoch == worker.epoch
    await deliver_audio(worker, control, frame)
    controls(control, b"approval_required")
    assert await control.receive() == b"approval_required"
    for i in range(34, 66):
        raw = await control.receive()
        assert int.from_bytes(raw[1:9]) == i * 2
        assert raw[9:] == i.to_bytes(2)
    controls(control, b"sealed")
    assert await control.receive() == b"sealed"


def test_failed_run_reserves_last_outgoing_slot_for_sealed():
    channel = Channel("actor", "workspace", outgoing=asyncio.Queue(2))
    channel.emit({"type": "ready"})
    channel.finish("run", "failed")

    assert channel.outgoing.get_nowait() == {"type": "ready"}
    assert channel.outgoing.get_nowait() == {"type": "sealed", "run_id": "run", "status": "failed"}
    assert channel.finished.is_set()
    assert not channel.closed.is_set()


@pytest.mark.parametrize("audio_first", [True, False])
async def test_consecutive_commits_preserve_input_boundaries(audio_first):
    control, worker = pair()
    await deliver_audio(control, worker, b"first")
    await control.send(b"commit:1")
    await deliver_audio(control, worker, b"second")
    await control.send(b"commit:2")
    if audio_first:
        assert await worker.receive() == b"bfirst"
        receiving = asyncio.create_task(worker.receive())
        await asyncio.sleep(0)
        assert not receiving.done()
    controls(worker, b"commit:1", b"commit:2", b"text")
    async with asyncio.timeout(1):
        if audio_first:
            assert await receiving == b"commit"
        else:
            assert await worker.receive() == b"bfirst"
            assert await worker.receive() == b"commit"
        assert await worker.receive() == b"bsecond"
        assert await worker.receive() == b"commit"
        assert await worker.receive() == b"text"


async def test_missing_audio_resets_only_its_own_segment():
    control, worker = pair()
    await control.send(b"blost")
    await control.send(b"commit:1")
    await deliver_audio(control, worker, b"next")
    controls(worker, b"commit:1", b"commit:2")
    async with asyncio.timeout(1):
        assert await worker.receive() == b"gap"
        assert await worker.receive() == b"bnext"
        assert await worker.receive() == b"commit"


async def test_control_send_pipelines_append_expiration_and_wakeup():
    control, _ = pair()
    batch = redis_batch(control)
    for seq, frame in enumerate((b"o", b"finish"), start=1):
        batch.reset_mock()
        control.redis.pipeline.reset_mock()
        first = seq == 1
        batch.execute.return_value = [b"1-0", True, 1] if first else [b"2-0", 1]
        await control.send(frame)
        control.redis.pipeline.assert_called_once_with(transaction=first)
        commands = [
            call.xadd(
                control.outbox,
                {b"seq": seq, b"fence": control.fence, b"frame": frame},
                maxlen=512,
                approximate=False,
                nomkstream=not first,
            )
        ]
        if first:
            commands.append(call.expire(control.outbox, 40))
        commands.extend((call.publish(f"a13n:rt:{control.peer}:control", control.outbox.encode()), call.execute()))
        assert batch.method_calls == commands
        batch.execute.assert_awaited_once()


@pytest.mark.parametrize("expired_heartbeat", [False, True])
async def test_control_receive_pipelines_markers_before_applying_rows(expired_heartbeat):
    _, worker = pair()
    batch = redis_batch(worker)
    worker.last_check = asyncio.get_running_loop().time() - 2
    worker.check_heartbeat_after = worker.last_check
    entries = [(b"1-0", {b"fence": worker.fence, b"seq": b"1", b"frame": b"j{}"})]
    alive = None if expired_heartbeat else worker.fence
    batch.execute.return_value = [(None, alive), [(worker.inbox, entries)]]
    async with asyncio.timeout(1):
        if expired_heartbeat:
            with pytest.raises(TransportError):
                await worker.receive()
            assert worker.cursor == b"0-0"
        else:
            assert await worker.receive() == b"j{}"
            assert worker.cursor == b"1-0"
    worker.redis.pipeline.assert_called_once_with(transaction=False)
    assert batch.method_calls == [
        call.mget(worker.inbox + ":close", worker.outbox + ":alive"),
        call.xread({worker.inbox: b"0-0"}, count=512),
        call.execute(raise_on_error=False),
    ]
    batch.execute.assert_awaited_once()


async def test_disconnect_wakeup_preempts_pending_commit():
    _, worker = pair()
    scanned = asyncio.Event()

    def scan(**kwargs):
        scanned.set()
        return [(None, None), []]

    batch = redis_batch(worker)
    batch.execute.side_effect = scan
    controls(worker, b"commit:1", b"text")
    receiving = asyncio.create_task(worker.receive())
    await scanned.wait()
    batch.execute.side_effect = None
    batch.execute.return_value = [(worker.fence, None), ResponseError("stream unavailable")]
    worker.scan = True
    worker.wake.set()
    async with asyncio.timeout(1):
        assert await receiving == b"closed"


async def test_mid_segment_loss_reports_once_and_discards_the_tail(monkeypatch):
    discontinuities = Mock()
    monkeypatch.setattr(realtime_transport, "PCM_DISCONTINUITIES", discontinuities)
    control, worker = pair()
    await deliver_audio(control, worker, b"first")
    await control.send(b"blost")
    await deliver_audio(control, worker, b"tail1")
    await deliver_audio(control, worker, b"tail2")
    await control.send(b"commit:4")
    await deliver_audio(control, worker, b"next")
    controls(worker, b"commit:4", b"commit:5")
    async with asyncio.timeout(1):
        assert await worker.receive() == b"bfirst"
        assert await worker.receive() == b"gap"
        assert await worker.receive() == b"bnext"
        assert await worker.receive() == b"commit"
    discontinuities.add.assert_called_once_with(1, {"direction": "ingress"})


async def test_auto_vad_resumes_continuous_input_after_reset():
    control, worker = pair()
    await deliver_audio(control, worker, b"before")
    await control.send(b"blost")
    await deliver_audio(control, worker, b"discontinuous")
    assert await worker.receive() == b"bbefore"
    assert await worker.receive() == b"gap"
    await deliver_audio(control, worker, b"repeat")
    assert await worker.receive() == b"brepeat"
    await control.send(b"blost_again")
    await deliver_audio(control, worker, b"discontinuous_again")
    assert await worker.receive() == b"gap"


async def test_late_first_commit_clears_uncommitted_tail_without_second_notice():
    control, worker = pair()
    await control.send(b"blost")
    await deliver_audio(control, worker, b"discontinuous")
    assert await worker.receive() == b"gap"
    await deliver_audio(control, worker, b"tail")
    assert await worker.receive() == b"btail"
    await control.send(b"commit:3")
    controls(worker, b"commit:3")
    assert await worker.receive() == b"reset"
    await deliver_audio(control, worker, b"new")
    controls(worker, b"commit:4")
    assert await worker.receive() == b"bnew"
    assert await worker.receive() == b"commit"


async def test_worker_keeps_stale_pcm_in_previous_epoch_until_provider_cut():
    from a13n_service.runs.realtime import _worker_io

    _, worker = pair()
    channel = Channel("actor", "workspace")
    async with _worker_io(worker, channel), asyncio.timeout(2):
        controls(worker, b'j{"type":"interrupt","played_bytes":2}')
        command = await channel.incoming.get()
        assert command.type == "interrupt" and worker.epoch == 1
        # The interrupt has crossed Redis while the provider input task is still busy.
        channel.emit(b"old")
        channel.playback_epoch += 1
        channel.emit(b"new")
        while not worker.redis.publish.await_count:
            await asyncio.sleep(0)
        calls = worker.redis.publish.await_args_list
        assert len(calls) == 1 and calls[0].args[1].endswith((3).to_bytes(8) + b"new")


async def test_worker_accepts_slow_control_handshake(monkeypatch):
    from a13n_service.runs.realtime_transport import Offer

    _, worker = pair()
    worker.bridge.start = AsyncMock()
    worker.redis.getdel.return_value = Offer(
        control_id="control", principal_id="user", workspace_id="workspace", audio=True
    ).model_dump_json()
    loop = asyncio.get_running_loop()
    clock = loop.time
    scanned = asyncio.Event()

    def execute(*, raise_on_error=True):
        if not raise_on_error:
            scanned.set()
        return [(None, None), []]

    redis_batch(worker).execute.side_effect = execute
    monkeypatch.setattr(realtime, "Pipe", lambda *args, **kwargs: worker)
    monkeypatch.setattr(realtime, "prove", AsyncMock())
    lease = MagicMock(workspace_id="workspace", attempt_id="attempt", number=1)

    async def open_call():
        runtime = MagicMock()
        runtime.settings.worker.stream_coalesce_seconds = 0.1
        async with realtime.worker_channel(runtime, lease) as channel:
            assert channel.transcript_window == 0.1
            assert await channel.incoming.get() == "hello"

    async with asyncio.timeout(15), asyncio.TaskGroup() as tasks:
        tasks.create_task(open_call())
        await scanned.wait()
        # Control is still authorizing the attachment after the normal heartbeat timeout.
        monkeypatch.setattr(loop, "time", lambda: clock() + 8)
        controls(worker, b"o", b'j{"type":"text","text":"hello"}')


async def test_relay_rejects_input_before_worker_attach():
    from a13n_service.infra.errors import ServiceError
    from a13n_service.runs.realtime import relay

    control, _ = pair()
    socket = AsyncMock()
    socket.receive.return_value = {"type": "websocket.receive", "text": '{"type":"text","text":"hello"}'}
    async with asyncio.timeout(1):
        with pytest.raises(ServiceError, match="Wait for realtime ready"):
            await relay(
                socket,
                control,
                offer=None,
                authorize_attachment=AsyncMock(),
                reauthorize=AsyncMock(),
                interval=60,
                observation=Mock(),
            )


async def test_relay_keeps_sealed_outcome_when_reader_fails_on_same_tick():
    from a13n_service.runs.realtime import relay
    from a13n_service.runs.realtime_transport import unavailable

    control, _ = pair()
    socket = AsyncMock()
    sealed = {"type": "sealed", "run_id": "run", "status": "completed"}
    frames = iter((b"a", b'j{"type":"ready"}', b'j{"type":"sealed","run_id":"run","status":"completed"}'))
    writer_waiting, reader_waiting, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def receive(**kwargs):
        if frame := next(frames, None):
            return frame
        writer_waiting.set()
        await release.wait()
        return b"finish"

    async def send(frame):
        if frame.startswith(b"j"):
            reader_waiting.set()
            await release.wait()
            raise unavailable()

    control.receive = receive
    control.send = send

    async def input_after_seal():
        await writer_waiting.wait()
        return {"type": "websocket.receive", "text": '{"type":"text","text":"late input"}'}

    socket.receive.side_effect = input_after_seal
    async with asyncio.timeout(2), asyncio.TaskGroup() as group:
        outcome = group.create_task(
            relay(
                socket,
                control,
                offer=None,
                authorize_attachment=AsyncMock(),
                reauthorize=AsyncMock(),
                interval=60,
                observation=Mock(),
            )
        )
        await reader_waiting.wait()
        release.set()
    assert outcome.result() is False
    assert socket.send_json.await_args.args == (sealed,)


async def test_control_orders_pcm_after_ready_and_interruption_receipts(monkeypatch):
    from a13n_service.runs.realtime import relay

    discontinuities = Mock()
    monkeypatch.setattr(realtime, "PCM_DISCONTINUITIES", discontinuities)
    control, _ = pair()
    incoming, events = asyncio.Queue(), asyncio.Queue()
    outgoing = asyncio.Queue()
    socket = AsyncMock()
    socket.receive.side_effect = incoming.get
    socket.send_json.side_effect = outgoing.put
    socket.send_bytes.side_effect = outgoing.put

    async def receive(**kwargs):
        return await events.get()

    control.receive = receive
    control.send = AsyncMock()

    async with asyncio.timeout(2):
        task = asyncio.create_task(
            relay(
                socket,
                control,
                offer=None,
                authorize_attachment=AsyncMock(),
                reauthorize=AsyncMock(),
                interval=60,
                observation=Mock(),
            )
        )
        try:
            await events.put(b"a")
            # Pub/Sub PCM can reach Control before the ordered ready frame.
            await events.put(b"b" + (0).to_bytes(8) + b"opening")
            await events.put(b'j{"type":"ready"}')
            assert await outgoing.get() == {"type": "ready"}
            assert await outgoing.get() == b"opening"
            await incoming.put({"type": "websocket.receive", "text": '{"type":"interrupt","played_bytes":0}'})
            while not control.epoch:
                await asyncio.sleep(0)
            await events.put(b"b" + (9600).to_bytes(8) + b"next")
            await events.put(b'j{"type":"interrupted"}')
            assert await outgoing.get() == {"type": "interrupted"}
            assert await outgoing.get() == {"type": "playback_position", "offset": 9600}
            assert await outgoing.get() == b"next"
            await events.put(b"finish")
            await task
            discontinuities.add.assert_called_once_with(1, {"direction": "egress"})
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_disconnect_survives_redis_close_notification_failure():
    from redis.exceptions import ConnectionError

    control, _ = pair()
    batch = redis_batch(control)
    batch.execute.side_effect = ConnectionError()
    await control.shutdown()
    batch.execute.assert_awaited_once()


async def test_unattached_control_close_and_idle_control_stream(redis_url):
    from a13n_service.infra.errors import ServiceError
    from a13n_service.runs.realtime_transport import ATTACH_SECONDS, Offer

    async with Redis.from_url(redis_url) as redis:
        for offered in (False, True):
            run = f"early-close-{offered}"
            control = Pipe(Bridge(redis), run, side="control")
            worker = Pipe(Bridge(redis), run, side="worker")
            lease = MagicMock(workspace_id="workspace", attempt_id="attempt")
            if offered:
                await control.publish_offer(
                    Offer(control_id=control.control_id, principal_id="user", workspace_id="workspace", audio=True)
                )
                await worker.attach(lease)
            await control.shutdown()
            async with asyncio.timeout(1):
                if offered:
                    assert await worker.receive() == b"closed"
                else:
                    with pytest.raises(ServiceError, match="transport is unavailable"):
                        await worker.attach(lease)
        await control.send(b"stop")
        assert await redis.ttl(control.outbox) >= ATTACH_SECONDS * 2 - 1
        await redis.expire(control.outbox, 10)
        await control.send(b"finish")
        assert 0 < await redis.ttl(control.outbox) <= 10
        await control.send(b"ping")
        assert await redis.ttl(control.outbox) >= ATTACH_SECONDS * 2 - 1
        await redis.delete(control.outbox)
        with pytest.raises(TransportError):
            await control.send(b"stop")
        assert not await redis.exists(control.outbox)


def transcript(text: str, *, final: bool = False) -> SpeechTranscript:
    return {"message_id": "msg", "role": "assistant", "text": text, "final": final}


async def test_realtime_coalesces_burst_without_a_control_reader(redis_url):
    async with Redis.from_url(redis_url) as redis:
        control = Pipe(Bridge(redis), "transcript-burst", side="control")
        worker = Pipe(Bridge(redis), "transcript-burst", side="worker")
        worker.control_id = control.control_id
        worker.attempt_id = control.attempt_id = "attempt"
        worker.peer, control.peer = control.bridge.id, worker.bridge.id
        channel = Channel("actor", "workspace", transcript_window=0.1)

        # The peer stalls long enough to accumulate more control entries than the old Redis limit.
        started = {"type": "ready"}
        queued = {"type": "approval_expired", "call_id": "call"}
        await worker.send(b"j" + json.dumps(started).encode())
        for _ in range(140):
            await worker.send(b"j" + json.dumps(queued).encode())
        async with realtime._worker_io(worker, channel):
            for i in range(600):
                channel.transcript(transcript(f"snapshot {i}"))
            user: SpeechTranscript = {**transcript("other role"), "role": "user"}
            channel.transcript(user)
            channel.transcript(transcript("complete", final=True))
            channel.transcript(transcript("another partial"))
            channel.emit({"type": "approval_required", "call_id": "next"})
            channel.transcript(transcript("before seal"))
            channel.finish("transcript-burst", "completed")

        assert channel.finished.is_set() and not channel.failed
        expected = [
            started,
            *([queued] * 140),
            {"type": "transcript", **user},
            {"type": "transcript", **transcript("complete", final=True)},
            {"type": "transcript", **transcript("another partial")},
            {"type": "approval_required", "call_id": "next"},
            {"type": "transcript", **transcript("before seal")},
            {"type": "sealed", "run_id": "transcript-burst", "status": "completed"},
        ]
        async with asyncio.timeout(3):
            assert [json.loads((await control.receive())[1:]) for _ in expected] == expected
            assert await control.receive() == b"finish"


async def test_final_transcript_supersedes_partial_and_cancels_idle_timer():
    channel = Channel("actor", "workspace", transcript_window=60)
    channel.transcript(transcript("partial"))
    timer = channel._transcript_timer
    assert timer is not None
    final = transcript("final", final=True)
    channel.transcript(final)
    assert timer.cancelled() and channel._transcript_timer is None
    assert channel.outgoing.get_nowait() == {"type": "transcript", **final}
    assert channel.outgoing.empty()


async def test_transcript_timer_and_zero_window():
    coalesced = Channel("actor", "workspace", transcript_window=0.01)
    coalesced.transcript(transcript("first"))
    coalesced.transcript(transcript("latest"))
    assert coalesced.outgoing.empty()
    async with asyncio.timeout(1):
        assert await coalesced.outgoing.get() == {"type": "transcript", **transcript("latest")}
    assert coalesced.outgoing.empty()

    immediate = Channel("actor", "workspace", transcript_window=0)
    for text in ("first", "latest"):
        immediate.transcript(transcript(text))
        assert immediate.outgoing.get_nowait() == {"type": "transcript", **transcript(text)}


@pytest.mark.parametrize(
    "kind", ["approval_required", "approval_expired", "interrupted", "draining", "sealed", "error"]
)
async def test_every_critical_control_frame_flushes_transcripts(kind):
    channel = Channel("actor", "workspace", transcript_window=1)
    channel.transcript(transcript("pending"))
    channel.emit({"type": kind})
    assert channel.outgoing.get_nowait() == {"type": "transcript", **transcript("pending")}
    assert channel.outgoing.get_nowait() == {"type": kind}


@pytest.mark.parametrize("side", ["control", "worker"])
async def test_pipe_counts_fatal_sequence_gap_once(monkeypatch, side):
    overflow = Mock()
    monkeypatch.setattr(realtime_transport, "CONTROL_OVERFLOWS", overflow)
    control, worker = pair()
    pipe = control if side == "control" else worker
    entries = [(b"1-0", {b"fence": pipe.fence, b"seq": b"2", b"frame": b"j{}"})]
    redis_batch(pipe).execute.return_value = [(None, None), [(b"control", entries)]]
    for _ in range(2):
        with pytest.raises(TransportError):
            await pipe.receive()
    overflow.add.assert_called_once_with(1, {"side": side})


async def test_worker_counts_fatal_control_queue_overflow(monkeypatch):
    overflow = Mock()
    monkeypatch.setattr(realtime, "CONTROL_OVERFLOWS", overflow)
    channel = Channel("actor", "workspace")
    assert channel.outgoing.maxsize == 512
    for _ in range(512):
        channel.emit({"type": "approval_expired"})
    with pytest.raises(TransportError, match="not keeping up"):
        channel.emit({"type": "draining"})
    with pytest.raises(TransportError, match="not keeping up"):
        channel.finish("run", "failed")
    assert channel.failed and channel.closed.is_set()
    overflow.add.assert_called_once_with(1, {"side": "worker"})


@pytest.mark.parametrize(
    ("transport", "scenario", "result", "reason"),
    [
        ("websocket", "normal", "ready", "completed"),
        ("webrtc", "provider_failure", "failed", "failed"),
        ("webrtc", "disconnect_after_seal", "failed", "failed"),
        ("websocket", "attach_timeout", "timeout", "transport_error"),
        ("webrtc", "offer_timeout", "timeout", "transport_error"),
        ("websocket", "disconnect", "abandoned", "client_disconnected"),
        ("websocket", "restart", "abandoned", "draining"),
        ("websocket", "worker_drain", "ready", "draining"),
        ("websocket", "worker_drain_timeout", "ready", "draining"),
        ("websocket", "bridge_closed", "ready", "transport_error"),
    ],
)
async def test_control_records_handshake_and_connection_lifetime(monkeypatch, transport, scenario, result, reason):
    active, handshakes, ended = Mock(), Mock(), Mock()
    monkeypatch.setattr(realtime, "ACTIVE_CONNECTIONS", active)
    monkeypatch.setattr(realtime, "HANDSHAKES", handshakes)
    monkeypatch.setattr(realtime, "CALLS_ENDED", ended)
    control, _ = pair()
    pipe_frames: asyncio.Queue[bytes] = asyncio.Queue()
    monkeypatch.setattr(control, "receive", pipe_frames.get)
    control.bridge.start = AsyncMock()
    if scenario == "attach_timeout":
        control.attach_deadline = asyncio.get_running_loop().time() - 1

    start = SimpleNamespace(transport=transport, audio=True, draft="", draft_asset_ids=(), max_duration_seconds=90)
    credential = SimpleNamespace(principal=SimpleNamespace(id="actor"))
    ticket = SimpleNamespace(start=start, run_id="run", session_id="session", thread_id="thread", credential=credential)
    runtime = SimpleNamespace(
        realtime_bridge=control.bridge, settings=SimpleNamespace(worker=SimpleNamespace(authority_seconds=60))
    )
    socket = AsyncMock()
    socket.app.state.runtime = runtime
    socket.scope = {"subprotocols": [realtime.SUBPROTOCOL, "token"]}
    socket.client_state = socket.application_state = WebSocketState.CONNECTED
    socket.receive_text.return_value = json.dumps(
        {"type": "offer", "sdp": "v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"}
    )
    incoming: asyncio.Queue[dict[str, str | int]] = asyncio.Queue()
    socket.receive.side_effect = incoming.get
    monkeypatch.setattr(realtime, "Pipe", lambda *args, **kwargs: control)
    monkeypatch.setattr(realtime, "_lookup", AsyncMock(return_value=ticket))
    monkeypatch.setattr(realtime, "_connection_principal", AsyncMock())
    monkeypatch.setattr(realtime, "_redeem", AsyncMock())
    monkeypatch.setattr(realtime, "_authorize_attachment", AsyncMock())
    monkeypatch.setattr(realtime, "_stop_abandoned", AsyncMock())

    async def observe_send(data):
        if data["type"] in {"accepted", "ready"}:
            assert active.add.call_args_list == [call(1, {"transport": transport})]
        if data["type"] == "sealed" and scenario == "disconnect_after_seal":
            incoming.put_nowait({"type": "websocket.disconnect", "code": 1000})

    socket.send_json.side_effect = observe_send
    if scenario in {"disconnect", "restart"}:
        incoming.put_nowait({"type": "websocket.disconnect", "code": 1012 if scenario == "restart" else 1000})
    elif scenario == "offer_timeout":
        socket.receive_text.side_effect = TimeoutError
    elif scenario != "attach_timeout":
        pipe_frames.put_nowait(b"a")
        failed_before_ready = scenario in {"provider_failure", "disconnect_after_seal"}
        if not failed_before_ready:
            pipe_frames.put_nowait(b'j{"type":"ready"}')
        if scenario in {"worker_drain", "worker_drain_timeout"}:
            pipe_frames.put_nowait(b'j{"type":"draining"}')
        if scenario in {"bridge_closed", "worker_drain_timeout"}:
            pipe_frames.put_nowait(b"closed")
        else:
            status = "failed" if failed_before_ready else "completed"
            pipe_frames.put_nowait(b"j" + json.dumps({"type": "sealed", "status": status}).encode())
            if scenario != "disconnect_after_seal":
                pipe_frames.put_nowait(b"finish")
    async with asyncio.timeout(2):
        await realtime.connect(socket, "workspace")
    assert active.add.call_args_list == [call(1, {"transport": transport}), call(-1, {"transport": transport})]
    assert handshakes.record.call_count == 1
    elapsed, labels = handshakes.record.call_args.args
    assert 0 <= elapsed < 2
    assert labels == {"transport": transport, "result": result}
    ended.add.assert_called_once_with(1, {"transport": transport, "reason": reason})
    if result == "timeout":
        message = (
            "The realtime transport is unavailable"
            if scenario == "attach_timeout"
            else "The realtime request could not be accepted."
        )
        socket.send_json.assert_awaited_with({"type": "error", "code": "realtime_transport_failed", "message": message})
        socket.close.assert_awaited_once_with(code=1008)
