"""Measure usage-ledger cost per model call as the run's record count grows.

Run: uv run --locked python dev/harness/benchmark_usage_ledger.py --records 250 1000 4000
Only synthetic in-memory records are used; nothing leaves the process. Delivery to a delta-capable Host reporter stays
roughly flat per call, with or without a run state coordinator. Two modes stay proportional to the record count by
design and are included for comparison: `--mode snapshot` delivers a full snapshot per call, and `--export` also exports
the state per call, as Hosts that checkpoint before every model request do, because an exported state carries the
complete usage snapshot.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
from time import perf_counter

from a13n_harness.metering import ModelUsageBinding
from a13n_harness.state import AgentContextState
from a13n_harness.usage import BoundedRequestUsage, ModelUsageRecord, RunUsageLedger, UsageDelta, UsageSnapshot


class DeltaReporter:
    async def report_delta(self, delta: UsageDelta) -> None:
        return None


class SnapshotReporter:
    async def report(self, snapshot: UsageSnapshot) -> None:
        return None


def record(ledger: RunUsageLedger, ordinal: int) -> ModelUsageRecord:
    return ModelUsageRecord(
        record_id=f"record-{ordinal}",
        run_id=ledger.run_id,
        agent_instance_id=ledger.instance.agent_instance_id,
        response_ordinal=ordinal,
        response_state="complete",
        response_timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        request_usage=BoundedRequestUsage(input_tokens=100 + ordinal, output_tokens=10),
    )


async def measure(count: int, mode: str, with_state: bool, export: bool) -> float:
    ledger = ModelUsageBinding.standalone(source="benchmark").ledger
    ledger.reporter = SnapshotReporter() if mode == "snapshot" else DeltaReporter()
    state = AgentContextState() if with_state or export else None
    ledger._state = state
    started = perf_counter()
    for ordinal in range(count):
        ledger._append(record(ledger, ordinal))
        await ledger._flush(reason="model_request")
        if state is not None and export:
            # The usage part of AgentContext.export_state.
            await ledger.save()
            await state.snapshot()
    elapsed = perf_counter() - started
    await ledger._flush(reason="terminal")
    return elapsed


async def main() -> None:
    parser = argparse.ArgumentParser(description="Measure usage-ledger cost per model call.")
    parser.add_argument("--records", type=int, nargs="+", default=[250, 1000, 4000])
    parser.add_argument("--mode", choices=("delta", "snapshot"), default="delta", help="Host reporter kind")
    parser.add_argument("--state", action="store_true", help="also publish into a run state coordinator")
    parser.add_argument(
        "--export", action="store_true", help="also export the state after every call (implies --state)"
    )
    args = parser.parse_args()
    print(f"mode={args.mode} state={args.state or args.export} export={args.export}")
    for count in args.records:
        elapsed = await measure(count, args.mode, args.state, args.export)
        print(f"records={count:6d} total={elapsed:9.3f}s per_record={elapsed / count * 1000:9.3f}ms")


if __name__ == "__main__":
    asyncio.run(main())
