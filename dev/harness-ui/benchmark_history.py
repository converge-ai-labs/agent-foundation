"""Compare legacy whole-display checkpoints with referenced history in a disposable store.

Run: uv run --locked python dev/harness-ui/benchmark_history.py --items 5000 50000
Only synthetic text and aggregate measurements leave the temporary store.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

from a13n_harness import HarnessState
from a13n_harness_ui.display_history import (
    DisplayHistoryCollector,
    import_display_history,
    ordinary_input,
    with_display_history,
)
from a13n_harness_ui.settings import StorageSettings
from a13n_harness_ui.storage import Continuation, StoredContinuation, open_local_store
from a13n_harness_ui.storage.continuations import publish_continuation, read_continuation
from a13n_harness_ui.storage.contracts import ReferencedContinuation
from a13n_harness_ui.storage.display import DisplayArchive, read_display, seal_display
from a13n_harness_ui.storage.objects import ObjectKind, ObjectRef
from a13n_stream_protocol.display import DisplayFold
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

COMPOSITION = ObjectRef(object_kind=ObjectKind.run_composition, object_schema_version="1", logical_digest="a" * 64)


def continuation(state, display=None):
    fields = dict(
        harness_release="benchmark",
        run_composition=COMPOSITION,
        harness_state=state,
        created_at=datetime.now(UTC),
    )
    return StoredContinuation(**fields) if display is None else Continuation(**fields, display=display)


async def publish(objects, value):
    if isinstance(value, Continuation):
        return await publish_continuation(objects, value)
    return (await objects.publish_model(object_kind=ObjectKind.continuation, value=value)).ref


async def checkpoint_sizes(objects, reference, mode):
    refs = {"manifest": reference}
    if mode == "referenced":
        manifest = await objects.read_model(reference, ReferencedContinuation)
        refs["native"] = manifest.native_state
        if manifest.display is not None:
            refs["display"] = manifest.display
    return {name: len((await objects.read(ref)).model_dump_json().encode()) for name, ref in refs.items()}


async def measure(count: int, repeats: int, single_turn: bool = False):
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    history = import_display_history(
        [
            message
            for index in range(count // 2)
            for message in (
                ModelRequest(parts=[UserPromptPart(f"Synthetic input {index}")], timestamp=timestamp),
                ModelResponse(parts=[TextPart("Synthetic answer " + "x" * 512)], timestamp=timestamp),
            )
        ]
    )
    if single_turn:
        history = history.model_copy(
            update={
                "items": tuple(
                    item for index, item in enumerate(history.items) if index == 0 or not ordinary_input(item)
                )
            }
        )
    native = HarnessState.new(message_history=[ModelRequest(parts=[UserPromptPart("fixed context " * 1024)])])
    delays = []

    async def heartbeat():
        while True:
            start = perf_counter()
            await asyncio.sleep(0.005)
            delays.append(max(0, perf_counter() - start - 0.005))

    with tempfile.TemporaryDirectory(prefix="harness-history-benchmark-") as directory:
        async with open_local_store(StorageSettings(data_root=Path(directory))) as store:
            start = perf_counter()
            archive = await seal_display(store.objects, DisplayArchive(tail=history))
            migration = perf_counter() - start
            restored = await read_display(store.objects, archive)
            assert restored.items == history.items
            del restored
            results = {}
            for mode in ("legacy", "referenced"):
                samples = []
                delays.clear()
                for _ in range(repeats):
                    selected_history = history if mode == "legacy" else archive.tail
                    seed = (
                        continuation(with_display_history(native, history))
                        if mode == "legacy"
                        else continuation(native, archive)
                    )
                    reference = await publish(store.objects, seed)
                    timer = asyncio.create_task(heartbeat())
                    await asyncio.sleep(0.01)
                    start = perf_counter()
                    loaded = (
                        await store.objects.read_model(reference, StoredContinuation)
                        if mode == "legacy"
                        else await read_continuation(store.objects, reference, include_display=True)
                    )
                    read_seconds = perf_counter() - start
                    start = perf_counter()
                    collector = None
                    fold = DisplayFold(
                        "benchmark",
                        selected_history.items,
                        full_content=True,
                        first=selected_history.items[0].ordinal,
                    )
                    if mode == "referenced":
                        collector = DisplayHistoryCollector((), selected_history)
                        collector.fold = fold
                        collector.capture()
                    initialize_seconds = perf_counter() - start
                    fold.fold([{"type": "TEXT_MESSAGE_CONTENT", "messageId": "new", "delta": "new output"}])
                    start = perf_counter()
                    if mode == "legacy":
                        frozen = selected_history.model_copy(update={"items": tuple(fold.export().items)})
                    else:
                        assert collector is not None
                        frozen = collector.capture()
                    capture_seconds = perf_counter() - start
                    start = perf_counter()
                    value = (
                        continuation(with_display_history(native, frozen))
                        if mode == "legacy"
                        else continuation(native, archive.model_copy(update={"tail": frozen}))
                    )
                    published = await publish(store.objects, value)
                    publish_seconds = perf_counter() - start
                    samples.append(
                        {
                            "read_ms": read_seconds * 1000,
                            "initialize_ms": initialize_seconds * 1000,
                            "capture_ms": capture_seconds * 1000,
                            "publish_ms": publish_seconds * 1000,
                        }
                    )
                    await asyncio.sleep(0.01)
                    timer.cancel()
                    await asyncio.gather(timer, return_exceptions=True)
                    assert loaded.harness_state.message_history == native.message_history
                sizes = await checkpoint_sizes(store.objects, published, mode)
                results[mode] = {
                    "median_ms": {
                        key: round(statistics.median(sample[key] for sample in samples), 3) for key in samples[0]
                    },
                    "checkpoint_json_bytes": sum(sizes.values()),
                    "object_json_bytes": sizes,
                    "capture_items": len(selected_history.items),
                    "max_event_loop_delay_ms": round(max(delays, default=0) * 1000, 3),
                }
            return {
                "items": len(history.items),
                "single_turn": single_turn,
                "repeats": repeats,
                "migration_ms": round(migration * 1000, 3),
                **results,
            }


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--items", type=int, nargs="+", default=[5000, 50000])
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--single-turn", action="store_true")
    args = parser.parse_args()
    for count in args.items:
        print(json.dumps(await measure(count, args.repeats, args.single_turn)), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
