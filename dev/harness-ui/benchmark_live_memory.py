"""Measure live fan-out in a fresh Linux process; use --trace for Python heap."""

from __future__ import annotations

import argparse
import asyncio
import gc
import json
import resource
import time
import tracemalloc
from contextlib import AsyncExitStack

from a13n_harness_ui.live import HarnessUiLiveHub
from ag_ui.core import CustomEvent


async def measure(watchers: int, count: int, trace: bool) -> dict:
    gc.collect()
    if trace:
        tracemalloc.start()
    hub = HarnessUiLiveHub(ring_size=count, subscriber_buffer_size=count)
    async with AsyncExitStack() as stack:
        subscriptions = [await stack.enter_async_context(hub.subscribe(root_thread_id="root")) for _ in range(watchers)]
        started = time.perf_counter()
        for index in range(count):
            await hub.publish(
                run_kind="child",
                root_thread_id="root",
                parent_thread_id="root",
                thread_id="child",
                run_id="run",
                events=(
                    CustomEvent(
                        name="sample",
                        value={
                            "index": index,
                            "rows": [{"key": str(row), "value": f"{index}:{row}:" + "x" * 80} for row in range(128)],
                        },
                    ),
                ),
            )
        publish_ms = (time.perf_counter() - started) * 1000
        gc.collect()
        retained = tracemalloc.get_traced_memory()[0] if trace else None
        started = time.perf_counter()
        for subscription in subscriptions:
            for index in range(count):
                event = await subscription.receive()
                assert event.payload["value"]["index"] == index
        drain_ms = (time.perf_counter() - started) * 1000
    await hub.close()
    del hub, subscriptions
    gc.collect()
    after_close = tracemalloc.get_traced_memory()[0] if trace else None
    if trace:
        tracemalloc.stop()
    return {
        "watchers": watchers,
        "events": count,
        "traced": trace,
        "queued_heap_bytes": retained,
        "closed_heap_bytes": after_close,
        "publish_ms": round(publish_ms, 2),
        "drain_ms": round(drain_ms, 2),
        "max_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watchers", type=int, default=8)
    parser.add_argument("--events", type=int, default=128)
    parser.add_argument("--trace", action="store_true")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(measure(args.watchers, args.events, args.trace))))
