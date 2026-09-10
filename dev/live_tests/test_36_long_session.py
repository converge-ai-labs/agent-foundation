"""One real sequential Run chain, production compaction, and PG/S3/API latency."""

import json
import logging
import math
import platform
from collections import defaultdict
from statistics import median
from time import perf_counter
from uuid import uuid4

import pytest

from .client import agent_input

pytestmark = pytest.mark.anyio
logger = logging.getLogger(__name__)


class Measurements:
    def __init__(self, path, metadata):
        self.path, self.metadata = path, metadata
        self.samples = defaultdict(list)
        self.enabled = False

    def add(self, name, milliseconds):
        if self.enabled:
            self.samples[name].append(milliseconds)
            self.save()

    async def measure(self, name, operation):
        started = perf_counter()
        result = await operation()
        self.add(name, (perf_counter() - started) * 1000)
        return result

    def save(self):
        summary = {
            name: {
                "n": len(values),
                "p50_ms": median(values),
                "p95_ms": sorted(values)[math.ceil(len(values) * 0.95) - 1],
                "max_ms": max(values),
            }
            for name, values in self.samples.items()
        }
        self.path.write_text(json.dumps({**self.metadata, "samples_ms": self.samples, "summary": summary}, indent=2))
        return summary


def session_input(live, case, *, sequence=None):
    text = live.start_body(case)["input"]["content"][0]["text"]
    if sequence is not None:
        text += f"\nLONG_SESSION_RUN {sequence}"
    if sequence == 1:
        text += f"\nLONG_SESSION_MEMORY {case['token']}"
    return agent_input(text + "\n" + "x" * live.config["long_session"]["message_bytes"])


async def accept(live, run, operation, case=None, *, sequence=None, results=None):
    body = {} if case is None else {"input": session_input(live, case, sequence=sequence)}
    if operation != "fork":
        body["expected_thread_version"] = (await live.thread(run["thread_id"]))["version"]

    async def post():
        return await live.request(
            "POST",
            f"/api/v1/runs/{run['id']}/{operation}",
            expected=202,
            headers={"Idempotency-Key": uuid4().hex},
            json=body,
        )

    receipt = await post() if results is None else await results.measure(operation + "_accept", post)
    live.track(receipt)
    return receipt


async def run_ids(live, path, depth):
    seen, cursor, cursors = set(), None, set()
    for _ in range(math.ceil(depth / 200) + 1):
        page = await live.request("GET", path, params={"limit": 200, **({"cursor": cursor} if cursor else {})})
        identifiers = [item["id"] for item in page["items"]]
        assert not seen.intersection(identifiers), "Duplicate Run across pages"
        seen.update(identifiers)
        cursor = page.get("next_cursor")
        if cursor is None:
            return seen
        assert cursor not in cursors, "Repeated pagination cursor"
        cursors.add(cursor)
    raise AssertionError("Run listing exceeded the expected chain depth")


