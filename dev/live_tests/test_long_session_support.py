"""Offline checks of benchmark data fidelity and reported sample semantics."""

import json

import pytest

from .long_session_model import history_evidence, response_content
from .test_36_long_session import Measurements, accept, run_ids


@pytest.mark.parametrize("padding", [32, 1024, 2048])
def test_model_usage_grows_with_real_request_size_and_summary_retains_memory(padding):
    memory = "a" * 32
    body = {"messages": [{"role": "user", "content": f"LONG_SESSION_MEMORY {memory}\nLONG_SESSION_RUN 1"}]}
    _, _, initial = response_content(body, "b" * 32, padding)
    for sequence in range(2, 51):
        body["messages"].append({"role": "user", "content": f"LONG_SESSION_RUN {sequence}\n" + "x" * padding})
    _, compact, grown = response_content(body, "b" * 32, padding)
    assert not compact and grown["prompt_tokens"] > initial["prompt_tokens"]
    body["messages"].append(
        {"role": "user", "content": "Generate a compact continuation summary for the conversation history."}
    )
    answer, compact, _ = response_content(body, "b" * 32, padding)
    assert compact and history_evidence(answer) == {"memories": [memory], "sequence": 50, "compactions": 1}
    body["messages"] = [{"role": "assistant", "content": answer}, {"role": "user", "content": "LONG_SESSION_RUN 51"}]
    _, compact, reduced = response_content(body, "b" * 32, padding)
    assert not compact and reduced["prompt_tokens"] < grown["prompt_tokens"]


def test_model_rejects_missing_original_memory():
    with pytest.raises(ValueError, match="initial Run's memory"):
        response_content({"messages": [{"role": "user", "content": "LONG_SESSION_RUN 1000"}]}, "b" * 32, 1024)


def test_compaction_directive_before_the_trailing_runtime_context():
    memory = "a" * 32
    body = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"LONG_SESSION_MEMORY {memory}\nLONG_SESSION_RUN 44"},
                    {"type": "text", "text": "Generate a compact continuation summary for the conversation history."},
                ],
            },
            {"role": "user", "content": [{"type": "text", "text": "<agent-context>Current runtime</agent-context>"}]},
        ]
    }
    answer, compact, _ = response_content(body, "b" * 32, 1024)
    assert compact and history_evidence(answer)["memories"] == [memory]


@pytest.mark.anyio
async def test_acceptance_timer_excludes_thread_version_read():
    events = []

    class Client:
        async def thread(self, thread_id):
            events.append("version")
            return {"version": 7}

        async def request(self, method, path, **kwargs):
            assert kwargs["json"] == {"expected_thread_version": 7}
            events.append("post")
            return {"run_id": "next"}

        def track(self, receipt):
            events.append("track")

    class Timer:
        async def measure(self, name, operation):
            events.append("timer_start")
            receipt = await operation()
            events.append("timer_end")
            return receipt

    await accept(Client(), {"id": "failed", "thread_id": "thread"}, "retry", results=Timer())
    assert events == ["version", "timer_start", "post", "timer_end", "track"]


@pytest.mark.anyio
async def test_run_listing_exceeds_the_old_five_thousand_run_limit():
    class Client:
        async def request(self, method, path, *, params):
            assert params["limit"] == 200
            start = int(params.get("cursor", 0))
            end = min(start + 200, 10000)
            return {
                "items": [{"id": str(index)} for index in range(start, end)],
                "next_cursor": str(end) if end < 10000 else None,
            }

    assert len(await run_ids(Client(), "/runs", 10000)) == 10000


@pytest.mark.anyio
async def test_measurements_exclude_warmup_and_failed_operations(tmp_path):
    report = Measurements(tmp_path / "latency.json", {"status": "incomplete"})
    report.add("read", 1000)
    report.enabled = True
    for value in range(1, 21):
        report.add("read", value)
    assert report.save()["read"] == {"n": 20, "p50_ms": 10.5, "p95_ms": 19, "max_ms": 20}

    async def failure():
        raise RuntimeError("injected")

    with pytest.raises(RuntimeError, match="injected"):
        await report.measure("failed", failure)
    data = json.loads(report.path.read_text())
    assert data["samples_ms"] == {"read": list(range(1, 21))}
    assert data["status"] == "incomplete"
