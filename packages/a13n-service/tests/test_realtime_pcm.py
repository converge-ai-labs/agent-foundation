import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from a13n_service.runs.realtime import Channel
from a13n_service.runs.realtime_transport import Bridge, Pipe

pytestmark = pytest.mark.anyio


def pair():
    redis = AsyncMock()
    redis.mget.return_value = (None, None)
    redis.xread.return_value = []
    batch = MagicMock()
    batch.execute = AsyncMock()
    redis.pipeline = MagicMock()
    redis.pipeline.return_value.__aenter__.return_value = batch
    return Pipe(Bridge(redis), "run", side="control"), Pipe(Bridge(redis), "run", side="worker")


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


async def test_disconnect_wakeup_preempts_pending_commit():
    _, worker = pair()
    scanned = asyncio.Event()

    async def scan(*args, **kwargs):
        scanned.set()
        return []

    worker.redis.xread.side_effect = scan
    controls(worker, b"commit:1", b"text")
    receiving = asyncio.create_task(worker.receive())
    await scanned.wait()
    worker.redis.mget.return_value = (worker.fence, None)
    worker.scan = True
    worker.wake.set()
    async with asyncio.timeout(1):
        assert await receiving == b"closed"


async def test_mid_segment_loss_reports_once_and_discards_the_tail():
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
    from a13n_service.runs import realtime
    from a13n_service.runs.realtime_transport import Offer

    _, worker = pair()
    worker.bridge.start = AsyncMock()
    worker.redis.getdel.return_value = Offer(
        control_id="control", principal_id="user", workspace_id="workspace", audio=True
    ).model_dump_json()
    loop = asyncio.get_running_loop()
    clock = loop.time
    scanned = asyncio.Event()

    def scan(*args, **kwargs):
        scanned.set()
        return []

    worker.redis.xread.side_effect = scan
    monkeypatch.setattr(realtime, "Pipe", lambda *args, **kwargs: worker)
    monkeypatch.setattr(realtime, "prove", AsyncMock())
    lease = MagicMock(workspace_id="workspace", attempt_id="attempt", number=1)

    async def open_call():
        async with realtime.worker_channel(MagicMock(), lease) as channel:
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
                socket, control, offer=None, authorize_attachment=AsyncMock(), reauthorize=AsyncMock(), interval=60
            )


async def test_control_buffers_new_epoch_pcm_until_interruption_receipt():
    from a13n_service.runs.realtime import relay

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
            relay(socket, control, offer=None, authorize_attachment=AsyncMock(), reauthorize=AsyncMock(), interval=60)
        )
        try:
            await events.put(b"a")
            await events.put(b'j{"type":"ready"}')
            assert await outgoing.get() == {"type": "ready"}
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
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_disconnect_survives_redis_close_notification_failure():
    from redis.exceptions import ConnectionError

    control, _ = pair()
    batch = control.redis.pipeline.return_value.__aenter__.return_value
    batch.execute.side_effect = ConnectionError()
    await control.shutdown()
    batch.execute.assert_awaited_once()


async def test_unattached_control_close_and_idle_control_stream(redis_url):
    from a13n_service.infra.errors import ServiceError
    from a13n_service.runs.realtime_transport import ATTACH_SECONDS, Offer
    from redis.asyncio import Redis

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
        await redis.expire(control.outbox, 1)
        await control.send(b"ping")
        assert await redis.ttl(control.outbox) >= ATTACH_SECONDS * 2 - 1
