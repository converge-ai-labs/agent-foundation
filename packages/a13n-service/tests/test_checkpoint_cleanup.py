"""Deferred checkpoint reclamation preserves references and has a total time and memory bound."""

import asyncio
from pathlib import Path

import pytest
from a13n_service.infra.objects.local import LocalObjects
from a13n_service.runs import checkpoints
from a13n_service.runs.checkpoints import Cleanup, Committed, DisplayPointer, StatePointer
from a13n_service.runs.display import StreamPosition

pytestmark = pytest.mark.anyio


def _committed(sequence: int, *, position: int | None = None) -> Committed:
    return Committed(
        state=StatePointer(digest=f"{sequence:064x}", size=0, format=1, seq=sequence, attempt=1),
        display=DisplayPointer(
            digest=f"{sequence + 100:064x}",
            size=0,
            format=1,
            position=StreamPosition(attempt=1, sequence=sequence if position is None else position),
        ),
    )


async def test_same_position_display_is_kept_until_seal(tmp_path: Path) -> None:
    objects = LocalObjects(tmp_path, max_bytes=1024, timeout=5)
    previous, committed = _committed(1, position=1), _committed(2, position=1)
    for kind, pointer in (
        ("state", previous.state),
        ("display", previous.display),
        ("state", committed.state),
        ("display", committed.display),
    ):
        await objects.put(f"orgs/org/runs/run/{kind}/{pointer.digest}", b"", content_type="application/json")
    await checkpoints._discard(objects, "org", "run", previous, committed)
    assert await objects.get(f"orgs/org/runs/run/state/{previous.state.digest}") is None
    assert await objects.get(f"orgs/org/runs/run/display/{previous.display.digest}") == b""
    cleanup = Cleanup(timeout=5)
    # This is the frozen pointer pair of a sealed run, whose references can never change again.
    run = checkpoints._Objects(
        id="run", organization_id="org", checkpoint=committed.state.model_dump(), display=committed.display.model_dump()
    )
    await cleanup.sealed(objects, run)
    worker = asyncio.create_task(cleanup.run())
    try:
        await cleanup.queue.join()
        assert await objects.get(f"orgs/org/runs/run/display/{previous.display.digest}") is None
        assert await objects.get(f"orgs/org/runs/run/state/{committed.state.digest}") == b""
        assert await objects.get(f"orgs/org/runs/run/display/{committed.display.digest}") == b""
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)


async def test_a_cleanup_deadline_covers_the_whole_job_and_does_not_stop_the_next_job(
    tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    objects = LocalObjects(tmp_path, max_bytes=1024, timeout=5)
    deleted = []

    async def delete(key: str) -> None:
        deleted.append(key)
        if "/slow/" in key:
            await asyncio.Event().wait()

    monkeypatch.setattr(objects, "delete", delete)
    cleanup = Cleanup(timeout=0.01)
    previous, committed = _committed(1), _committed(2)
    await cleanup.discard(objects, "org", "slow", previous, committed)
    await cleanup.discard(objects, "org", "fast", previous, committed)
    worker = asyncio.create_task(cleanup.run())
    try:
        async with asyncio.timeout(1):
            await cleanup.queue.join()
        assert len(deleted) == 3
        assert "/slow/" in deleted[0] and all("/fast/" in key for key in deleted[1:])
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)


async def test_cleanup_buffer_never_blocks_a_committer(tmp_path: Path) -> None:
    objects = LocalObjects(tmp_path, max_bytes=1024, timeout=5)
    cleanup = Cleanup(timeout=5)
    previous, committed = _committed(1), _committed(2)
    async with asyncio.timeout(1):
        for _ in range(cleanup.BUFFER + 1):
            await cleanup.discard(objects, "org", "run", previous, committed)
    assert cleanup.queue.qsize() == cleanup.BUFFER