async def test_long_session_operations(long_session):
    lab, checkpoints, repetitions = long_session
    live = lab.client
    case = await live.case("basic")
    source = None
    previous = None
    started = perf_counter()
    trajectory = lab.root / "trajectory.jsonl"
    progress_path = lab.root / "progress.json"
    progress = {"status": "incomplete", "checkpoints": checkpoints, "completed_runs": 0}
    progress_path.write_text(json.dumps(progress))
    for depth in range(1, checkpoints[-1] + 1):
        run_started = perf_counter()
        if source is None:
            body = {**live.start_body(case), "input": session_input(live, case, sequence=depth)}
            receipt = await live.request(
                "POST",
                f"/api/v1/workspaces/{live.config['workspace_id']}/runs",
                expected=202,
                headers={"Idempotency-Key": uuid4().hex},
                json=body,
            )
            live.track(receipt)
        else:
            receipt = await accept(live, source, "continue", case, sequence=depth)
        settled = await live.finish(receipt["run_id"])
        assert settled["output_text"].startswith(f"{case['token']}\nLONG_SESSION_RUN {depth}\n")
        if source is not None:
            assert settled["parent_run_id"] == source["id"] and settled["thread_id"] == source["thread_id"]
        source = settled
        elapsed_ms = (perf_counter() - run_started) * 1000
        probe = await live.request("GET", f"/__live__/performance/runs/{source['id']}", params={"measure": "false"})
        assert probe["sequence"] == depth and probe["memories"] == [case["token"]]
        if probe["attempts_started"] > 1:
            logger.warning(
                "Run recovered: runs=%s run_id=%s attempts=%s complete_ms=%.1f",
                depth,
                source["id"],
                probe["attempts_started"],
                elapsed_ms,
            )
        if previous is not None:
            assert probe["compactions"] in {previous["compactions"], previous["compactions"] + 1}
            if probe["compactions"] > previous["compactions"]:
                assert probe["compacted"] and probe["state_bytes"] < previous["state_bytes"]
                logger.info(
                    "Compacted: runs=%s count=%s state_bytes=%s -> %s",
                    depth,
                    probe["compactions"],
                    previous["state_bytes"],
                    probe["state_bytes"],
                )
        with trajectory.open("a") as output:
            output.write(json.dumps({"runs": depth, "run_id": source["id"], "complete_ms": elapsed_ms, **probe}) + "\n")
        previous = probe
        progress.update(
            completed_runs=depth, compactions=probe["compactions"], elapsed_seconds=perf_counter() - started
        )
        progress_path.write_text(json.dumps(progress))
        if depth % 50 == 0 or depth in checkpoints:
            logger.info(
                "Chain progress: runs=%s/%s elapsed_seconds=%.1f state_bytes=%s messages=%s compactions=%s",
                depth,
                checkpoints[-1],
                perf_counter() - started,
                probe["state_bytes"],
                probe["messages"],
                probe["compactions"],
            )
        if depth in checkpoints:
            if depth >= 1000:
                assert probe["compactions"] > 0, "Long chains must exercise real compaction"
            await measure_checkpoint(lab, source, depth, repetitions, probe)
    progress.update(status="passed", elapsed_seconds=perf_counter() - started)
    progress_path.write_text(json.dumps(progress))


async def measure_checkpoint(lab, source, depth, repetitions, baseline):
    live = lab.client
    results = Measurements(
        lab.root / f"latency-{depth}.json",
        {
            "real_sequential_runs": depth,
            "message_padding_bytes": lab.config["long_session"]["message_bytes"],
            "context_window": lab.config["long_session"]["context_window"],
            "compaction_threshold_ratio": 0.9,
            "baseline": baseline,
            "repetitions": repetitions,
            "platform": platform.platform(),
            "python": platform.python_version(),
            "worker_lease_seconds": float(lab.worker_environment["A13N_SERVICE_WORKER_LEASE_SECONDS"]),
            "source_run_id": source["id"],
            "source_thread_id": source["thread_id"],
            "status": "incomplete",
            "notes": "Loopback PG/Redis/RustFS; size-aware deterministic model (JSON UTF-8 bytes / 4); real built-in compaction; one warmup; completion polling 100 ms. Acceptance excludes Thread version pre-read; completion includes it. Run lists use 200 entries per page.",
        },
    )
    results.save()
    logger.info("Long-session report: %s", results.path)
    page_path = f"/api/v1/threads/{source['thread_id']}/runs"
    # A complete warmup journey is excluded, including its storage reads.
    for sample in range(repetitions + 1):
        results.enabled = sample > 0
        probe = await live.request("GET", f"/__live__/performance/runs/{source['id']}")
        assert probe["sequence"] == depth and probe["memories"] == baseline["memories"]
        for name, value in probe["timings_ms"].items():
            results.add(name, value)
        results.metadata["state_bytes"] = probe["state_bytes"]
        await results.measure("run_get", lambda: live.run(source["id"]))
        page = await results.measure("runs_first_page", lambda: live.request("GET", page_path, params={"limit": 200}))
        assert page["items"]
        runs = await results.measure("runs_all_pages", lambda: run_ids(live, page_path, depth))
        assert len(runs) == depth

        case = await live.case("basic")
        started = perf_counter()
        fork = await accept(live, source, "fork", case, results=results)
        forked = await live.finish(fork["run_id"])
        results.add("fork_complete", (perf_counter() - started) * 1000)
        assert forked["parent_run_id"] == source["id"] and forked["thread_id"] != source["thread_id"]
        assert forked["session_id"] == source["session_id"] and forked["output_text"].startswith(case["token"] + "\n")

        started = perf_counter()
        receipt = await accept(live, forked, "continue", case, results=results)
        continued = await live.finish(receipt["run_id"])
        results.add("continue_complete", (perf_counter() - started) * 1000)
        assert continued["parent_run_id"] == forked["id"] and continued["output_text"].startswith(case["token"] + "\n")

        failing = await live.case("model_error")
        receipt = await accept(live, continued, "continue", failing)
        failed = await live.finish(receipt["run_id"], "failed")
        await live.release(failing)
        started = perf_counter()
        receipt = await accept(live, failed, "retry", results=results)
        retried = await live.finish(receipt["run_id"])
        results.add("retry_complete", (perf_counter() - started) * 1000)
        assert retried["retry_of_run_id"] == failed["id"] and retried["output_text"].startswith(failing["token"] + "\n")
        assert retried["parent_run_id"] == continued["id"] and retried["input"] == failed["input"]

        gate = await live.case("gate")
        receipt = await accept(live, retried, "continue", gate)
        run_id = receipt["run_id"]
        await lab.wait_evidence(gate, "gate_ready", run_id=run_id)
        queued_case = await live.case("basic")
        thread = await live.thread(receipt["thread_id"])
        queued = await results.measure(
            "queue_accept",
            lambda thread=thread, queued_case=queued_case: live.request(
                "POST",
                f"/api/v1/threads/{thread['id']}/runs",
                expected=202,
                headers={"Idempotency-Key": uuid4().hex},
                json={"expected_thread_version": thread["version"], "input": session_input(live, queued_case)},
            ),
        )
        assert queued["outcome"] == "queued" and queued["run"] is None
        steer = await results.measure(
            "steer_accept",
            lambda run_id=run_id: live.request(
                "POST",
                f"/api/v1/runs/{run_id}/steer",
                expected=202,
                headers={"Idempotency-Key": uuid4().hex},
                json=agent_input("Apply this benchmark steer."),
            ),
        )
        started = perf_counter()
        await live.release(gate)
        status = await live.wait(
            lambda run_id=run_id, steer=steer: live.request("GET", f"/api/v1/runs/{run_id}/steers/{steer['steer_id']}"),
            lambda value: value["status"] != "pending",
            "steer consumption",
        )
        results.add("steer_release_to_consumed", (perf_counter() - started) * 1000)
        assert status["status"] == "consumed" and status["consumed_by_run_id"] == run_id
        assert status["consumed_state_digest_sha256"]
        await live.finish(run_id)
        submission = queued["queued_submission"]["queued_submission_id"]
        consumed = await live.wait(
            lambda submission=submission: live.request("GET", f"/api/v1/queued-submissions/{submission}"),
            lambda value: value["state"] != "queued",
            "queued successor",
        )
        results.add("queue_release_to_consumed", (perf_counter() - started) * 1000)
        assert consumed["state"] == "consumed" and consumed["consumed_run_id"]
        live.runs.append(consumed["consumed_run_id"])
        successor = await live.finish(consumed["consumed_run_id"])
        results.add("queue_release_to_complete", (perf_counter() - started) * 1000)
        assert successor["parent_run_id"] == run_id and successor["output_text"].startswith(queued_case["token"] + "\n")
        restored = await live.request("GET", f"/__live__/performance/runs/{successor['id']}")
        assert restored["memories"] == baseline["memories"] and restored["sequence"] == depth
        assert restored["compactions"] >= probe["compactions"]
        assert await live.run(source["id"]) == source
        logger.info(
            "Long session: runs=%s state_bytes=%s sample=%s/%s completed",
            depth,
            probe["state_bytes"],
            sample,
            repetitions,
        )
    results.metadata["status"] = "passed"
    for name, values in results.save().items():
        logger.info("Latency: runs=%s operation=%s %s", depth, name, values)
